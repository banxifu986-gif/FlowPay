"""澄清状态与跨轮简报：状态序列化、主题去重、检查点恢复（specs/007 FR-5）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

import pytest
from openai.types.chat import ChatCompletion

from flowpay.core.agent.trace import Tracer
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_list,
    require_object,
    require_text,
    to_json_object,
)
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.workflows.multi_agent import MultiAgentWorkflow
from flowpay.workflows.multi_agent.clarification import ClarificationState
from flowpay.workflows.multi_agent.state import SharedTurn

if TYPE_CHECKING:
    from flowpay.workflows.manual_loop import ConditionField
    from flowpay.workflows.multi_agent.clarification import ClarifyOutcome

from tests.core.principals import INTERNAL, MERCHANT

from .fixtures import (
    Model,
    Search,
    completion,
    conversation,
    empty_sources,
    grounded,
    intent_call,
    read_call,
    search_call,
    tasks_call,
)

TROUBLESHOOT = "支付 pay_bg_001_0003 一直没收到通知，帮我看看"


class WorkerScript(Model):
    """按请求可见的工具集分发脚本：文档调查与汇总各一份队列，与调度顺序无关。"""

    def __init__(self) -> None:
        """主队列留给协调者与业务调查员；两个子队列按调用点各自消费。"""
        super().__init__()
        self.doc_script: list[ChatCompletion | Exception] = []
        self.synthesize_script: list[ChatCompletion | Exception] = []

    @override
    async def invoke(self, request: JsonObject, emit: ChunkSink) -> ChatCompletion:
        """按工具集选队列：文档调查、汇总节点各用各的脚本，其余按主队列。"""
        names = {
            require_object(require_object(tool)["function"])["name"]
            for tool in require_list(request["tools"])
        }
        if "read_document" in names and self.doc_script:
            self.responses.insert(0, self.doc_script.pop(0))
        elif "set_response" in names and self.synthesize_script:
            self.responses.insert(0, self.synthesize_script.pop(0))
        return await super().invoke(request, emit)


class NoopTracer:
    """只吞事件的追踪替身；本文件的用例不校验事件载荷。"""

    async def emit(self, *_args: object, **_kwargs: object) -> None:
        return None


def runtime() -> AgentRuntime:
    """绑定空追踪的运行时：模型与检索全部由夹具替换，不触达外部服务。"""
    tracer = cast("Tracer", cast(object, NoopTracer()))
    return AgentRuntime(tracer, collection_id="fixture")


async def run_turn(model: Model, saved_state: JsonObject) -> TurnResult:
    """跑一轮多轮排查，返回阶段产出的检查点与回答。"""
    workflow = MultiAgentWorkflow(model, retrieval_input)
    return await workflow.run(TurnInput(TROUBLESHOOT, [], saved_state), runtime())


def seeded() -> JsonObject:
    """带澄清状态的检查点：已问目标、已答现象、用户声明拿不到时间。"""
    return {
        "clarification": {
            "asked_topics": ["goal"],
            "answers": {"symptom_details": "创建支付后回调一直 500"},
            "unavailable_topics": ["occurrence_time"],
        }
    }


def test_asked_topics_are_deduped() -> None:
    """同一主题重复发问只记一次；顺序保持首次出现的次序。"""
    state = ClarificationState()
    state.mark_asked(["goal", "symptom_details"])
    state.mark_asked(["goal"])
    assert state.asked_topics == ["goal", "symptom_details"]


def test_latest_answer_wins_and_clears_unavailable() -> None:
    """改口取最新值；先声明拿不到、后又提供时该主题恢复为已回答。"""
    state = ClarificationState()
    state.mark_unavailable(["api_version"])
    state.answer("api_version", "2026-01-15")
    state.answer("api_version", "2026-07-01")
    assert state.answers == {"api_version": "2026-07-01"}
    assert state.unavailable_topics == []


def test_marks_unavailable_drops_previous_answer() -> None:
    """用户改口说拿不到时，旧值不再作为已确认事实保留。"""
    state = ClarificationState()
    state.answer("occurrence_time", "昨天下午")
    state.mark_unavailable(["occurrence_time"])
    assert state.answers == {}
    assert state.unavailable_topics == ["occurrence_time"]


def test_missing_topics_are_those_never_answered_asked_or_unavailable() -> None:
    """还缺的主题只包含未回答、未标记拿不到、也未问过的目标主题。"""
    state = ClarificationState()
    state.mark_asked(["goal"])
    state.answer("symptom_details", "回调 500")
    state.mark_unavailable(["occurrence_time"])
    assert state.missing_topics() == ["api_version"]


def test_state_round_trips_through_checkpoint_shape() -> None:
    """检查点 JSON 用固定三个键；恢复后状态与写入前一致。"""
    state = ClarificationState()
    state.mark_asked(["goal"])
    state.answer("symptom_details", "回调 500")
    state.mark_unavailable(["occurrence_time"])
    stored = require_object(parse_json(json.dumps(to_json_object(state))))
    restored = ClarificationState.restore(stored)
    assert restored.asked_topics == ["goal"]
    assert restored.answers == {"symptom_details": "回调 500"}
    assert restored.unavailable_topics == ["occurrence_time"]
    assert restored == state


def test_asked_conditions_are_recorded_and_restored() -> None:
    """采纳的追问同时记条件：已问条件跨检查点保留（SC-3 的条件侧口径）。"""
    state = ClarificationState()
    state.mark_asked(["goal"], ["request_id", "environment"])
    assert state.asked_conditions == ["request_id", "environment"]
    stored = require_object(parse_json(json.dumps(to_json_object(state))))
    restored = ClarificationState.restore(stored)
    assert restored.asked_conditions == ["request_id", "environment"]
    assert restored == state


def test_restore_drops_unknown_or_duplicate_condition_fields() -> None:
    """检查点里不认识的条件字段丢弃，重复字段去重，顺序按首次出现。"""
    restored = ClarificationState.restore(
        {
            "asked_topics": [],
            "answers": {},
            "unavailable_topics": [],
            "asked_conditions": ["request_id", "not_a_field", "request_id"],
        }
    )
    assert restored.asked_conditions == ["request_id"]


def test_still_missing_filters_asked_conditions_and_their_category() -> None:
    """已问条件与同类别其它字段都算已知；未问过的类别照常放行。"""
    from flowpay.workflows.multi_agent.clarification import still_missing

    state = ClarificationState()
    state.mark_asked([], ["request_id"])
    topics, conditions = still_missing(
        {}, state, [], conditions=["request_id", "payment_id", "environment"]
    )
    assert topics == []
    assert conditions == ["environment"]


def test_note_lists_asked_conditions() -> None:
    """简报同时列出已问过的条件，模型据此不再重复索要。"""
    from flowpay.workflows.multi_agent.clarification import clarification_note

    state = ClarificationState()
    state.mark_asked([], ["account_id"])
    note = require_text(clarification_note(state))
    assert "已问过（条件）：account_id" in note


def test_note_is_absent_without_any_progress() -> None:
    """没有任何澄清进度时不注入简报段落。"""
    from flowpay.workflows.multi_agent.clarification import clarification_note

    assert clarification_note(ClarificationState()) is None


def test_note_lists_asked_answers_and_unavailable() -> None:
    """简报逐组列出已问、已回答（含值）与用户明确拿不到的主题。"""
    from flowpay.workflows.multi_agent.clarification import clarification_note

    note = require_text(clarification_note(ClarificationState.restore(seeded()["clarification"])))
    assert "已问过：goal" in note
    assert "已回答：symptom_details=创建支付后回调一直 500" in note
    assert "用户明确拿不到：occurrence_time" in note


async def test_turn_restores_state_into_plan_briefing_and_writes_it_back() -> None:
    """带澄清状态的检查点：plan 请求看到进度说明，轮末检查点原样保留状态。"""
    model = Model()
    model.responses = [
        completion(calls=[tasks_call("plan_1", [])]),
        grounded("已根据已有结论作答。"),
    ]
    result = await run_turn(model, seeded())
    plan_request = str(model.requests[0]["messages"])
    assert "已问过：goal" in plan_request
    assert "用户明确拿不到：occurrence_time" in plan_request
    clarification = require_object(require_object(result.saved_state)["clarification"])
    assert require_list(clarification["asked_topics"]) == ["goal"]
    assert require_object(clarification["answers"])["symptom_details"] == ("创建支付后回调一直 500")
    assert require_list(clarification["unavailable_topics"]) == ["occurrence_time"]


async def test_empty_state_is_checkpointed_as_empty_shape() -> None:
    """无澄清进度时检查点仍写出空形状，供下一轮凭它恢复。"""
    model = Model()
    model.responses = [
        completion(calls=[tasks_call("plan_1", [])]),
        grounded("已根据已有结论作答。"),
    ]
    result = await run_turn(model, {})
    clarification = require_object(require_object(result.saved_state)["clarification"])
    assert require_list(clarification["asked_topics"]) == []
    assert require_object(clarification["answers"]) == {}
    assert require_list(clarification["unavailable_topics"]) == []
    assert "已问过" not in str(model.requests[0]["messages"])


def test_shared_turn_holds_clarification_state() -> None:
    """本轮共享资源持有澄清状态，独立于 Case 的查询条件。"""
    shared = SharedTurn()
    assert shared.clarification.answers == {}
    shared.clarification.answer("goal", "确认回调为什么没到")
    assert shared.case.known == {}


# --- 批次 2：US1 先查后问、一次问全（T007–T012） ---

if TYPE_CHECKING:
    from flowpay.workflows.multi_agent.clarification import Clarifier


def ask_arguments(
    missing: list[str] | None = None,
    topics: list[str] | None = None,
    question: str = "还需要补充",
) -> JsonObject:
    """构造一次 ask_user 的工具参数；参数形状与模型可见 schema 一致。"""
    return {"missing": missing or [], "topics": topics or [], "question": question}


def unused_handler(arguments: JsonObject) -> ClarifyOutcome:
    """只用于构造工具检查 schema；本文件的用例不走真实处理函数。"""
    raise AssertionError(f"unexpected call: {arguments}")


def test_ask_user_schema_offers_conditions_and_topics() -> None:
    """ask_user 同时接受条件与背景主题，只有 question 必填（跨字段校验在处理函数里）。"""
    from flowpay.workflows.multi_agent.clarification import clarify_tool

    tool = clarify_tool(unused_handler)
    assert tool.name == "ask_user"
    parameters = require_object(tool.parameters)
    properties = require_object(parameters["properties"])
    assert set(properties) == {"missing", "topics", "question"}
    assert require_object(require_object(properties["topics"])["items"])["enum"] == [
        "goal",
        "symptom_details",
        "occurrence_time",
        "api_version",
    ]
    assert require_list(parameters["required"]) == ["question"]
    assert parameters["additionalProperties"] is False


async def test_ask_user_rejects_empty_conditions_and_topics() -> None:
    """两组都空时不能发问：追问必须说明缺什么。"""
    from flowpay.core.tools import ToolInputError
    from flowpay.workflows.multi_agent.clarification import clarify_tool

    tool = clarify_tool(clarifier().accept)
    with pytest.raises(ToolInputError):
        await tool.execute(ask_arguments())


def clarifier(
    known: dict[str, str] | None = None,
    state: ClarificationState | None = None,
) -> Clarifier:
    """构造一条追问通道：已知条件与澄清状态，待回复列表由通道自己维护。"""
    from flowpay.workflows.multi_agent.clarification import Clarifier

    return Clarifier(
        cast("Mapping[ConditionField, str]", known or {}), state or ClarificationState(), []
    )


async def test_accept_refuses_topics_already_asked_answered_or_unavailable() -> None:
    """已问、已回答、用户明说拿不到的主题都退回，只有真正的新主题进入待回复。"""
    state = ClarificationState()
    state.mark_asked(["api_version"])
    state.answer("goal", "确认回调为什么没到")
    state.mark_unavailable(["occurrence_time"])
    channel = clarifier(state=state)
    outcome = channel.accept(ask_arguments([], ["goal", "occurrence_time", "api_version"]))
    assert outcome.accepted_topics == ()
    assert outcome.notice is not None
    assert channel.pending == []


async def test_accept_refuses_conditions_user_already_provided() -> None:
    """用户已经给过的条件不再索要，回填说明让模型直接用。"""
    channel = clarifier(known={"account_id": "acct_bg_001", "environment": "production"})
    outcome = channel.accept(ask_arguments(["account_id", "environment"]))
    assert outcome.accepted_conditions == ()
    assert outcome.notice is not None
    assert channel.pending == []


async def test_accept_refuses_asked_condition_category() -> None:
    """已问过条件后，同类别其它字段也退回（一次问全，不逐轮换字段钓）。"""
    state = ClarificationState()
    state.mark_asked([], ["payment_id"])
    channel = clarifier(state=state)
    outcome = channel.accept(ask_arguments(["request_id"]))
    assert outcome.accepted_conditions == ()
    assert outcome.notice is not None
    assert "已问过" in outcome.notice
    assert channel.pending == []


async def test_second_request_merges_into_single_pending_entry() -> None:
    """同轮两个调查员的缺项合并成一条待回复追问，用户只会看到一条。"""
    channel = clarifier()
    first = channel.accept(ask_arguments(["account_id"], ["goal"], "缺账户与目标"))
    second = channel.accept(ask_arguments(["payment_id"], ["symptom_details"], "还要编号与现象"))
    assert first.accepted_conditions == ("account_id",)
    assert second.accepted_conditions == ("payment_id",)
    pending = channel.pending
    assert len(pending) == 1
    request = pending[0]
    assert request.conditions == ("account_id", "payment_id")
    assert request.topics == ("goal", "symptom_details")
    assert "缺账户与目标" in request.question
    assert "还要编号与现象" in request.question


def manager_fixture(
    tmp_path: Path, model: Model, search: Search
) -> tuple[SessionManager, RuntimeResources]:
    """真实运行时装配 + 受控模型；与 test_multi_agent 同一套夹具口径。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(
        settings, sources=empty_sources(tmp_path), retriever=search.for_run
    )
    workflow = MultiAgentWorkflow(model, retrieval_input)
    manager = SessionManager(EventStore(tmp_path / "events.sqlite"), workflow, settings, resources)
    return manager, resources


