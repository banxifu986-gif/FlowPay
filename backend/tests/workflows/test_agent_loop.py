"""跨阶段检查原生工具历史、可纠正错误和每轮预算。"""

import asyncio
from pathlib import Path

import pytest
from openai.types.chat import ChatCompletion

from flowpay.core.agent.trace import Tracer
from flowpay.core.config import Settings
from flowpay.core.data_sources import SourceClient
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.router import create_stage
from flowpay.workflows.manual_loop import ManualLoopWorkflow
from tests.core.principals import INTERNAL

from .fixtures import Model, Search, completion, empty_sources, grounded, search_call


def cast_stage(stage: str, model: Model) -> ManualLoopWorkflow:
    """取得可调预算的 manual-loop 实例；create_stage 的 Protocol 不暴露实现字段。"""
    workflow = create_stage(stage, model, retrieval_input)
    if not isinstance(workflow, ManualLoopWorkflow):
        raise AssertionError(f"unexpected workflow type: {type(workflow)}")
    return workflow


def successful_search(identifier: str = "good") -> list[ChatCompletion | Exception]:
    """一次检索加一次作答，用的是各阶段共用的原生协议。"""
    return [completion(calls=[search_call(identifier, "HTTP 410")]), grounded()]


@pytest.mark.parametrize(
    "error",
    [
        "unknown_tool",
        "invalid_json",
        "invalid_limit",
        "boolean_limit",
        "blank_query",
        "truncated",
    ],
)
@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_model_can_repair_its_own_request(tmp_path: Path, stage: str, error: str) -> None:
    """坏参数不执行检索，错误以匹配原 ID 的 tool 消息返回；坏回答走模型反馈。"""
    model, search = Model(), Search()
    settings = Settings(tmp_path, tmp_path, workflow=stage, knowledge_collection_id="fixture")
    resources = RuntimeResources(settings, empty_sources(tmp_path), retriever=search.for_run)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"),
        create_stage(stage, model, retrieval_input),
        settings,
        resources,
    )
    call = search_call(
        "bad",
        " " if error == "blank_query" else "HTTP 410",
        11 if error == "invalid_limit" else True if error == "boolean_limit" else 10,
    )
    function = require_object(call["function"])
    if error == "unknown_tool":
        function["name"] = "missing_tool"
    if error == "invalid_json":
        function["arguments"] = "{bad"
    bad = (
        completion("还没说完", finish="length")
        if error == "truncated"
        else completion(calls=[call])
    )
    model.responses = [bad, *successful_search()]
    try:
        sid = (await manager.create_session(INTERNAL))["id"]
        await manager.submit_message(sid, "first", "HTTP 410", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert view["status"] == "completed"
        assert len(search.queries) == 1
        feedback = require_list(model.requests[1]["messages"])
        if error == "truncated":
            assert "上次输出无法使用" in str(feedback)
        else:
            result = require_object(feedback[-1])
            assert result["role"] == "tool" and result["tool_call_id"] == "bad"
            assert "error" in str(result["content"])
        assert any(
            e["type"] == ("answer.rejected" if error == "truncated" else "tool.error")
            for e in manager.store.read(sid)
        )
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_budget_is_per_turn_and_tool_history_survives(tmp_path: Path, stage: str) -> None:
    """一次模型多工具请求最多执行三次；下一轮恢复历史后重新开放工具。"""
    model, search = Model(), Search()
    settings = Settings(tmp_path, tmp_path, workflow=stage, knowledge_collection_id="fixture")
    resources = RuntimeResources(settings, empty_sources(tmp_path), retriever=search.for_run)
    workflow = cast_stage(stage, model)
    # 本用例的观察对象就是每轮工具预算；把实现额度固定为 3，验证第 4 个调用被拒绝。
    workflow.max_tool_calls = 3
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"),
        workflow,
        settings,
        resources,
    )
    model.responses = [
        completion(calls=[search_call(str(i), f"query {i}") for i in range(4)]),
        grounded(),
    ]
    try:
        sid = (await manager.create_session(INTERNAL))["id"]
        await manager.submit_message(sid, "first", "HTTP 410", INTERNAL)
        await manager.tasks[sid]
        assert (await manager.get_session(sid, INTERNAL))["status"] == "completed"
        assert len(search.queries) == 3
        assert model.requests[-1]["tools"] == []
        model.responses = successful_search("next")
        next_request = len(model.requests)
        await manager.submit_message(sid, "second", "再查一次", INTERNAL)
        await manager.tasks[sid]
        assert (await manager.get_session(sid, INTERNAL))["status"] == "completed"
        assert len(search.queries) == 4
        assert model.requests[next_request]["tools"]
        assert "本轮工具预算已关闭" not in str(model.requests[next_request]["messages"])
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_current_state_is_saved_and_collection_mismatch_is_rejected(
    tmp_path: Path, stage: str
) -> None:
    """当前检查点保存原生历史，集合不符时拒绝继续。"""
    model, search = Model(), Search()
    workflow = create_stage(stage, model, retrieval_input)
    tracer = Tracer(EventStore(tmp_path / "events.sqlite"), "s", "r")
    sources = SourceClient(empty_sources(tmp_path))
    runtime = AgentRuntime(
        tracer, sources, knowledge=search.for_run(tracer), collection_id="fixture"
    )
    model.responses = successful_search()
    turn = TurnInput("HTTP 410", [], {})
    outcome = await workflow.run(turn, runtime)
    assert "format" not in outcome.saved_state
    assert "tool_call_id" in str(outcome.saved_state["messages"])
    # 数据版本核对在受理入口完成；阶段只在恢复已登记来源时守住最后一道边界。
    moved = AgentRuntime(tracer, sources, knowledge=search.for_run(tracer), collection_id="other")
    with pytest.raises(ValueError, match="another collection"):
        await workflow.run(TurnInput("继续", [], outcome.saved_state), moved)


