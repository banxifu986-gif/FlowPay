"""跨会话记忆的阶段注入与收尾抽取：opt-in 生效、缺省关闭、失败不阻断（specs/004）。

multi-agent 用例不依赖业务数据集（Search 夹具即可）；manual-loop 用例与
test_manual_loop 同口径依赖数据集快照。
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from flowpay.core.config import Settings
from flowpay.core.data_sources import DataSources
from flowpay.core.event_log import EventStore
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.core.memory.contracts import MemoryNote, MemoryPolicy
from flowpay.core.memory.store import MemoryStore
from flowpay.dataset import dataset_path, snapshot_ready
from flowpay.router import create_stage
from flowpay.workflows.manual_loop import MEMORY_SECTION_HEADER

from .fixtures import Model, Search, completion, conversation, grounded

ROOT = Path(__file__).resolve().parents[3]
DATASET = dataset_path() or Path("/nonexistent")
POLICY = ROOT / "backend/policies/sources.toml"

QUESTION = "支付 pay_bg_001_0003 一直没收到通知，帮我看看"

# manual-loop 用例与 test_manual_loop 同口径：需要业务数据集快照在位。
needs_snapshot = pytest.mark.skipif(
    not snapshot_ready(), reason="business dataset snapshot is not available"
)


def make_policy(budget: int = 2000) -> MemoryPolicy:
    """键召回口径的策略；语义兜底不在本文件覆盖（见 tests/memory）。"""
    return MemoryPolicy(recall_char_budget=budget)


def seed_note(tmp_path: Path) -> MemoryStore:
    """预置一条键命中测试消息的记忆库。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(
        MemoryNote(
            note_id="note_seed",
            session_id="session_seed",
            workflow="multi-agent",
            created_at="2026-11-15T09:00:00+00:00",
            keys=["pay_bg_001_0003"],
            conditions={"known": {}},
            body="此前结论：投递被 410 拒绝，端点随后停用。",
            embedding=None,
            body_chars=20,
        )
    )
    return store