def ask_call(
    identifier: str,
    missing: list[str] | None = None,
    topics: list[str] | None = None,
    question: str = "请补充缺失信息",
) -> JsonObject:
    """构造一次带条件与主题的 ask_user 调用；arguments 仍是 JSON 字符串。"""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "ask_user",
            "arguments": json.dumps(
                {"missing": missing or [], "topics": topics or [], "question": question}
            ),
        },
    }


async def say(manager: SessionManager, sid: str, request_id: str, text: str) -> None:
    """提交一轮用户消息并等它结束。"""
    await manager.submit_message(sid, request_id, text, INTERNAL)
    await manager.tasks[sid]


async def test_sql_worker_offers_topics_and_records_asked_topics(tmp_path: Path) -> None:
    """业务调查员可以连背景主题一起问；问过的主题进入澄清状态，供后续轮次不再重复。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[tasks_call("plan_1", [{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["goal", "occurrence_time"],
                )
            ]
        ),
        completion(calls=[response_call("clarify", question="请补充账户、环境与发生时间。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == ["goal", "occurrence_time"]
        worker_tools = [
            require_object(require_object(item)["function"])
            for item in require_list(model.requests[1]["tools"])
        ]
        ask = next(tool for tool in worker_tools if tool["name"] == "ask_user")
        properties = require_object(require_object(ask["parameters"])["properties"])
        assert set(properties) == {"missing", "topics", "question"}
    finally:
        await manager.shutdown()
        resources.close()


def plan_call(tasks: list[JsonObject], clarification: JsonObject | None = None) -> JsonObject:
    """构造一次 set_tasks 调用，可带澄清状态更新；arguments 仍是 JSON 字符串。"""
    arguments: JsonObject = {"tasks": tasks}
    if clarification is not None:
        arguments["clarification"] = clarification
    return {
        "id": "plan_clarify",
        "type": "function",
        "function": {"name": "set_tasks", "arguments": json.dumps(arguments)},
    }


def test_set_tasks_schema_carries_clarification_update() -> None:
    """协调者的结构化调用同时回报澄清状态更新：答案与"拿不到"两组，默认空。"""
    from flowpay.workflows.multi_agent.investigation import SET_TASKS

    parameters = require_object(require_object(SET_TASKS["function"])["parameters"])
    properties = require_object(parameters["properties"])
    clarification = require_object(properties["clarification"])
    assert set(require_object(clarification["properties"])) == {"answers", "unavailable"}
    assert clarification.get("additionalProperties") is False
    assert require_object(require_object(clarification["properties"])["answers"])["type"] == (
        "array"
    )
    assert parameters["additionalProperties"] is False


def test_plan_update_reads_answers_and_unavailable_from_tasks_call() -> None:
    """同一次调用里的答案与"拿不到"声明都能被解析出来；任务照常拆解。"""
    from flowpay.workflows.multi_agent.investigation import read_plan

    tasks, update = read_plan(
        completion(
            calls=[
                plan_call(
                    [{"kind": "docs", "objective": "查明重试策略"}],
                    {
                        "answers": [{"topic": "goal", "value": "确认通知为什么没到"}],
                        "unavailable": ["occurrence_time"],
                    },
                )
            ]
        )
    )
    assert [task.objective for task in tasks] == ["查明重试策略"]
    assert [(a.topic, a.value) for a in update.answers] == [("goal", "确认通知为什么没到")]
    assert update.unavailable == ["occurrence_time"]


def test_plan_update_drops_unknown_topic_and_truncates_long_value() -> None:
    """非法主题与未知类别按空处理；超长值按上限截断，不阻塞拆任务。"""
    from flowpay.workflows.multi_agent.investigation import read_plan

    answers = [
        {"topic": "unknown_topic", "value": "x"},
        {"topic": "symptom_details", "value": "现" * 800},
    ]
    tasks, update = read_plan(
        completion(
            calls=[
                plan_call(
                    [{"kind": "sql", "objective": "查记录"}],
                    {"answers": answers, "unavailable": ["api_version", "not_a_topic"]},
                )
            ]
        )
    )
    assert [task.objective for task in tasks] == ["查记录"]
    assert [a.topic for a in update.answers] == ["symptom_details"]
    assert len(update.answers[0].value) <= 500
    assert update.unavailable == ["api_version"]


async def test_plan_updates_clarification_state_in_the_same_call(tmp_path: Path) -> None:
    """状态更新与任务拆解共用一次模型调用：本轮只有协调者与汇总两次请求。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [{"kind": "docs", "objective": "查明重试策略"}],
                    {
                        "answers": [{"topic": "goal", "value": "确认通知为什么没到"}],
                        "unavailable": ["occurrence_time"],
                    },
                )
            ]
        ),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
        grounded("已根据报告作答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_object(clarification["answers"]) == {"goal": "确认通知为什么没到"}
        assert require_list(clarification["unavailable_topics"]) == ["occurrence_time"]
        assert len(model.requests) == 4
        assert len(search.queries) == 1
    finally:
        await manager.shutdown()
        resources.close()


async def test_missing_account_queries_docs_then_asks_once(tmp_path: Path) -> None:
    """缺账户时业务调查先标记缺条件，文档调查照常执行，本轮只发一条合并追问。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [
                        {"kind": "sql", "objective": "查这笔支付的通知事件"},
                        {"kind": "docs", "objective": "查明 Webhook 重试策略"},
                    ]
                )
            ]
        ),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["goal", "occurrence_time"],
                )
            ]
        ),
    ]
    # 文档调查员的脚本单独排队：两个 worker 的调度顺序因此不影响断言。
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
    ]
    model.synthesize_script = [
        completion(calls=[response_call("clarify", question="请提供账户、环境与发生时间。")])
    ]

    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        assert len(search.queries) == 1
        findings = [require_object(item) for item in require_list(view["state"]["findings"])]
        assert "search_documents" in [require_text(item["tool"]) for item in findings]
        clarification = require_object(view["state"]["clarification"])
        assert require_list(clarification["asked_topics"]) == ["goal", "occurrence_time"]
    finally:
        await manager.shutdown()
        resources.close()


async def test_sufficient_evidence_answers_without_clarification(tmp_path: Path) -> None:
    """已有报告足以回答时直接作答，不发起新的追问。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
        grounded("重试策略是 HTTP 410 后禁用端点。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert len(search.queries) == 1
        clarification = require_object(view["state"]["clarification"])
        assert require_list(clarification["asked_topics"]) == []
    finally:
        await manager.shutdown()
        resources.close()


def test_set_tasks_parameters_are_self_contained() -> None:
    """模型可见的整个参数 Schema 自包含：不出现未随附 $defs 的悬空 $ref。"""
    from flowpay.workflows.multi_agent.investigation import SET_TASKS

    parameters = require_object(require_object(SET_TASKS["function"])["parameters"])
    assert "$ref" not in json.dumps(parameters)
    items = require_object(
        require_object(require_object(parameters["properties"])["tasks"])["items"]
    )
    assert set(require_list(items["required"])) == {"kind", "objective"}


def test_plan_update_keeps_four_deduped_answers_at_most() -> None:
    """超过 4 条答案时按主题去重并只保留前 4 条，不抛异常。"""
    from flowpay.workflows.multi_agent.investigation import read_plan

    entries: list[JsonObject] = [
        {"topic": "goal", "value": "目标一"},
        {"topic": "symptom_details", "value": "现象一"},
        {"topic": "occurrence_time", "value": "昨天"},
        {"topic": "api_version", "value": "2026-07-01"},
        {"topic": "goal", "value": "目标二"},
    ]
    tasks, update = read_plan(
        completion(
            calls=[
                plan_call(
                    [{"kind": "docs", "objective": "查明重试策略"}],
                    {"answers": entries},
                )
            ]
        )
    )
    assert [task.objective for task in tasks] == ["查明重试策略"]
    assert [(a.topic, a.value) for a in update.answers] == [
        ("goal", "目标一"),
        ("symptom_details", "现象一"),
        ("occurrence_time", "昨天"),
        ("api_version", "2026-07-01"),
    ]


def test_plan_update_skips_non_string_unavailable_item() -> None:
    """unavailable 里的非字符串项被丢弃，其余合法项保留。"""
    from flowpay.workflows.multi_agent.investigation import read_plan

    tasks, update = read_plan(
        completion(
            calls=[
                plan_call(
                    [{"kind": "sql", "objective": "查记录"}],
                    {"unavailable": ["api_version", 42, None, "occurrence_time"]},
                )
            ]
        )
    )
    assert [task.objective for task in tasks] == ["查记录"]
    assert update.unavailable == ["api_version", "occurrence_time"]


def test_plan_update_ignores_broken_clarification_section() -> None:
    """澄清段整体类型不对时返回空更新，任务拆解照常进行。"""
    from flowpay.workflows.multi_agent.investigation import read_plan

    tasks, update = read_plan(
        completion(
            calls=[
                plan_call(
                    [{"kind": "docs", "objective": "查明规则"}],
                    {"answers": "not-a-list", "unavailable": {"topic": "goal"}},
                )
            ]
        )
    )
    assert [task.objective for task in tasks] == ["查明规则"]
    assert update.answers == []
    assert update.unavailable == []


# --- 批次 3：US2 等待轮带结论（T013–T016） ---


def response_call(
    mode: str,
    answer: str = "",
    question: str = "",
    topics: list[str] | None = None,
    conditions: list[str] | None = None,
) -> JsonObject:
    """构造一次 set_response 调用；arguments 仍是协议要求的 JSON 字符串。"""
    arguments: JsonObject = {"mode": mode, "answer": answer, "question": question}
    if topics is not None:
        arguments["topics"] = topics
    if conditions is not None:
        arguments["conditions"] = conditions
    return {
        "id": "resp_1",
        "type": "function",
        "function": {"name": "set_response", "arguments": json.dumps(arguments)},
    }


def test_set_response_schema_declares_modes_and_fields() -> None:
    """汇总决策工具的 Schema 自包含：mode 必填，answer 与 question 由模式决定。"""
    from flowpay.workflows.multi_agent.investigation import SET_RESPONSE

    parameters = require_object(require_object(SET_RESPONSE["function"])["parameters"])
    assert "$ref" not in json.dumps(parameters)
    properties = require_object(parameters["properties"])
    assert set(properties) == {"mode", "answer", "question", "topics", "conditions"}
    assert require_object(properties["mode"])["enum"] == ["answer", "clarify"]
    assert require_list(parameters["required"]) == ["mode"]
    assert parameters["additionalProperties"] is False


def test_read_response_parses_answer_mode() -> None:
    """answer 模式带正文：不需要追问。"""
    from flowpay.workflows.multi_agent.investigation import read_response

    decision = read_response(completion(calls=[response_call("answer", answer="结论如下。")]))
    assert decision is not None
    assert decision.mode == "answer"
    assert decision.answer == "结论如下。"


def test_read_response_parses_clarify_mode() -> None:
    """clarify 模式带问题与仍缺的条件/主题。"""
    from flowpay.workflows.multi_agent.investigation import read_response

    decision = read_response(
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="已查到规则；还需要账户与发生时间。",
                    topics=["occurrence_time", "api_version"],
                    conditions=["account_id"],
                )
            ]
        )
    )
    assert decision is not None
    assert decision.mode == "clarify"
    assert decision.question == "已查到规则；还需要账户与发生时间。"
    assert decision.topics == ("occurrence_time", "api_version")
    assert decision.conditions == ("account_id",)