@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_cancel_after_success_keeps_previous_checkpoint(
    tmp_path: Path,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败/取消拥有独立消息副本，已提交的上轮工具和来源不受影响。"""
    model, search = Model(), Search()
    settings = Settings(tmp_path, tmp_path, workflow=stage, knowledge_collection_id="fixture")
    resources = RuntimeResources(settings, empty_sources(tmp_path), retriever=search.for_run)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"),
        create_stage(stage, model, retrieval_input),
        settings,
        resources,
    )
    model.responses = successful_search()
    try:
        sid = (await manager.create_session(INTERNAL))["id"]
        await manager.submit_message(sid, "first", "HTTP 410", INTERNAL)
        await manager.tasks[sid]
        saved = (await manager.get_session(sid, INTERNAL))["state"]
        entered = asyncio.Event()

        async def block(request: JsonObject, emit: ChunkSink) -> ChatCompletion:
            """模型收到追加问题后等待取消，不产生可提交响应。"""
            _ = request, emit
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(model, "invoke", block)
        await manager.submit_message(sid, "second", "继续", INTERNAL)
        await asyncio.wait_for(entered.wait(), 3)
        await manager.cancel_run(sid, INTERNAL)
        view = await manager.get_session(sid, INTERNAL)
        assert view["status"] == "canceled" and view["state"] == saved
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_unknown_citation_does_not_retry_or_discard_answer(
    tmp_path: Path, stage: str
) -> None:
    """三个阶段都提交完整正文，未知引用留作诊断，不额外消耗模型调用。"""
    model, search = Model(), Search()
    workflow = create_stage(stage, model, retrieval_input)
    store = EventStore(tmp_path / "events.sqlite")
    tracer = Tracer(store, "s", "r")
    runtime = AgentRuntime(
        tracer,
        SourceClient(empty_sources(tmp_path)),
        knowledge=search.for_run(tracer),
        collection_id="fixture",
    )
    text = "资料仍有缺口。[doc:UNKNOWN#c001]"
    model.responses = [completion(text)]
    result = await workflow.run(TurnInput("请解释规则", [], {}), runtime)
    # 正文原样提交，不因为编造的编号重试；界面据本轮实际片段标出无法定位的引用。
    assert result.text == text
    assert "doc:UNKNOWN#c001" not in [passage.id for passage in result.evidence]
    assert len(model.requests) == 1
    assert not any(e["type"] == "answer.rejected" for e in store.read("s"))


@pytest.mark.parametrize("stage", ["manual-loop"])
async def test_previous_workflow_version_requires_new_session(tmp_path: Path, stage: str) -> None:
    """版本判断由会话管理器统一负责，旧版本会话不能进入当前阶段循环。"""
    from flowpay.core.execution.errors import Conflict

    model = Model()
    workflow = create_stage(stage, model, retrieval_input)
    settings = Settings(tmp_path, tmp_path)
    store = EventStore(tmp_path / "events.sqlite")
    # 旧会话版本必须与当前实现不同；硬编码 "1" 曾与当前 version 相同，断言必然不触发。
    store.append(
        "old",
        [("session.created", {"workflow": {"id": workflow.id, "version": "previous"}})],
    )
    manager = SessionManager(store, workflow, settings)
    try:
        with pytest.raises(Conflict):
            await manager.submit_message("old", "request", "继续", INTERNAL)
        assert model.requests == []
    finally:
        await manager.shutdown()
