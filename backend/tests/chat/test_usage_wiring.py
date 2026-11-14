"""用量从模型响应到前端消息的端到端契约（T012）。

multi-agent 轻路径（意图分类、文档问答、文档调查员）不需要业务数据源，
在本环境即可全链路驱动；manual-loop 全链路被数据集门控，不在此覆盖。
"""

from pathlib import Path

from openai.types.chat import ChatCompletion

from flowpay.core.chat.session_view import build_session_view
from flowpay.core.chat.types import SessionView
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.event_log.types import Event
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.json import JsonObject
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.router import create_stage

from ..workflows.fixtures import Model, Search, completion, intent_call, tasks_call

USAGE_A: JsonObject = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
USAGE_B: JsonObject = {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}
USAGE_C: JsonObject = {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34}
USAGE_D: JsonObject = {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45}
USAGE_E: JsonObject = {"prompt_tokens": 50, "completion_tokens": 6, "total_tokens": 56}


def build_manager(
    tmp_path: Path, model: Model, search: Search
) -> tuple[SessionManager, RuntimeResources]:
    """与 test_multi_agent 同一装配：真实事件库 + 受控模型与检索。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, retriever=search.for_run)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"),
        create_stage("multi-agent", model, retrieval_input),
        settings,
        resources,
    )
    return manager, resources


async def submit_and_view(manager: SessionManager, message: str) -> SessionView:
    """提交一条消息并等待本轮结束，返回会话视图。"""
    sid = (await manager.create_session())["id"]
    await manager.submit_message(sid, "first", message)
    await manager.tasks[sid]
    view = await manager.get_session(sid)
    assert view["status"] == "completed"
    return view


def event(seq: int, kind: str, payload: JsonObject, run_id: str | None = None) -> Event:
    """手工构造一条最小事件；schema 其余字段取协议占位值。"""
    return Event(
        schema_version=1,
        event_id=f"e{seq}",
        session_id="s1",
        seq=seq,
        run_id=run_id,
        call_id=None,
        parent_call_id=None,
        timestamp="2026-11-14T00:00:00",
        type=kind,
        payload=payload,
    )


def session_created() -> Event:
    """会话首事件；workflow 身份与既有阶段一致。"""
    return event(
        1,
        "session.created",
        {"workflow": {"id": "flowpay-multi-agent-support", "version": "1"}},
    )


async def test_answer_turn_carries_usage(tmp_path: Path) -> None:
    """文档问答一轮（分类 + 作答两次直连调用）：回答消息携带逐位求和的用量。"""
    model, search = Model(), Search()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "docs")], usage=USAGE_A),
        completion("按文档回答。", usage=USAGE_B),
    ]
    model.responses = responses
    manager, resources = build_manager(tmp_path, model, search)
    try:
        view = await submit_and_view(manager, "Webhook 重试策略是什么？")
        usage = view["messages"][-1]["usage"]
        assert usage == {
            "model_calls": 2,
            "input_tokens": 30,
            "output_tokens": 5,
            "total_tokens": 35,
        }
    finally:
        await manager.shutdown()
        resources.close()


async def test_usage_without_provider_usage_reads_none_fields(tmp_path: Path) -> None:
    """响应都不带 usage：各字段记 None 而不是 0，调用次数如实统计。"""
    model, search = Model(), Search()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "docs")]),
        completion("按文档回答。"),
    ]
    model.responses = responses
    manager, resources = build_manager(tmp_path, model, search)
    try:
        view = await submit_and_view(manager, "Webhook 重试策略是什么？")
        usage = view["messages"][-1]["usage"]
        assert usage == {
            "model_calls": 2,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    finally:
        await manager.shutdown()
        resources.close()


async def test_usage_covers_all_call_sites_and_rejected_calls(tmp_path: Path) -> None:
    """排查一轮（route/plan/调查员被拒重答/汇总）：五处调用点全部计入。"""
    model, search = Model(), Search()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE_A),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])],
            usage=USAGE_B,
        ),
        completion("还没说完", finish="length", usage=USAGE_C),
        completion("重试策略报告。", usage=USAGE_D),
        completion("最终回答。", usage=USAGE_E),
    ]
    model.responses = responses
    manager, resources = build_manager(tmp_path, model, search)
    try:
        view = await submit_and_view(manager, "Webhook 重试策略是什么？帮我看看")
        usage = view["messages"][-1]["usage"]
        # 被纠正循环拒绝的第三次调用（USAGE_C）同样计费——费用已经发生。
        assert usage == {
            "model_calls": 5,
            "input_tokens": 150,
            "output_tokens": 20,
            "total_tokens": 170,
        }
    finally:
        await manager.shutdown()
        resources.close()


async def test_chunked_stream_usage_flows_to_frontend(tmp_path: Path) -> None:
    """分片流式（FR-12）：正文与工具参数分片发射时用量仍完整聚合（审核回流）。

    Model 开启分片后走 ChatStreamAccumulator 重组路径——compatible 端点生产上
    正是流式，这条用例钉住"流式聚合不丢 usage"的组合行为。
    """
    model = Model(chunked_content=2, chunked_tool_args=True)
    search = Search()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "docs")], usage=USAGE_A),
        completion("按文档回答。", usage=USAGE_B),
    ]
    model.responses = responses
    manager, resources = build_manager(tmp_path, model, search)
    try:
        view = await submit_and_view(manager, "Webhook 重试策略是什么？")
        usage = view["messages"][-1]["usage"]
        assert usage == {
            "model_calls": 2,
            "input_tokens": 30,
            "output_tokens": 5,
            "total_tokens": 35,
        }
        # 分片发射确实发生：作答调用的正文增量多于一条。
        deltas = [e for e in manager.store.read(view["id"]) if e["type"] == "model.delta"]
        assert len(deltas) >= 2
    finally:
        await manager.shutdown()
        resources.close()


def test_legacy_assistant_event_without_usage_replays() -> None:
    """旧事件没有 usage 键：投影记 None；带 usage 键的事件照常读出。"""
    legacy = build_session_view(
        [
            session_created(),
            event(2, "message.user", {"content": "问题"}, run_id="r1"),
            event(3, "message.assistant", {"content": "旧回答", "evidence": []}, run_id="r1"),
        ]
    )
    assert legacy["messages"][-1]["usage"] is None
    carried = build_session_view(
        [
            session_created(),
            event(2, "message.user", {"content": "问题"}, run_id="r1"),
            event(
                3,
                "message.assistant",
                {"content": "新回答", "evidence": [], "usage": {"model_calls": 1}},
                run_id="r1",
            ),
        ]
    )
    assert carried["messages"][-1]["usage"] == {"model_calls": 1}