def test_read_response_rejects_invalid_mode() -> None:
    """非法的 mode 无法解释，按未调用处理。"""
    from flowpay.workflows.multi_agent.investigation import read_response

    assert read_response(completion(calls=[response_call("maybe", answer="结论")])) is None


def test_read_response_requires_text_for_each_mode() -> None:
    """answer 模式缺正文、clarify 模式缺问题都按未调用处理。"""
    from flowpay.workflows.multi_agent.investigation import read_response

    assert read_response(completion(calls=[response_call("answer", question="只有问题")])) is None
    assert read_response(completion(calls=[response_call("clarify", answer="只有正文")])) is None


def test_read_response_without_tool_call_is_none() -> None:
    """模型没调用工具时返回 None，由调用点按 pending 决定回退。"""
    from flowpay.workflows.multi_agent.investigation import read_response

    assert read_response(completion("直接写了正文。")) is None


async def test_invalid_response_decision_falls_back_to_clarify(tmp_path: Path) -> None:
    """模型给出非法决策时按 pending 回退为追问，整轮不失败。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id"],
                    topics=["occurrence_time"],
                    question="请提供账户与发生时间",
                )
            ]
        ),
        completion(calls=[response_call("maybe")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        assert require_text(view["messages"][-1]["content"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_missing_response_decision_answers_with_model_text(tmp_path: Path) -> None:
    """模型没调用决策工具时按正文作答，行为与既有口径一致。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([])]),
        grounded("直接作答的结论。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert "直接作答的结论" in require_text(view["messages"][-1]["content"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_answer_decision_wins_over_pending_question(tmp_path: Path) -> None:
    """模型判断证据足够时直接作答：即使有调查员在本轮发起了追问。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(calls=[ask_call("ask_1", missing=["account_id"], topics=["goal"])]),
        completion(calls=[response_call("answer", answer="已查到的结论足够回答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert "已查到的结论足够回答" in require_text(view["messages"][-1]["content"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_clarify_decision_body_is_spliced_deterministically(tmp_path: Path) -> None:
    """模型只声明缺项与问题时，正文由系统拼出结论、引用与缺项清单（T014 修复口径）。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明重试策略"}])]),
    ]
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略是 HTTP 410 后禁用端点。"),
    ]
    model.synthesize_script = [
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="还需要你补充账户编号与发生时间。",
                    topics=["occurrence_time"],
                    conditions=["account_id"],
                )
            ]
        )
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "已查到的结论" in answer
        assert "HTTP 410 后禁用端点" in answer
        assert "[doc:RULE-1#c001]" in answer
        assert "还需你补充：account_id、发生时间" in answer
        assert "还需要你补充账户编号与发生时间。" in answer
    finally:
        await manager.shutdown()
        resources.close()