def make_multi_manager(
    tmp_path: Path,
    model: Model,
    search: Search,
    memory_policy: MemoryPolicy | None,
    memory_store: MemoryStore | None = None,
) -> tuple[SessionManager, RuntimeResources]:
    """multi-agent 装配：记忆按参数启停，检索用夹具，不依赖数据集。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(
        settings,
        retriever=search.for_run,
        memory_store=memory_store,
        memory_policy=memory_policy,
    )
    workflow = create_stage("multi-agent", model, retrieval_input, None, memory_policy)
    manager = SessionManager(EventStore(tmp_path / "events.sqlite"), workflow, settings, resources)
    return manager, resources


async def run_turn(manager: SessionManager, sid: str, request_id: str, text: str) -> None:
    """提交一轮消息并等待结束。"""
    await manager.submit_message(sid, request_id, text)
    await manager.tasks[sid]


def events_of(manager: SessionManager, sid: str) -> list[tuple[str, JsonObject]]:
    """读回会话事件流，返回 (type, payload) 列表。"""
    return [(row["type"], row["payload"]) for row in manager.store.read(sid)]


def request_texts(request: JsonObject) -> str:
    """拼接一次模型请求里全部消息正文（记忆段可能进 system 或 user 消息）。"""
    texts: list[str] = []
    for message in require_list(request["messages"]):
        record = require_object(message)
        content = record.get("content")
        if isinstance(content, str):
            texts.append(content)
    return "\n".join(texts)


async def test_multi_agent_injects_memory_into_case_note(tmp_path: Path) -> None:
    """键命中：记忆段进 case_note（plan 请求可见），事件 matched=key。"""
    model, search = Model(), Search()
    store = seed_note(tmp_path)
    manager, resources = make_multi_manager(tmp_path, model, search, make_policy(), store)
    model.responses = [conversation("没有需要调查的任务。"), grounded("排查分支的回答。")]
    try:
        sid = require_text((await manager.create_session())["id"])
        await run_turn(manager, sid, "first", QUESTION)
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "completed"
        injected = request_texts(model.requests[0])
        assert MEMORY_SECTION_HEADER in injected
        assert "此前结论：投递被 410 拒绝" in injected
        assert "一律以本轮为准" in injected
        recalled = [
            payload for kind, payload in events_of(manager, sid) if kind == "memory.recalled"
        ]
        assert len(recalled) == 1
        assert require_object(recalled[0])["matched"] == "key"
    finally:
        await manager.shutdown()
        resources.close()


async def test_multi_agent_without_memory_keeps_requests_clean(tmp_path: Path) -> None:
    """缺省关闭：不建记忆库、不发记忆事件、请求里没有记忆段落。"""
    model, search = Model(), Search()
    manager, resources = make_multi_manager(tmp_path, model, search, None)
    model.responses = [conversation("没有需要调查的任务。"), grounded("排查分支的回答。")]
    try:
        sid = require_text((await manager.create_session())["id"])
        await run_turn(manager, sid, "first", QUESTION)
        for request in model.requests:
            assert MEMORY_SECTION_HEADER not in request_texts(request)
        kinds = [kind for kind, _ in events_of(manager, sid)]
        assert not any(kind.startswith("memory.") for kind in kinds)
        assert not (tmp_path / "memory.sqlite").exists()
    finally:
        await manager.shutdown()
        resources.close()


async def test_multi_agent_key_miss_emits_none_event(tmp_path: Path) -> None:
    """键未命中：不发记忆段，事件 matched=none，本轮照常完成。"""
    model, search = Model(), Search()
    store = seed_note(tmp_path)
    manager, resources = make_multi_manager(tmp_path, model, search, make_policy(), store)
    model.responses = [conversation("没有需要调查的任务。"), grounded("排查分支的回答。")]
    try:
        sid = require_text((await manager.create_session())["id"])
        await run_turn(manager, sid, "first", "另一个账户 acct_other_9 的问题")
        for request in model.requests:
            assert MEMORY_SECTION_HEADER not in request_texts(request)
        recalled = [
            payload for kind, payload in events_of(manager, sid) if kind == "memory.recalled"
        ]
        assert require_object(recalled[0])["matched"] == "none"
    finally:
        await manager.shutdown()
        resources.close()


def ask_call(identifier: str, missing: list[str], question: str) -> JsonObject:
    """构造一次 ask_user 调用（协议要求 arguments 为 JSON 字符串）。"""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "ask_user",
            "arguments": json.dumps({"missing": missing, "question": question}),
        },
    }


def search_document(identifier: str, query: str) -> JsonObject:
    """构造一次 search_documents 调用。"""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "search_documents",
            "arguments": json.dumps({"query": query, "limit": 10}),
        },
    }


@pytest.fixture
async def manual_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[SessionManager, Model, MemoryStore]]:
    """manual-loop 装配：记忆开启，业务源用真实数据集，检索用夹具。

    manual-loop 启动即装配 SQL 工具取值封闭字段，因此必须依赖数据集快照。
    """
    model = Model()
    search = Search()
    settings = Settings(
        tmp_path, tmp_path, workflow="manual-loop", knowledge_collection_id="fixture"
    )
    store = seed_note(tmp_path)
    resources = RuntimeResources(
        settings,
        sources=DataSources.from_config(POLICY, DATASET),
        retriever=search.for_run,
        memory_store=store,
        memory_policy=make_policy(),
    )
    workflow = create_stage("manual-loop", model, retrieval_input, None, make_policy())
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), workflow, settings, resources
    )
    try:
        yield manager, model, store
    finally:
        await manager.shutdown()
        resources.close()


@needs_snapshot
async def test_manual_loop_injects_and_extracts(
    manual_runtime: tuple[SessionManager, Model, MemoryStore],
) -> None:
    """manual-loop：记忆段注入简报；完成轮带查询动作时抽取新记忆落库。"""
    manager, model, store = manual_runtime
    model.responses = [
        completion(calls=[search_document("call_1", "webhook 重试策略")]),
        completion("上一轮排查过投递失败问题，本轮先看文档重试策略。"),
    ]
    sid = require_text((await manager.create_session())["id"])
    await run_turn(manager, sid, "first", QUESTION)
    view = await manager.get_session(sid)
    assert require_text(view["status"]) == "completed"
    injected = request_texts(model.requests[0])
    assert MEMORY_SECTION_HEADER in injected
    extracted = [payload for kind, payload in events_of(manager, sid) if kind == "memory.extracted"]
    assert len(extracted) == 1
    assert require_object(extracted[0])["keys"] == 1
    # 新结论与种子键集完全相同 → 同键替换：库里仍一条，正文换成本轮抽取的。
    hits = store.match_keys(["pay_bg_001_0003"], limit=5)
    assert len(hits) == 1
    assert "最终回答：" in hits[0].body


@needs_snapshot
async def test_manual_loop_waiting_user_skips_extraction(
    manual_runtime: tuple[SessionManager, Model, MemoryStore],
) -> None:
    """追问结束的轮次没有结论可存：发 extract_skipped(waiting_user)。"""
    manager, model, _ = manual_runtime
    model.responses = [
        completion(calls=[ask_call("ask_1", ["environment"], "是生产还是沙箱环境？")])
    ]
    sid = require_text((await manager.create_session())["id"])
    await run_turn(manager, sid, "first", QUESTION)
    view = await manager.get_session(sid)
    assert require_text(view["status"]) == "waiting_user"
    skipped = [
        payload for kind, payload in events_of(manager, sid) if kind == "memory.extract_skipped"
    ]
    assert require_object(skipped[0])["reason"] == "waiting_user"


@needs_snapshot
async def test_manual_loop_no_findings_skips_extraction(
    manual_runtime: tuple[SessionManager, Model, MemoryStore],
) -> None:
    """无查询动作的完成轮：发 extract_skipped(no_findings)，记忆库不变。"""
    manager, model, store = manual_runtime
    model.responses = [completion("直接回答，没有任何查询。")]
    sid = require_text((await manager.create_session())["id"])
    await run_turn(manager, sid, "first", QUESTION)
    view = await manager.get_session(sid)
    assert require_text(view["status"]) == "completed"
    skipped = [
        payload for kind, payload in events_of(manager, sid) if kind == "memory.extract_skipped"
    ]
    assert require_object(skipped[0])["reason"] == "no_findings"
    assert store.match_keys(["pay_bg_001_0003"], limit=5)[0].body.startswith("此前结论")
