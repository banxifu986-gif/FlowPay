"""商户数据边界回归：scope 在工具边界被服务端覆盖（specs/005 US2）。

固定输入只用于复现边界：A 会话拿 B 的单号去查，结果必须是空行，而不是 B 的支付。
"""

import json
import sqlite3
from pathlib import Path

import pytest
from typing_extensions import TypedDict

from flowpay.core.auth import Principal
from flowpay.core.config import Settings
from flowpay.core.data_sources import DataSources, digest
from flowpay.core.data_sources.reader import SourceResult
from flowpay.core.data_sources.tools import LogToolSpec, SqlToolSpec, log_tool, sql_tool
from flowpay.core.event_log import EventStore
from flowpay.core.event_log.types import Event
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.stage import StageCard
from flowpay.core.json import JsonObject, require_object, require_text
from flowpay.core.knowledge.contracts import Passage
from tests.core.principals import INTERNAL, MERCHANT

# 会话里对话值永远是 B 的账户：商户会话必须被覆盖，内部账号照旧生效。
CONVERSATION_ACCOUNT = "acct_b"
CONVERSATION_PAYMENT = "pay_b_1"


@pytest.fixture
def sources(tmp_path: Path) -> DataSources:
    """两账户一付款的只读快照；策略与 manifest 按真实登记格式生成。"""
    database = tmp_path / "flowpay.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE payments(payment_id TEXT, account_id TEXT, environment TEXT)")
        db.execute(
            "INSERT INTO payments VALUES('pay_a_1', ?, 'production')", (MERCHANT.account_id,)
        )
        db.execute("INSERT INTO payments VALUES('pay_b_1','acct_b','production')")
    log = tmp_path / "api_requests.jsonl"
    log.write_text(
        json.dumps({"account_id": MERCHANT.account_id, "request_id": "req_a", "http_status": 200})
        + "\n"
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"files": {p.name: {"sha256": digest(p)} for p in (database, log)}})
    )
    policy = {
        "release_id": "test",
        "manifest": "manifest.json",
        "manifest_sha256": digest(manifest),
        "sql": {
            "payments": {
                "path": database.name,
                "relation": "payments",
                "scope": {"account_id": "account_id", "environment": "environment"},
                "filters": {"payment_id": "payment_id"},
                "select": ["payment_id", "account_id"],
                "order_by": "payment_id",
                "columns": {"payments": ["payment_id", "account_id", "environment"]},
                "values": {"environment": ["production", "sandbox"]},
            }
        },
        "logs": {
            "api_requests": {
                "path": log.name,
                "format": "jsonl",
                "filter_fields": ["account_id", "request_id"],
                "result_fields": ["account_id", "request_id", "http_status"],
            }
        },
    }
    return DataSources(tmp_path, policy)


class BoundaryWorkflow:
    """用真实工具边界查一笔 B 的支付；对话值固定为 B 的账户。"""

    id: str = "boundary"
    version: str = "1"
    card: StageCard = StageCard(label="boundary", description="scope 覆盖回归")

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        """工具参数由「对话」给出，能否查到 B 的记录完全取决于服务端覆盖。"""
        del turn
        scopes: list[JsonObject] = []

        async def run_sql(source: str, scope: JsonObject, filters: JsonObject) -> SourceResult:
            scopes.append(scope)
            return await runtime.query_sql(source, scope, filters)

        async def run_logs(
            source: str,
            *,
            filters: JsonObject | None = None,
            start: str | None = None,
            end: str | None = None,
        ) -> SourceResult:
            return await runtime.query_logs(source, filters=filters, start=start, end=end)

        toolbox = runtime.toolbox(
            [
                sql_tool(
                    SqlToolSpec(
                        "query_payments",
                        "",
                        "payments",
                        ("account_id", "environment"),
                        ("payment_id",),
                    ),
                    run_sql,
                    {},
                    runtime.scope_overrides,
                ),
                log_tool(
                    LogToolSpec(
                        "get_api_request", "", "api_requests", ("account_id", "request_id")
                    ),
                    run_logs,
                    {},
                    runtime.scope_overrides,
                ),
            ]
        )
        sql_result = await toolbox.execute(
            "query_payments",
            {
                "account_id": CONVERSATION_ACCOUNT,
                "environment": "production",
                "payment_id": CONVERSATION_PAYMENT,
            },
            "call_sql",
        )
        log_result = await toolbox.execute(
            "get_api_request",
            {"account_id": CONVERSATION_ACCOUNT, "request_id": "req_a"},
            "call_logs",
        )
        return TurnResult(
            text="boundary",
            saved_state={
                "rows": sql_result["rows"],
                "logs": log_result["rows"],
                "scopes": scopes,
            },
        )


class BoundaryOutcome(TypedDict):
    """一轮的检查点与事件流；事件流用于断言实际执行的 scope。"""

    state: JsonObject
    events: list[Event]