# --- 批次 3：等待轮正文（T014） ---


async def test_clarify_fallback_reports_findings_before_asking(tmp_path: Path) -> None:
    """回退成追问时先给已查到的结论与引用，再列仍缺信息；不原样转发调查员的问题。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [
                        {"kind": "sql", "objective": "查这笔支付的通知事件"},
                        {"kind": "docs", "objective": "查明重试策略"},
                    ]
                )
            ]
        ),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["goal"],
                    question="内部问题：缺账户与目标，请追问。",
                )
            ]
        ),
        conversation("模型直接写正文、没有给出决策。"),
    ]
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略是 HTTP 410 后禁用端点。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "已查到的结论" in answer
        assert "HTTP 410 后禁用端点" in answer
        assert "[doc:RULE-1#c001]" in answer
        assert "account_id" in answer and "排查目标" in answer
        assert "内部问题：缺账户与目标，请追问。" not in answer
    finally:
        await manager.shutdown()
        resources.close()


async def test_clarify_fallback_says_no_findings_without_reports(tmp_path: Path) -> None:
    """本轮没有查询产出时明确说明尚未查到结论，仍给出追问。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id"],
                    topics=["symptom_details"],
                    question="内部问题：账户与现象。",
                )
            ]
        ),
        conversation("模型没给决策时按 pending 回退。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "尚未查到结论" in answer
        assert "account_id" in answer and "现象与报错" in answer
        assert "内部问题：账户与现象。" not in answer
    finally:
        await manager.shutdown()
        resources.close()


async def test_clarify_decision_text_is_checked_for_invented_ids(tmp_path: Path) -> None:
    """追问正文同样过编造编号核对：对不上时回传纠正并重试一次。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明规则"}])]),
    ]
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
    ]
    model.synthesize_script = [
        completion(
            calls=[response_call("clarify", question="已查到结论，另见 pay_bg_9999_0001 的记录。")]
        ),
        completion(
            calls=[response_call("clarify", question="已查到结论；还需要账户编号与发生时间。")]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "pay_bg_9999_0001" not in answer
        assert "还需要账户编号与发生时间" in answer
    finally:
        await manager.shutdown()
        resources.close()


# --- T014 修复：确定性摘要拼接 + clarification.decided 事件 ---


def decision_events(tmp_path: Path, session_id: str) -> list[JsonObject]:
    """读取该会话的澄清决策事件载荷；事件库里没有时返回空列表。"""
    store = EventStore(tmp_path / "events.sqlite")
    return [
        require_object(event["payload"])
        for event in store.read(session_id)
        if event["type"] == "clarification.decided"
    ]


async def test_clarify_decision_body_always_carries_report_summary(tmp_path: Path) -> None:
    """模型只给问题、没写摘要时，正文仍由系统拼出结论与引用，再接模型问题。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明重试策略"}])]),
    ]
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略是 HTTP 410 后禁用端点。"),
    ]
    model.synthesize_script = [
        completion(calls=[response_call("clarify", question="请提供账户编号。")])
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "已查到的结论" in answer
        assert "HTTP 410 后禁用端点" in answer
        assert "[doc:RULE-1#c001]" in answer
        assert "请提供账户编号" in answer
    finally:
        await manager.shutdown()
        resources.close()


async def test_clarify_decision_body_without_reports_says_no_findings(tmp_path: Path) -> None:
    """无报告时摘要段明确写尚未查到结论，模型问题与缺项仍在正文里。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([])]),
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="请提供账户编号。",
                    topics=["symptom_details"],
                    conditions=["account_id"],
                )
            ]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "尚未查到结论" in answer
        assert "请提供账户编号" in answer
        assert "account_id" in answer and "现象与报错" in answer
    finally:
        await manager.shutdown()
        resources.close()


async def test_decision_event_records_model_clarify(tmp_path: Path) -> None:
    """模型澄清决策落一条事件：mode=clarify、source=model、缺项与 has_reports 齐全。"""
    model, search = WorkerScript(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明规则"}])]),
    ]
    model.doc_script = [
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
    ]
    model.synthesize_script = [
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="请提供账户编号。",
                    topics=["symptom_details", "occurrence_time"],
                    conditions=["account_id"],
                )
            ]
        )
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        events = decision_events(tmp_path, sid)
        assert len(events) == 1
        assert events[0]["mode"] == "clarify"
        assert events[0]["source"] == "model"
        assert require_list(events[0]["topics"]) == ["symptom_details", "occurrence_time"]
        assert require_list(events[0]["conditions"]) == ["account_id"]
        assert events[0]["has_reports"] is True
    finally:
        await manager.shutdown()
        resources.close()


async def test_decision_event_records_fallback_clarify(tmp_path: Path) -> None:
    """模型没给决策时事件记为 fallback，缺项取本轮 pending 并集。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["goal", "occurrence_time"],
                )
            ]
        ),
        conversation("模型直接写正文、没有给出决策。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        events = decision_events(tmp_path, sid)
        assert len(events) == 1
        assert events[0]["mode"] == "clarify"
        assert events[0]["source"] == "fallback"
        assert require_list(events[0]["topics"]) == ["goal", "occurrence_time"]
        assert require_list(events[0]["conditions"]) == ["account_id", "environment"]
        # 发问的调查员不交报告：本轮没有任何调查报告，has_reports 因此为假。
        assert events[0]["has_reports"] is False
    finally:
        await manager.shutdown()
        resources.close()


async def test_decision_event_records_answer(tmp_path: Path) -> None:
    """合法作答决策落事件：mode=answer、source=model、缺项为空。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([])]),
        completion(calls=[response_call("answer", answer="已根据报告作答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        events = decision_events(tmp_path, sid)
        assert len(events) == 1
        assert events[0]["mode"] == "answer"
        assert events[0]["source"] == "model"
        assert require_list(events[0]["topics"]) == []
        assert require_list(events[0]["conditions"]) == []
        assert events[0]["has_reports"] is False
    finally:
        await manager.shutdown()
        resources.close()


# --- T014 关闭：决策缺项过滤 + mark_asked 时序 ---


async def test_clarify_decision_filters_known_and_answered_items(tmp_path: Path) -> None:
    """模型重复索要已回答主题或已知条件时被过滤：正文、状态与事件都只含仍缺项。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [],
                    {
                        "answers": [
                            {"topic": "goal", "value": "查为什么没收到通知"},
                            {"topic": "symptom_details", "value": "回调一直 500"},
                        ]
                    },
                )
            ]
        ),
        conversation("已记录背景信息。"),
        completion(calls=[intent_call("intent_1", "troubleshoot")]),
        completion(calls=[plan_call([])]),
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="请补充发生时间与支付编号。",
                    topics=["goal", "symptom_details", "occurrence_time"],
                    conditions=["account_id", "payment_id"],
                )
            ]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(
            manager, sid, "first", "生产环境，账户 acct_bg_001，回调一直 500，查为什么没收到通知"
        )
        await say(manager, sid, "second", "帮我继续排查")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "排查目标" not in answer
        assert "现象与报错" not in answer
        assert "account_id" not in answer
        assert "payment_id" in answer and "发生时间" in answer
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == ["occurrence_time"]
        events = decision_events(tmp_path, sid)
        assert events[-1]["mode"] == "clarify"
        assert require_list(events[-1]["topics"]) == ["occurrence_time"]
        assert require_list(events[-1]["conditions"]) == ["payment_id"]
    finally:
        await manager.shutdown()
        resources.close()


