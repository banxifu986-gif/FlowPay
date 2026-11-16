"""日志来源的归属反查：没有账户列的台账也不能跨商户查（specs/005 F1）。

投递尝试日志没有 account_id 字段；core 按 sources.toml 的 ownership 声明，用投递表
经 events 反查出归属，再与主体绑定值比较。反查是登记关系驱动的通用机制，不是业务特判。
"""

import json
import sqlite3
from pathlib import Path

import pytest

from flowpay.core.auth import Principal
from flowpay.core.config import Settings
from flowpay.core.data_sources import DataSources, digest
from flowpay.core.data_sources.reader import SourceResult
from flowpay.core.data_sources.tools import LogToolSpec, log_tool
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.stage import StageCard
from flowpay.core.json import JsonObject, require_object
from tests.core.principals import INTERNAL, MERCHANT


@pytest.fixture
def sources(tmp_path: Path) -> DataSources:
    """两账户各一条投递及尝试记录；归属只能经 events 反查得到。"""
    database = tmp_path / "flowpay.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE events(event_id TEXT, account_id TEXT, environment TEXT)")
        db.execute(
            "CREATE TABLE webhook_deliveries(delivery_id TEXT, event_id TEXT, endpoint_id TEXT)"
        )
        db.execute("INSERT INTO events VALUES('evt_a', ?, 'production')", (MERCHANT.account_id,))
        db.execute("INSERT INTO events VALUES('evt_b', 'acct_other', 'production')")
        db.execute("INSERT INTO webhook_deliveries VALUES('del_a', 'evt_a', 'we_a')")
        db.execute("INSERT INTO webhook_deliveries VALUES('del_b', 'evt_b', 'we_b')")
    log = tmp_path / "webhook_delivery_attempts.jsonl"
    log.write_text(
        "\n".join(
            json.dumps({"delivery_id": delivery, "attempt_number": 1, "http_status": 200})
            for delivery in ("del_a", "del_b")
        )
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
            "webhook_delivery_ownership": {
                "path": database.name,
                "relation": "webhook_deliveries d JOIN events e ON e.event_id = d.event_id",
                "scope": {},
                "filters": {
                    "delivery_id": "d.delivery_id",
                    "event_id": "d.event_id",
                    "endpoint_id": "d.endpoint_id",
                },
                "select": ["d.delivery_id", "e.account_id", "e.environment"],
                "order_by": "d.delivery_id",
                "columns": {
                    "webhook_deliveries": ["delivery_id", "event_id", "endpoint_id"],
                    "events": ["event_id", "account_id", "environment"],
                },
            }
        },
        "logs": {
            "delivery_attempts": {
                "path": log.name,
                "format": "jsonl",
                "filter_fields": ["delivery_id", "event_id", "endpoint_id"],
                "result_fields": ["delivery_id", "attempt_number", "http_status"],
                "ownership": {
                    "source": "webhook_delivery_ownership",
                    "scope": ["account_id", "environment"],
                },
            }
        },
    }
    return DataSources(tmp_path, policy)


class LogOwnershipWorkflow:
    """按「对话给出」的两个投递编号各查一次投递尝试日志。"""

    id: str = "log-ownership"
    version: str = "1"
    card: StageCard = StageCard(label="logs", description="日志归属回归")

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        del turn

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
                log_tool(
                    LogToolSpec(
                        "list_delivery_attempts", "", "delivery_attempts", ("delivery_id",)
                    ),
                    run_logs,
                    {},
                    runtime.scope_overrides,
                )
            ]
        )
        rows: JsonObject = {}
        for delivery in ("del_a", "del_b"):
            result = await toolbox.execute(
                "list_delivery_attempts", {"delivery_id": delivery}, f"call_{delivery}"
            )
            rows[delivery] = result["rows"]
        return TurnResult(text="logs", saved_state={"attempts": rows})


async def run_query(
    tmp_path: Path, sources: DataSources, principal: Principal, suffix: str
) -> tuple[JsonObject, list[dict[str, object]]]:
    """按主体跑一轮，返回检查点与事件流；会话与资源在用例内关闭。"""
    settings = Settings(tmp_path, tmp_path)
    manager = SessionManager(
        EventStore(tmp_path / f"logs-{suffix}.sqlite"),
        LogOwnershipWorkflow(),
        settings,
        RuntimeResources(settings, sources=sources),
    )
    sid = (await manager.create_session(principal))["id"]
    await manager.submit_message(sid, "one", "del_a del_b", principal)
    await manager.tasks[sid]
    state = (await manager.get_session(sid, principal))["state"]
    events = manager.store.read(sid)
    await manager.shutdown()
    return state, [dict(event) for event in events]


async def test_merchant_cannot_read_other_delivery_attempts(
    tmp_path: Path, sources: DataSources
) -> None:
    """他人的 delivery_id 返回空行；自己的有行，且事件记录了解析后的账户。"""
    state, events = await run_query(tmp_path, sources, MERCHANT, "merchant")
    attempts = require_object(state["attempts"])
    assert attempts["del_a"]
    assert attempts["del_b"] == []

    requests = [
        require_object(event["payload"]) for event in events if event["type"] == "source.request"
    ]
    denied = [
        require_object(request["parameters"])
        for request in requests
        if require_object(request["parameters"]).get("delivery_id") == "del_b"
    ]
    assert denied
    assert denied[0]["account_id"] == MERCHANT.account_id
    # 拒绝路径不能把命中行的真实 owner 带进商户可读的事件。
    assert "acct_other" not in str(events)


async def test_internal_account_sees_all_delivery_attempts(
    tmp_path: Path, sources: DataSources
) -> None:
    """内部账号没有绑定账户：不做反查，行为与 005 之前一致。"""
    state, _ = await run_query(tmp_path, sources, INTERNAL, "internal")
    attempts = require_object(state["attempts"])
    assert attempts["del_a"] and attempts["del_b"]


async def test_unknown_delivery_id_is_indistinguishable_from_foreign(
    tmp_path: Path, sources: DataSources
) -> None:
    """不存在的编号与别人的编号都返回空行：不泄漏存在性。"""
    state, _ = await run_query(tmp_path, sources, MERCHANT, "unknown")
    attempts = require_object(state["attempts"])
    assert attempts["del_b"] == []