async def play(tmp_path: Path, sources: DataSources, principal: Principal) -> BoundaryOutcome:
    """按主体跑一轮，返回检查点与来源记录；会话与资源在用例内关闭。"""
    settings = Settings(tmp_path, tmp_path)
    manager = SessionManager(
        EventStore(tmp_path / f"{principal.username}.sqlite"),
        BoundaryWorkflow(),
        settings,
        RuntimeResources(settings, sources=sources),
    )
    sid = require_text((await manager.create_session(principal))["id"])
    await manager.submit_message(sid, "one", "acct_b production pay_b_1", principal)
    await manager.tasks[sid]
    state = dict((await manager.get_session(sid, principal))["state"])
    events = manager.store.read(sid)
    await manager.shutdown()
    return {"state": state, "events": events}


async def test_merchant_scope_is_injected_and_hides_other_account(
    tmp_path: Path, sources: DataSources
) -> None:
    """商户会话拿 B 的单号查不到行；事件里记录的是覆盖后的 A 账户。"""
    outcome = await play(tmp_path, sources, MERCHANT)
    state = outcome["state"]
    assert state["rows"] == []
    assert state["logs"] == [
        {"account_id": MERCHANT.account_id, "request_id": "req_a", "http_status": 200}
    ]
    requests = [
        require_object(e["payload"]) for e in outcome["events"] if e["type"] == "source.request"
    ]
    assert requests
    assert require_object(requests[0]["parameters"])["account_id"] == MERCHANT.account_id


async def test_internal_scope_still_comes_from_conversation(
    tmp_path: Path, sources: DataSources
) -> None:
    """内部账号不注入 scope：对话给的 B 账户照旧生效（现状保留）。"""
    outcome = await play(tmp_path, sources, INTERNAL)
    state = outcome["state"]
    assert state["rows"] == [{"payment_id": "pay_b_1", "account_id": CONVERSATION_ACCOUNT}]
    assert state["logs"] == []


async def test_tool_wrapper_overrides_only_declared_scope_columns() -> None:
    """覆盖只作用于登记 scope 列；environment 等对话条件原样传给查询。"""
    received: list[tuple[str, JsonObject, JsonObject]] = []

    async def query(source: str, scope: JsonObject, filters: JsonObject) -> SourceResult:
        received.append((source, scope, filters))
        return {
            "rows": [],
            "truncated": False,
            "source": {"path": "x", "sha256": ""},
            "version": {
                "dataset_release_id": "t",
                "dataset_manifest_sha256": "",
                "source_policy_sha256": "",
                "files": {},
            },
            "query": {"operation": source, "parameters": scope},
        }

    tool = sql_tool(
        SqlToolSpec("q", "", "payments", ("account_id", "environment"), ("payment_id",)),
        query,
        {},
        {"account_id": "acct_a"},
    )
    result = await tool.execute(
        {"account_id": "acct_b", "environment": "sandbox", "payment_id": "pay_b_1"}
    )
    assert result["rows"] == []
    assert received[0][1] == {"account_id": "acct_a", "environment": "sandbox"}
    assert received[0][2] == {"payment_id": "pay_b_1"}


class RecordingRetriever:
    """记录检索与整篇读回下发的过滤条件；不接真实向量库。"""

    def __init__(self, calls: list[tuple[str, JsonObject | None]]) -> None:
        self.calls: list[tuple[str, JsonObject | None]] = calls

    async def search(
        self,
        collection_id: str,
        text: str,
        limit: int = 5,
        filters: JsonObject | None = None,
    ) -> list[Passage]:
        del collection_id, text, limit
        self.calls.append(("search", filters))
        return []

    async def read_document(
        self, collection_id: str, doc_id: str, filters: JsonObject | None = None
    ) -> list[Passage]:
        del collection_id, doc_id
        self.calls.append(("read", filters))
        return []


class DocumentWorkflow:
    """只走文档能力的一轮：检索一次、整篇读回一次。"""

    id: str = "documents"
    version: str = "1"
    card: StageCard = StageCard(label="documents", description="文档可见范围回归")

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        del turn
        hits = await runtime.search_knowledge("webhook 重试")
        loaded = await runtime.read_document("KB-BR-015")
        return TurnResult(
            text="documents",
            saved_state={"hits": len(hits), "loaded": len(loaded)},
        )


async def test_document_audience_follows_role(tmp_path: Path) -> None:
    """商户的检索与读回都带 external 过滤；内部不过滤（specs/005 US3）。"""
    calls: list[tuple[str, JsonObject | None]] = []
    settings = Settings(tmp_path, tmp_path, knowledge_collection_id="docs")
    resources = RuntimeResources(settings, retriever=lambda tracer: RecordingRetriever(calls))
    manager = SessionManager(
        EventStore(tmp_path / "documents.sqlite"), DocumentWorkflow(), settings, resources
    )
    try:
        for principal in (MERCHANT, INTERNAL):
            sid = require_text((await manager.create_session(principal))["id"])
            await manager.submit_message(sid, "one", "webhook 重试", principal)
            await manager.tasks[sid]
    finally:
        await manager.shutdown()
    assert calls == [
        ("search", {"audience": "external"}),
        ("read", {"audience": "external"}),
        ("search", None),
        ("read", None),
    ]