async def test_retry_discards_first_clarify_topics(tmp_path: Path) -> None:
    """首轮 clarify 因编造编号被丢弃后重试为作答：asked_topics 不被污染，事件记作答。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([])]),
        completion(
            calls=[
                response_call("clarify", question="另见 pay_bg_9999_0001 的记录。", topics=["goal"])
            ]
        ),
        completion(calls=[response_call("answer", answer="已根据报告作答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == []
        events = decision_events(tmp_path, sid)
        assert len(events) == 1
        assert events[0]["mode"] == "answer"
        assert events[0]["source"] == "model"
    finally:
        await manager.shutdown()
        resources.close()


async def test_retry_marks_only_the_final_clarify_topics(tmp_path: Path) -> None:
    """重试后仍追问时，只有最终采纳的决策主题进入已问集合。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([])]),
        completion(
            calls=[
                response_call("clarify", question="另见 pay_bg_9999_0001 的记录。", topics=["goal"])
            ]
        ),
        completion(
            calls=[
                response_call(
                    "clarify",
                    question="请补充发生时间。",
                    topics=["occurrence_time"],
                )
            ]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == ["occurrence_time"]
        events = decision_events(tmp_path, sid)
        assert len(events) == 1
        assert events[0]["mode"] == "clarify"
        assert require_list(events[0]["topics"]) == ["occurrence_time"]
    finally:
        await manager.shutdown()
        resources.close()


async def test_clarify_with_only_known_items_keeps_summary_and_question(tmp_path: Path) -> None:
    """过滤后没有仍缺项时，正文保留摘要与模型问题，不把已知项再列一遍。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [],
                    {"answers": [{"topic": "goal", "value": "确认通知为什么没到"}]},
                )
            ]
        ),
        conversation("已记录目标。"),
        completion(calls=[intent_call("intent_1", "troubleshoot")]),
        completion(calls=[plan_call([])]),
        completion(
            calls=[response_call("clarify", question="还需要补充一些信息。", topics=["goal"])]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "生产环境，账户 acct_bg_001，帮我看看为什么没收到通知")
        await say(manager, sid, "second", "继续排查")
        view = await manager.get_session(sid, INTERNAL)
        answer = require_text(view["messages"][-1]["content"])
        assert "尚未查到结论" in answer
        assert "还需要补充一些信息" in answer
        assert "排查目标" not in answer
        clarification = require_object(require_object(view["state"])["clarification"])
        # 目标已回答，本来就不算"问过"：过滤后没有仍缺项，asked_topics 保持为空。
        assert require_list(clarification["asked_topics"]) == []
    finally:
        await manager.shutdown()
        resources.close()


# --- 批次 4：US3 跨轮状态（T017–T019） ---


def test_restore_drops_unknown_topics_but_keeps_valid_ones() -> None:
    """未知主题（不在四枚举内）丢弃，合法主题保留。"""
    restored = ClarificationState.restore(
        {
            "asked_topics": ["goal", "not_a_topic"],
            "answers": {"symptom_details": "回调 500", "mystery": "x"},
            "unavailable_topics": ["occurrence_time", "unknown"],
        }
    )
    assert restored.asked_topics == ["goal"]
    assert restored.answers == {"symptom_details": "回调 500"}
    assert restored.unavailable_topics == ["occurrence_time"]


def test_restore_truncates_long_answer_values() -> None:
    """检查点里的超长值按上限截断，与 answer() 同口径。"""
    restored = ClarificationState.restore({"answers": {"symptom_details": "现" * 800}})
    assert len(restored.answers["symptom_details"]) == 500


def test_restore_prefers_answers_over_unavailable() -> None:
    """同一主题既在 answers 又标记拿不到时以 answers 为准，并从不可提供里移除。"""
    restored = ClarificationState.restore(
        {
            "answers": {"occurrence_time": "昨天下午"},
            "unavailable_topics": ["occurrence_time", "api_version"],
        }
    )
    assert restored.answers == {"occurrence_time": "昨天下午"}
    assert restored.unavailable_topics == ["api_version"]


def test_restore_tolerates_broken_structure() -> None:
    """结构不合法时按空/跳过处理，不抛异常。"""
    restored = ClarificationState.restore(
        {
            "asked_topics": ["goal", 42, None],
            "answers": ["not-an-object"],
            "unavailable_topics": "not-a-list",
        }
    )
    assert restored.asked_topics == ["goal"]
    assert restored.answers == {}
    assert restored.unavailable_topics == []


def test_restore_round_trip_keeps_four_key_shape() -> None:
    """恢复后再写回检查点，仍是固定的四个键（含条件侧的已问记录）。"""
    state = ClarificationState.restore(
        {"answers": {"goal": "查为什么没通知"}, "asked_topics": ["occurrence_time"]}
    )
    stored = require_object(parse_json(json.dumps(to_json_object(state))))
    assert set(stored) == {
        "asked_topics",
        "answers",
        "unavailable_topics",
        "asked_conditions",
    }
    assert require_list(stored["asked_topics"]) == ["occurrence_time"]
    assert require_object(stored["answers"]) == {"goal": "查为什么没通知"}
    assert require_list(stored["unavailable_topics"]) == []


def test_clarifier_refuses_asked_topic_with_notice() -> None:
    """已问过的主题被拒绝，处理函数回填说明让模型改用已知信息。"""
    state = ClarificationState()
    state.mark_asked(["occurrence_time"])
    channel = clarifier(state=state)
    outcome = channel.accept(ask_arguments([], ["occurrence_time"]))
    assert outcome.accepted_topics == ()
    assert outcome.notice


async def test_worker_ask_without_adopted_clarify_does_not_mark_asked(tmp_path: Path) -> None:
    """调查员提议追问、但模型最终作答时不算"问过"：asked_topics 保持空。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(calls=[ask_call("ask_1", missing=["account_id"], topics=["goal"])]),
        completion(calls=[response_call("answer", answer="已根据已有信息作答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == []
    finally:
        await manager.shutdown()
        resources.close()


async def test_next_round_refuses_asked_topics_and_keeps_new_ones(tmp_path: Path) -> None:
    """上一轮已问过的主题下一轮不再索要；简报把它标成已问过，只问真正的新主题。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(calls=[ask_call("ask_1", missing=["account_id"], topics=["occurrence_time"])]),
        completion(calls=[response_call("clarify", question="请补充账户与发生时间。")]),
        completion(calls=[intent_call("intent_2", "troubleshoot")]),
        completion(calls=[plan_call([{"kind": "sql", "objective": "继续查投递记录"}])]),
        completion(
            calls=[
                ask_call("ask_2", missing=["request_id"], topics=["occurrence_time", "api_version"])
            ]
        ),
        completion(calls=[response_call("clarify", question="请补充 API 版本与请求编号。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        await say(manager, sid, "second", "继续排查投递问题")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "API 版本" in answer
        assert "发生时间" not in answer
        # 第一轮消息里已经给过的 payment_id 不再索要，只问未给过的 request_id。
        assert "request_id" in answer
        assert "payment_id" not in answer
        clarification = require_object(require_object(view["state"])["clarification"])
        assert require_list(clarification["asked_topics"]) == ["occurrence_time", "api_version"]
        assert "已问过：occurrence_time" in str(model.requests[4]["messages"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_user_declining_a_topic_is_remembered_and_not_asked_again(tmp_path: Path) -> None:
    """US3 验收：用户说"查不到"后不再问该主题，后续轮次照常排查并标注未确认部分。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        # 第 1 轮：业务调查缺条件与背景主题
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["occurrence_time"],
                )
            ]
        ),
        completion(calls=[response_call("clarify", question="请补充账户、环境与发生时间。")]),
        # 第 2 轮：补齐条件，并明确说发生时间查不到
        completion(
            calls=[
                plan_call(
                    [],
                    {"unavailable": ["occurrence_time"]},
                )
            ]
        ),
        completion(
            calls=[
                response_call(
                    "answer",
                    answer="已根据现有记录作答；由于拿不到发生时间，未能确认时间范围。",
                )
            ]
        ),
        # 第 3 轮：继续排查，只问还没问过的主题
        completion(calls=[plan_call([{"kind": "sql", "objective": "查投递记录"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_2",
                    topics=["occurrence_time", "api_version"],
                )
            ]
        ),
        completion(calls=[response_call("clarify", question="请补充 API 版本。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        first = await manager.get_session(sid, INTERNAL)
        assert require_text(first["status"]) == "waiting_user"
        first_state = require_object(require_object(first["state"])["clarification"])
        assert require_list(first_state["asked_topics"]) == ["occurrence_time"]
        assert require_object(first_state["answers"]) == {}
        assert require_list(first_state["unavailable_topics"]) == []

        await say(
            manager,
            sid,
            "second",
            "生产环境，账户 acct_bg_001，支付 pay_bg_001_0003，发生时间查不到",
        )
        second = await manager.get_session(sid, INTERNAL)
        assert require_text(second["status"]) == "completed"
        assert "未能确认时间范围" in require_text(second["messages"][-1]["content"])
        second_state = require_object(require_object(second["state"])["clarification"])
        assert require_list(second_state["unavailable_topics"]) == ["occurrence_time"]
        assert require_list(second_state["asked_topics"]) == ["occurrence_time"]
        assert require_object(second_state["answers"]) == {}

        await say(manager, sid, "third", "支付 pay_bg_001_0003 继续排查")
        third = await manager.get_session(sid, INTERNAL)
        assert require_text(third["status"]) == "waiting_user"
        answer = require_text(third["messages"][-1]["content"])
        assert "API 版本" in answer
        assert "发生时间" not in answer
        third_state = require_object(require_object(third["state"])["clarification"])
        assert require_list(third_state["asked_topics"]) == ["occurrence_time", "api_version"]
        assert require_list(third_state["unavailable_topics"]) == ["occurrence_time"]
        assert require_object(third_state["answers"]) == {}
        # 跨轮恢复：第三轮的协调者请求里看得到"拿不到"的声明。
        assert "用户明确拿不到：occurrence_time" in str(model.requests[5]["messages"])
    finally:
        await manager.shutdown()
        resources.close()


# --- 批次 5：US4 登录态绑定账户（T020–T022） ---


def test_bound_scope_is_treated_as_known_by_the_clarifier() -> None:
    """已绑定条件视为已知：拒绝索要并回填"系统绑定"，不进入本轮追问。"""
    from flowpay.workflows.multi_agent.clarification import Clarifier

    channel = Clarifier(
        {},
        ClarificationState(),
        [],
        {"account_id": "acct_bound_001"},
    )
    outcome = channel.accept(ask_arguments(["account_id"], ["goal"]))
    assert outcome.accepted_conditions == ()
    assert outcome.accepted_topics == ("goal",)
    assert "系统绑定" in (outcome.notice or "")
    assert [request.conditions for request in channel.pending] == [()]


async def test_merchant_session_schema_and_questions_omit_the_bound_account(
    tmp_path: Path,
) -> None:
    """商户会话：工具 schema 不含账户，追问不接受账户，用户可见正文也不出现账户编号。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(
            calls=[
                ask_call(
                    "ask_1",
                    missing=["account_id", "environment"],
                    topics=["occurrence_time"],
                    question="请补充环境与发生时间。",
                )
            ]
        ),
        completion(
            calls=[
                response_call(
                    "clarify", question="请补充环境与发生时间。", conditions=["account_id"]
                )
            ]
        ),
    ]
    try:
        sid = require_text((await manager.create_session(MERCHANT))["id"])
        await manager.submit_message(sid, "first", TROUBLESHOOT, MERCHANT)
        await manager.tasks[sid]
        view = await manager.get_session(sid, MERCHANT)
        assert require_text(view["status"]) == "waiting_user"
        answer = require_text(view["messages"][-1]["content"])
        assert "account_id" not in answer
        assert "environment" in answer
        worker_tools = [
            require_object(require_object(item)["function"])
            for item in require_list(model.requests[1]["tools"])
        ]
        payments = next(tool for tool in worker_tools if tool["name"] == "query_payments")
        properties = require_object(require_object(payments["parameters"])["properties"])
        assert "account_id" not in properties
        assert "系统绑定" in require_text(payments["description"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_internal_session_still_asks_for_the_account(tmp_path: Path) -> None:
    """内部会话行为不变：schema 含账户必填，追问照旧索要账户。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "sql", "objective": "查通知事件"}])]),
        completion(calls=[ask_call("ask_1", missing=["account_id"], topics=["goal"])]),
        completion(calls=[response_call("clarify", question="请补充账户与环境。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        view = await manager.get_session(sid, INTERNAL)
        answer = require_text(view["messages"][-1]["content"])
        assert "account_id" in answer
        worker_tools = [
            require_object(require_object(item)["function"])
            for item in require_list(model.requests[1]["tools"])
        ]
        payments = next(tool for tool in worker_tools if tool["name"] == "query_payments")
        properties = require_object(require_object(payments["parameters"])["properties"])
        assert "account_id" in properties
        assert "系统绑定" not in require_text(payments["description"])
    finally:
        await manager.shutdown()
        resources.close()


def test_worker_prompt_defers_required_fields_to_the_tool_schema() -> None:
    """worker 提示词不再复述"账户和环境必填"，改为以工具 schema 为准。"""
    from flowpay.workflows.multi_agent.investigation import SQL_WORKER_PROMPT

    assert "账户和环境必填" not in SQL_WORKER_PROMPT
    assert "以工具 schema 为准" in SQL_WORKER_PROMPT


# --- 批次 6：US5 查询改写（T027） ---


def test_retrieval_query_appends_answered_topics() -> None:
    """已回答的主题值拼进检索查询；没有回答时查询原样返回。"""
    from flowpay.workflows.multi_agent.clarification import query_with_facts

    state = ClarificationState()
    state.answer("symptom_details", "创建支付后回调一直 500")
    state.answer("goal", "确认通知为什么没到")
    assert query_with_facts(state, "Webhook 重试策略") == (
        "Webhook 重试策略 创建支付后回调一直 500 确认通知为什么没到"
    )
    assert query_with_facts(ClarificationState(), "Webhook 重试策略") == "Webhook 重试策略"


async def test_doc_search_uses_the_rewritten_query(tmp_path: Path) -> None:
    """跨轮已回答的主题进入文档检索查询，而不是只留在简报里。"""
    model, search = Model(), Search()
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[
                plan_call(
                    [],
                    {"answers": [{"topic": "symptom_details", "value": "回调一直 500"}]},
                )
            ]
        ),
        conversation("已记录现象。"),
        completion(calls=[intent_call("intent_2", "troubleshoot")]),
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
        completion(calls=[response_call("answer", answer="已根据报告作答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        await say(manager, sid, "second", "继续查文档规则")
        assert search.queries
        assert any("回调一直 500" in text for text, _ in search.queries)
        assert any("Webhook 重试策略" in text for text, _ in search.queries)
    finally:
        await manager.shutdown()
        resources.close()


# --- specs/009 US5 多跳两段式（T024）：findings 结论值拼进第二跳查询 ---


def finding(tool: str, target: str, digest: str) -> "Finding":
    """构造一条结论摘要；rows 取 0 只为满足签名。"""
    from flowpay.workflows.multi_agent.state import Finding

    return Finding(tool, target, 0, digest)


def test_second_hop_appends_sorted_record_ids() -> None:
    """第一跳查得的记录编号进第二跳查询：取有序集合并排序（审查 B4）。"""
    from flowpay.workflows.multi_agent.clarification import query_with_findings

    findings = [
        finding("sql", "payments id=pay_bg_001_0003", "3 行；首行 id=pay_bg_001_0003"),
        finding("sql", "events payment_id=pay_bg_001_0003", "2 行；首行 id=evt_bm_002_01"),
    ]
    assert query_with_findings(findings, "通知投递状态") == (
        "通知投递状态 evt_bm_002_01 pay_bg_001_0003"
    )


def test_second_hop_is_deterministic_over_finding_order() -> None:
    """同一 findings 集合不同顺序产出同一查询文本（恢复续跑不因顺序漂移重查）。"""
    from flowpay.workflows.multi_agent.clarification import query_with_findings

    base = [
        finding("sql", "payments id=pay_bm_030_02", "1 行"),
        finding("sql", "events id=evt_bg_003_01", "1 行"),
        finding("sql", "webhooks id=req_bm_030_01", "1 行"),
    ]
    first = query_with_findings(base, "投递结果")
    second = query_with_findings(list(reversed(base)), "投递结果")
    assert first == second
    # 重复编号只拼一次。
    dup = [*base, finding("sql", "again pay_bm_030_02", "1 行")]
    assert query_with_findings(dup, "投递结果") == first


def test_second_hop_without_findings_is_noop() -> None:
    """无第一跳结论时不拼接，行为不变（FR-10）。"""
    from flowpay.workflows.multi_agent.clarification import query_with_findings

    assert query_with_findings([], "Webhook 重试策略") == "Webhook 重试策略"
    # findings 里没有编号也一样。
    assert query_with_findings([finding("search_documents", "重试策略", "0 个片段")], "重试策略") == (
        "重试策略"
    )


def test_second_hop_value_capped_at_500_chars() -> None:
    """拼接值整体 ≤500 字符：截断到上限（沿用既有长度口径）。"""
    from flowpay.workflows.multi_agent.clarification import query_with_findings

    ids = [f"pay_aaaa_{i:04d}_9999" for i in range(100)]
    findings = [finding("sql", f"payments id={identifier}", "1 行") for identifier in ids]
    query = query_with_findings(findings, "通知状态")
    assert len(query) <= 500
    assert query.startswith("通知状态 ")


async def test_doc_search_second_hop_carries_findings(tmp_path: Path) -> None:
    """集成：第一跳的结论值进入第二轮的文档检索查询。

    第一轮 docs 调查员读回带业务编号的文档（Search.documents 注册 RULE-1
    之外再注册一份带编号的文档），read_document 的 findings 摘要携带该编号；
    第二轮 docs 任务检索时该编号拼进查询（specs/009 US5 两段式）。
    """
    model, search = Model(), Search()
    from flowpay.core.knowledge.contracts import Passage, PassageSource

    search.documents["DELIVERY-9"] = [
        Passage(
            id="doc:DELIVERY-9#c001",
            title="投递失败处理",
            text="支付 pay_bg_001_0003 投递失败时按重试策略处理。",
            score=0.9,
            source=PassageSource(
                collection_id="fixture",
                doc_id="DELIVERY-9",
                chunk_no=1,
                path="knowledge/delivery.md",
                sha256="fixture",
                locator={"start_char": 0},
                metadata={"doc_type": "troubleshooting"},
            ),
        )
    ]
    manager, resources = manager_fixture(tmp_path, model, search)
    model.responses = [
        completion(calls=[plan_call([{"kind": "docs", "objective": "查明投递失败的处理规则"}])]),
        completion(calls=[search_call("s1", "投递失败处理规则")]),
        completion(calls=[read_call("r1", "DELIVERY-9")]),
        grounded("规则结论。"),
        completion(calls=[response_call("answer", answer="规则已查明。")]),
        completion(calls=[intent_call("intent_2", "troubleshoot")]),
        completion(calls=[plan_call([{"kind": "docs", "objective": "查投递失败的后续处理"}])]),
        completion(calls=[search_call("s2", "投递失败后续处理")]),
        grounded("规则结论二。"),
        completion(calls=[response_call("answer", answer="已根据报告作答。")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", TROUBLESHOOT)
        await say(manager, sid, "second", "继续查投递失败的后续处理")
        assert search.queries
        assert any("pay_bg_001_0003" in text for text, _ in search.queries)
    finally:
        await manager.shutdown()
        resources.close()
