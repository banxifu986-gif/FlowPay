"""multi-agent：意图路由、轻分支、并行调查、追问与跨轮状态的受控回归。"""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import cast, override

import pytest
from openai.types.chat import ChatCompletion

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.chat.types import SessionView
from flowpay.core.config import Settings
from flowpay.core.data_sources import DataSources
from flowpay.core.event_log import EventStore, EventStoreProtocol
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.dataset import dataset_path, snapshot_ready
from flowpay.workflows.multi_agent import MultiAgentWorkflow
from flowpay.workflows.multi_agent.state import Finding, WorkerReport
from tests.core.principals import INTERNAL

from .fixtures import (
    Model,
    Search,
    completion,
    conversation,
    empty_sources,
    grounded,
    intent_call,
    search_call,
    tasks_call,
)

ROOT = Path(__file__).resolve().parents[3]
DATASET = dataset_path() or Path("/nonexistent")
POLICY = ROOT / "backend/policies/sources.toml"


def make_manager(
    tmp_path: Path,
    model: Model,
    search: Search,
    max_concurrency: int | None = None,
    sources: DataSources | None = None,
    policy: ContextPolicy | None = None,
    investigation_tool_budget: int = 16,
) -> tuple[SessionManager, RuntimeResources]:
    """真实运行时装配 + 受控模型；与 test_manual_loop 同一套夹具口径。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, sources=sources, retriever=search.for_run)
    workflow = MultiAgentWorkflow(
        model,
        retrieval_input,
        policy,
        max_concurrency=max_concurrency,
        investigation_tool_budget=investigation_tool_budget,
    )
    manager = SessionManager(EventStore(tmp_path / "events.sqlite"), workflow, settings, resources)
    return manager, resources


def test_create_stage_forwards_investigation_tool_budget() -> None:
    """FR-9 配置接线：create_stage 把单任务预算上限传给 multi-agent 阶段。"""
    from flowpay.router import create_stage

    # Protocol 不暴露实现字段（test_agent_loop 同口径）：断言前收口实现类型。
    workflow = cast(
        MultiAgentWorkflow,
        create_stage("multi-agent", Model(), retrieval_input, investigation_tool_budget=7),
    )
    assert workflow.investigation_tool_budget == 7


async def say(manager: SessionManager, sid: str, request_id: str, text: str) -> None:
    """提交一轮用户消息并等它结束。"""
    await manager.submit_message(sid, request_id, text, INTERNAL)
    await manager.tasks[sid]


async def test_rule_short_circuit_needs_no_model_call(tmp_path: Path) -> None:
    """带业务编号的消息走规则短路：零分类调用，协调者直接拆解并汇总。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [conversation("没有需要调查的任务。"), grounded("排查分支的回答。")]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(
            sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看", INTERNAL
        )
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == (
            "排查分支的回答。\n\n[doc:RULE-1#c001]"
        )
        assert len(model.requests) == 2
        assert all(
            require_text(require_object(require_object(t)["function"])["name"]) != "set_intent"
            for request in model.requests
            for t in require_list(request["tools"])
        )
    finally:
        await manager.shutdown()
        resources.close()


async def test_chitchat_replies_without_any_tool(tmp_path: Path) -> None:
    """无编号消息分类为闲聊后直调模型作答：不检索、不提供工具（SC-2）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(calls=[intent_call("intent_1", "chitchat")]),
        conversation("你好！我可以帮你排查支付与 Webhook 问题。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "你好，你能做什么？", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert (
            require_text(view["messages"][-1]["content"])
            == "你好！我可以帮你排查支付与 Webhook 问题。"
        )
        assert search.queries == []
        assert len(model.requests) == 2
        definition = require_object(require_list(model.requests[0]["tools"])[0])
        assert require_object(definition["function"])["name"] == "set_intent"
        assert require_list(model.requests[1]["tools"]) == []
    finally:
        await manager.shutdown()
        resources.close()


async def test_doc_qa_answers_from_retrieved_evidence(tmp_path: Path) -> None:
    """无编号消息分类为文档问答后检索一次作答：命中片段作为证据随回答交给界面。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(calls=[intent_call("intent_1", "docs")]),
        grounded("HTTP 410 后禁用端点。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "Webhook 的重试策略是什么？", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert "HTTP 410 后禁用端点" in require_text(view["messages"][-1]["content"])
        assert len(search.queries) == 1
        assert len(model.requests) == 2
        evidence = require_list(require_object(view["messages"][-1]).get("evidence", []))
        assert [require_text(require_object(p)["id"]) for p in evidence] == ["doc:RULE-1#c001"]
    finally:
        await manager.shutdown()
        resources.close()


async def test_doc_qa_without_hits_admits_insufficiency(tmp_path: Path) -> None:
    """检索零命中时直接明示依据不足，不再消耗模型调用，也不编造引用。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [completion(calls=[intent_call("intent_1", "docs")])]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "Webhook 的重试策略是什么？missing", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert "依据" in require_text(view["messages"][-1]["content"])
        assert len(search.queries) == 1
        assert len(model.requests) == 1
    finally:
        await manager.shutdown()
        resources.close()


async def test_unreadable_intent_falls_back_to_troubleshoot(tmp_path: Path) -> None:
    """模型没有调用 set_intent 时兜底进排查：空任务直接汇总，仍给出回答。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        conversation("这是普通文本，没有调用工具。"),
        conversation("没有需要调查的任务。"),
        grounded("直接回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "谢谢", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == "直接回答。\n\n[doc:RULE-1#c001]"

    finally:
        await manager.shutdown()
        resources.close()


def clarify_call(identifier: str, question: str) -> JsonObject:
    """构造一次 set_response 的追问决策；汇总节点据此以等待状态结束本轮。"""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "set_response",
            "arguments": json.dumps({"mode": "clarify", "question": question}),
        },
    }


def ask_call(identifier: str, missing: list[str], question: str) -> JsonObject:
    """构造一次 ask_user 调用；参数仍是协议要求的 JSON 字符串。"""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "ask_user",
            "arguments": json.dumps({"missing": missing, "question": question}),
        },
    }


async def test_doc_worker_searches_and_reports_with_evidence(tmp_path: Path) -> None:
    """带编号消息进排查：协调者拆出文档任务，调查员检索后交报告，汇总带证据作答。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(
            calls=[tasks_call("plan_1", [{"kind": "docs", "objective": "查明 Webhook 重试策略"}])]
        ),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略是 HTTP 410 后禁用端点。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == (
            "已根据调查报告回答。\n\n[doc:RULE-1#c001]"
        )
        assert len(search.queries) == 1
        assert len(model.requests) == 4
        evidence = require_list(require_object(view["messages"][-1]).get("evidence", []))
        assert [require_text(require_object(p)["id"]) for p in evidence] == ["doc:RULE-1#c001"]
        findings = require_list(view["state"]["findings"])
        first = require_object(findings[0])
        assert require_text(first["tool"]) == "search_documents"
        assert "1 个片段" in require_text(first["digest"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_repeated_query_is_refused_and_uses_existing_results(tmp_path: Path) -> None:
    """同一轮两个相同查询只执行一次：重复发起被 claim 拒绝（SC-6）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search, max_concurrency=1)
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1",
                    [
                        {"kind": "docs", "objective": "查明重试策略"},
                        {"kind": "docs", "objective": "再查一次重试策略"},
                    ],
                )
            ]
        ),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("首查结论。"),
        completion(calls=[search_call("s2", "Webhook 重试策略")]),
        grounded("复用已有结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert len(search.queries) == 1
    finally:
        await manager.shutdown()
        resources.close()


class SlowSearch(Search):
    """检索前加固定延时，用于对照并行与串行的轮内耗时。"""

    @override
    async def search(
        self, collection_id: str, text: str, limit: int = 5, filters: JsonObject | None = None
    ):
        await asyncio.sleep(0.2)
        return await super().search(collection_id, text, limit, filters)


async def run_timed_turn(root: Path, max_concurrency: int | None) -> float:
    """跑一轮双文档任务调查，返回轮内耗时。"""
    model, search = Model(), SlowSearch()
    manager, resources = make_manager(root, model, search, max_concurrency)
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1",
                    [
                        {"kind": "docs", "objective": "查明重试策略"},
                        {"kind": "docs", "objective": "查明禁用规则"},
                    ],
                )
            ]
        ),
        completion(calls=[search_call("s1", "重试策略")]),
        grounded("结论一。"),
        completion(calls=[search_call("s2", "禁用规则")]),
        grounded("结论二。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        start = perf_counter()
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        elapsed = perf_counter() - start
        # 形状断言：两个任务目标不同，无论 FIFO 如何错位，两次检索都必须真的执行。
        assert len(search.queries) == 2
        return elapsed
    finally:
        await manager.shutdown()
        resources.close()


async def test_parallel_workers_finish_no_later_than_serial(tmp_path: Path) -> None:
    """两个调查任务并行执行的轮内耗时不高于串行（SC-3）。

    单次计时在并发负载下会受调度抖动影响；三轮交替取中位数，仍比较同一实现
    的并行/串行形态，不靠放宽到「慢一点也算过」来掩盖退化。
    """
    serial: list[float] = []
    parallel: list[float] = []
    for round_no in range(3):
        serial.append(await run_timed_turn(tmp_path / f"serial-{round_no}", 1))
        parallel.append(await run_timed_turn(tmp_path / f"parallel-{round_no}", None))

    def median(values: list[float]) -> float:
        return sorted(values)[len(values) // 2]

    assert median(parallel) < median(serial), (serial, parallel)


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_insufficient_conditions_ask_user_then_wait(tmp_path: Path) -> None:
    """条件不足时调查员追问缺项，本轮以 waiting_user 结束（FR-7）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(
        tmp_path, model, search, sources=DataSources.from_config(POLICY, DATASET)
    )
    model.responses = [
        completion(
            calls=[tasks_call("plan_1", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_1", ["account_id", "environment"], "请提供账户与环境")]),
        completion(calls=[clarify_call("resp_1", "请提供账户与环境")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        content = require_text(view["messages"][-1]["content"])
        # 007 FR-4：等待轮正文由系统拼出摘要 + 缺项 + 模型问题，不再只是问题原文。
        assert "请提供账户与环境" in content
        assert "尚未查到结论" in content

    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_supplied_conditions_are_not_asked_again_after_waiting(tmp_path: Path) -> None:
    """等待用户补充条件后继续：已提供的字段不重复索要，简报携带上一轮条件（FR-7）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(
        tmp_path, model, search, sources=DataSources.from_config(POLICY, DATASET)
    )
    model.responses = [
        completion(
            calls=[tasks_call("plan_1", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_1", ["account_id", "environment"], "请提供账户与环境")]),
        completion(calls=[clarify_call("resp_1", "请提供账户与环境")]),
        completion(
            calls=[tasks_call("plan_2", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_2", ["account_id", "environment"], "再次索要条件")]),
        grounded("条件已齐，继续调查并给出结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        waiting = await manager.get_session(sid, INTERNAL)
        assert require_text(waiting["status"]) == "waiting_user"
        await say(manager, sid, "second", "生产环境，账户 acct_bg_001")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        # 第二轮调查员的第一次请求带上一轮的 payment_id；重复索要被退回时
        # 提示"用户已经提供"，随后本轮仍以正常回答结束，而不是再次等待。
        assert "pay_bg_001_0003" in str(model.requests[3]["messages"])
        assert "不要重复索要" in str(model.requests[4]["messages"])
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_adopted_clarify_blocks_condition_reask_next_round(tmp_path: Path) -> None:
    """采纳过的追问记入已问条件：下一轮同类重复索要被拦截，改为作答并标注（SC-3）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(
        tmp_path, model, search, sources=DataSources.from_config(POLICY, DATASET)
    )
    model.responses = [
        completion(
            calls=[tasks_call("plan_1", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_1", ["account_id", "environment"], "请提供账户与环境")]),
        completion(calls=[clarify_call("resp_1", "请提供账户与环境")]),
        # 第二轮首条消息不命中规则短路：先给意图分类，再进排查分支。
        completion(calls=[intent_call("i_2", "troubleshoot")]),
        completion(
            calls=[tasks_call("plan_2", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_2", ["account_id", "environment"], "再次索要条件")]),
        grounded("无法继续查询，基于已有文档结论回答并标注未确认。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        waiting = await manager.get_session(sid, INTERNAL)
        assert require_text(waiting["status"]) == "waiting_user"
        clarification = require_object(require_object(waiting["state"])["clarification"])
        assert require_list(clarification["asked_conditions"]) == ["account_id", "environment"]
        await say(manager, sid, "second", "我再看看，稍后补充")
        view = await manager.get_session(sid, INTERNAL)
        # 第二轮重复索要被整体退回：回填说明指出已问过，本轮不再以等待结束。
        assert "已问过" in str(model.requests[6]["messages"])
        assert require_text(view["status"]) == "completed"
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_pending_question_only_stops_the_asking_worker(tmp_path: Path) -> None:
    """一个调查员发起追问后只有它自己收束：其余调查员把能查的查完再进汇总（007 FR-3）。

    specs/007 的"先查后问"要求文档调查不被缺账户阻塞；本轮仍以 waiting_user 结束，
    但文档任务照常执行，汇总节点拿到两边的报告。
    """
    model, search = Model(), Search()
    manager, resources = make_manager(
        tmp_path,
        model,
        search,
        max_concurrency=1,
        sources=DataSources.from_config(POLICY, DATASET),
    )
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1",
                    [
                        {"kind": "sql", "objective": "查这笔支付的通知事件"},
                        {"kind": "docs", "objective": "查明重试策略"},
                    ],
                )
            ]
        ),
        completion(calls=[ask_call("ask_1", ["account_id", "environment"], "请提供账户与环境")]),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("重试策略结论。"),
        completion(calls=[clarify_call("resp_1", "请提供账户与环境")]),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "waiting_user"
        assert len(search.queries) == 1
    finally:
        await manager.shutdown()
        resources.close()


async def test_conditions_survive_intent_switch(tmp_path: Path) -> None:
    """排查→闲聊→排查：已知条件跨轮保留且注入后续调查简报（SC-5）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(calls=[tasks_call("plan_1", [])]),
        grounded("已记录条件。"),
        completion(calls=[intent_call("intent_1", "chitchat")]),
        conversation("不客气。"),
        completion(calls=[tasks_call("plan_2", [{"kind": "docs", "objective": "查明重试策略"}])]),
        grounded("文档结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "生产环境，账户 acct_bg_001，支付 pay_bg_001_0003")
        await say(manager, sid, "second", "谢谢")
        await say(manager, sid, "third", "acct_bg_001 这里，顺便查查重试策略的文档")
        view = await manager.get_session(sid, INTERNAL)
        known = require_object(require_object(view["state"]["case"])["known"])
        assert set(known) == {"environment", "account_id", "payment_id"}
        # 第三轮调查员（requests[5]，requests[4] 是协调者）的请求里带简报：
        # 已知条件逐条列全，明确不重复索要。
        worker_request = str(model.requests[5]["messages"])
        assert "acct_bg_001" in worker_request and "不要再次索要" in worker_request
    finally:
        await manager.shutdown()
        resources.close()


async def test_synthesize_disputes_invented_ids(tmp_path: Path) -> None:
    """汇总回答里出现工具从未返回过的记录编号时回传纠正并重试一次（SC-4）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(calls=[tasks_call("plan_1", [])]),
        grounded("查到 pay_bg_0099_7777 已成功，依据充分。"),
        grounded("修正后的结论。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == (
            "修正后的结论。\n\n[doc:RULE-1#c001]"
        )

        correction = str(model.requests[2]["messages"])
        assert "只引用工具实际返回的记录" in correction and "pay_bg_0099_7777" in correction
    finally:
        await manager.shutdown()
        resources.close()


async def test_environment_word_does_not_short_circuit(tmp_path: Path) -> None:
    """带"生产环境"字样的文档问题不进排查短路，仍走 LLM 分类与文档问答（FR-2/FR-9）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [
        completion(calls=[intent_call("intent_1", "docs")]),
        grounded("HTTP 410 后禁用端点。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "生产环境的 Webhook 重试策略是什么？")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        definition = require_object(require_list(model.requests[0]["tools"])[0])
        assert require_object(definition["function"])["name"] == "set_intent"
        assert len(search.queries) == 1
    finally:
        await manager.shutdown()
        resources.close()


async def test_plan_task_list_is_capped(tmp_path: Path) -> None:
    """协调者拆解超过 4 个子任务时参数校验失败，按空任务兜底直接汇总。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    tasks: list[JsonObject] = [{"kind": "docs", "objective": f"任务{i}"} for i in range(5)]
    model.responses = [completion(calls=[tasks_call("plan_1", tasks)]), grounded("直接回答。")]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        assert len(model.requests) == 2
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_condition_revision_discards_stale_findings(tmp_path: Path) -> None:
    """改口换账户后：旧账户的查询结论被作废，简报不再列出，同条件可重查（US4）。"""
    model, search = Model(), Search()
    manager, resources = make_manager(
        tmp_path, model, search, sources=DataSources.from_config(POLICY, DATASET)
    )
    sql_task: list[JsonObject] = [{"kind": "sql", "objective": "查这笔支付的状态"}]
    model.responses = [
        completion(calls=[tasks_call("plan_1", sql_task)]),
        completion(
            calls=[
                {
                    "id": "pay_1",
                    "type": "function",
                    "function": {
                        "name": "query_payments",
                        "arguments": json.dumps(
                            {
                                "account_id": "acct_bg_001",
                                "environment": "production",
                                "payment_id": "pay_bg_001_0003",
                            }
                        ),
                    },
                }
            ]
        ),
        grounded("bg_001 报告。"),
        grounded("已根据调查报告回答。"),
        completion(calls=[tasks_call("plan_2", sql_task)]),
        completion(
            calls=[
                {
                    "id": "pay_2",
                    "type": "function",
                    "function": {
                        "name": "query_payments",
                        "arguments": json.dumps(
                            {
                                "account_id": "acct_bm_030",
                                "environment": "production",
                                "payment_id": "pay_bg_001_0003",
                            }
                        ),
                    },
                }
            ]
        ),
        grounded("bm_030 报告。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "生产环境 acct_bg_001 支付 pay_bg_001_0003 没收到通知")
        first = await manager.get_session(sid, INTERNAL)
        findings = require_list(first["state"]["findings"])
        assert any("acct_bg_001" in require_text(require_object(f)["target"]) for f in findings)
        await say(manager, sid, "second", "说错了，是 acct_bm_030")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        findings = require_list(view["state"]["findings"])
        assert findings and all(
            "acct_bg_001" not in require_text(require_object(f)["target"]) for f in findings
        )
        # 第二轮调查员的简报里不再出现旧账户的结论行；改口说明本身仍可提旧值。
        second_turn = "\n".join(str(request["messages"]) for request in model.requests[4:])
        assert "query_payments account_id=acct_bg_001" not in second_turn
    finally:
        await manager.shutdown()
        resources.close()


# ---- specs/003 P1（T015）：子 agent 输出预算与失败证据 ----

USAGE: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

GUARD = ContextPolicy(
    char_budget=100000,
    tool_result_budget=100000,
    report_char_budget=100,
)


async def run_solo_troubleshoot(
    tmp_path: Path, model: Model, search: Search, policy: ContextPolicy | None = None
) -> SessionView:
    """单 doc 任务排查轮（不经数据源），返回会话视图。"""
    manager, resources = make_manager(tmp_path, model, search, policy=policy)
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "帮我看看这笔支付为什么没收到通知")
        return await manager.get_session(sid, INTERNAL)
    finally:
        await manager.shutdown()
        resources.close()


async def test_worker_failure_becomes_error_report(tmp_path: Path) -> None:
    """调查者内部故障：产出含任务目标与安全失败文案的错误报告，本轮照常作答（FR-10）。

    提示词卫生（CLAUDE.md）：原始异常的类型与文本只进服务端日志，不进报告、
    不进模型请求、不进用户可见正文——报告只携带安全失败文案（classify_failure）。
    """
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "重试策略")], usage=USAGE),
        grounded("已根据调查报告回答。"),
    ]
    search = Search()
    search.fail = True
    view = await run_solo_troubleshoot(tmp_path, model, search)
    assert require_text(view["status"]) == "completed"  # 故障不让整轮失败
    # 错误报告进入 synthesize 请求：任务目标与安全失败文案在，原始异常不在。
    content = json.dumps(model.requests[-1]["messages"], ensure_ascii=False)
    assert "RuntimeError" not in content and "search offline" not in content
    assert "查明重试策略" in content
    assert "未能完成" in content and "记录已保留" in content


async def test_worker_report_clipped_to_policy_budget(tmp_path: Path) -> None:
    """报告产出端按 policy 预算截断并携带标记（FR-9）。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "重试策略")], usage=USAGE),
        completion("长" * 500, usage=USAGE),
        grounded("已根据调查报告回答。"),
    ]
    view = await run_solo_troubleshoot(tmp_path, model, Search(), policy=GUARD)
    assert require_text(view["status"]) == "completed"
    content = json.dumps(model.requests[-1]["messages"], ensure_ascii=False)
    assert "已截断 400 字符" in content  # 500 → 100 产出端截断
    assert "长" * 500 not in content


async def test_budget_exhausted_worker_reports_findings_slice(tmp_path: Path) -> None:
    """预算耗尽无报告：按开工基线切片产出预算说明报告（FR-10）。"""
    from flowpay.core.agent.budget import Budget
    from flowpay.core.agent.trace import Tracer
    from flowpay.core.agent.turn import AgentTurn
    from flowpay.core.execution.contracts import TurnInput
    from flowpay.core.execution.runtime import AgentRuntime
    from flowpay.workflows.multi_agent.investigation import budget_report, investigate
    from flowpay.workflows.multi_agent.state import Finding, SharedTurn

    store = EventStore(tmp_path / "events.sqlite")
    runtime = AgentRuntime(Tracer(store, "s1", "r1"))
    shared = SharedTurn(
        budget=Budget(0, 8),
        findings=[
            Finding("sql", "payments account=acct_bg_001", 3, "3 行；首行 id=pay_bg_001_0003")
        ],
    )
    agent = AgentTurn.start(
        TurnInput("调查任务：查明重试策略", [], {}),
        runtime,
        "系统提示",
        Model(),
        shared.budget,
        remember=False,
    )
    result = await investigate(agent, shared, "docs", "调查任务：查明重试策略", None)
    reports = cast("list[WorkerReport]", result["reports"])
    assert len(reports) == 1
    assert "预算" in reports[0].text
    # 切片语义直测：基线之后的 findings 进入说明，基线之前的不重复罗列。
    sliced = budget_report(shared, "docs", baseline=0, budget=0)
    assert "acct_bg_001" in sliced.text
    beyond = budget_report(shared, "docs", baseline=1, budget=0)
    assert "acct_bg_001" not in beyond.text


async def test_cancellation_passes_through_investigate(tmp_path: Path) -> None:
    """用户取消（CancelledError）不被失败报告吞掉，照常穿透（FR-10）。"""
    from flowpay.core.agent.budget import Budget
    from flowpay.core.agent.trace import Tracer
    from flowpay.core.agent.turn import AgentTurn
    from flowpay.core.execution.contracts import TurnInput
    from flowpay.core.execution.runtime import AgentRuntime
    from flowpay.workflows.multi_agent.investigation import investigate
    from flowpay.workflows.multi_agent.state import SharedTurn

    store = EventStore(tmp_path / "events.sqlite")
    runtime = AgentRuntime(Tracer(store, "s1", "r1"))
    shared = SharedTurn(budget=Budget(4, 4))

    class CancelModel(Model):
        """invoke 直接取消的受控模型：驱动取消穿透路径（specs/003 FR-10）。"""

        @override
        async def invoke(self, request: JsonObject, emit: ChunkSink) -> ChatCompletion:
            _ = request, emit
            raise asyncio.CancelledError()

    agent = AgentTurn.start(
        TurnInput("调查任务：查明重试策略", [], {}),
        runtime,
        "系统提示",
        CancelModel(),
        shared.budget,
        remember=False,
    )
    with pytest.raises(asyncio.CancelledError):
        await investigate(agent, shared, "docs", "调查任务：查明重试策略", None)


# ---- specs/003 P2（T018）：会话内记忆治理 ----


def test_merge_old_findings_caps_and_preserves_stale_matching() -> None:
    """超上限时最旧合并为一条：target 拼接保 discard_stale 子串、rows 求和、digest 截断。"""
    from flowpay.workflows.multi_agent.state import FINDINGS_CAP, merge_old_findings

    findings = [
        Finding("sql", f"payments account=acct_{i:03d}", i, f"{i} 行；首行 id=pay_{i:04d}")
        for i in range(25)
    ]
    merged = merge_old_findings(findings, cap=FINDINGS_CAP)
    assert len(merged) == 20
    head = merged[0]
    assert head.tool == "merged"
    # target 拼接保留全部被合并成员 → discard_stale 的子串匹配仍命中（FR-11）。
    assert "acct_000" in head.target and "acct_005" in head.target
    assert "acct_006" not in head.target  # 未合并成员不混入
    assert head.rows == sum(range(6))  # 最旧 25-20+1=6 条合并，rows 求和
    assert len(head.digest) <= 2000  # digest 拼接截断
    # 其余成员原样保留、顺序不变。
    kept_targets = [f"payments account=acct_{i:03d}" for i in range(6, 25)]
    assert [f.target for f in merged[1:]] == kept_targets


def test_merge_old_findings_noop_under_cap() -> None:
    """不超上限时原样返回（新列表），不做任何合并。"""
    from flowpay.workflows.multi_agent.state import merge_old_findings

    findings = [
        Finding("sql", "payments account=acct_bg_001", 1, "1 行"),
        Finding("docs", "重试策略", 2, "2 个片段"),
    ]
    merged = merge_old_findings(findings, cap=20)
    assert merged == findings and merged is not findings


# ---- specs/009 US4（T021）：plan 校验三路径（改派/重计划/丢弃）----


def reassigned_events(store: EventStoreProtocol, sid: str) -> list[JsonObject]:
    """读取会话事件里的 task.reassigned 载荷列表。"""
    return [
        require_object(event["payload"])
        for event in store.read(sid)
        if event["type"] == "task.reassigned"
    ]


def dropped_events(store: EventStoreProtocol, sid: str) -> list[JsonObject]:
    """读取会话事件里的 task.dropped 载荷列表。"""
    return [
        require_object(event["payload"])
        for event in store.read(sid)
        if event["type"] == "task.dropped"
    ]


async def test_plan_reassigns_mismatched_task(tmp_path: Path) -> None:
    """路径一：目标写明业务记录但 kind=docs——改派后任务正常完成（FR-8）。

    重计划响应为空任务（模型未按协议重拆）：沿用改派列表兜底，不失败整轮。
    """
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search, sources=empty_sources(tmp_path))
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1", [{"kind": "docs", "objective": "查支付 pay_bg_001_0003 的状态"}]
                )
            ]
        ),
        conversation("没有需要调查的任务。"),  # 重计划响应：空任务 → 兜底走改派列表
        conversation("业务记录查询需要数据源，未配置时以报告收束。"),  # sql 调查员回复
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        events = reassigned_events(manager.store, sid)
        assert len(events) == 1
        assert require_text(events[0]["objective"]) == "查支付 pay_bg_001_0003 的状态"
        assert require_text(events[0]["from"]) == "docs_worker"
        assert require_text(events[0]["to"]) == "sql_worker"
        assert dropped_events(manager.store, sid) == []
    finally:
        await manager.shutdown()
        resources.close()


async def test_plan_replan_produces_correct_kinds(tmp_path: Path) -> None:
    """路径二：重计划一次后模型给出正确 kind——采用重计划列表并正常完成。"""
    model, search = Model(), Search()
    # max_concurrency=1 串行化 Send：受控响应按任务分发顺序对齐，不靠并发时序。
    manager, resources = make_manager(
        tmp_path, model, search, sources=empty_sources(tmp_path), max_concurrency=1
    )
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1", [{"kind": "docs", "objective": "查支付 pay_bg_001_0003 的状态"}]
                )
            ]
        ),
        completion(
            calls=[
                tasks_call(
                    "plan_2",
                    [
                        {"kind": "sql", "objective": "查支付 pay_bg_001_0003 的状态"},
                        {"kind": "docs", "objective": "查平台文档里 Webhook 重试策略的规则"},
                    ],
                )
            ]
        ),
        grounded("业务结论。"),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("规则结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        # 纠错提示确实带上了原指派与正确方向。
        correction = json.dumps(model.requests[1]["messages"], ensure_ascii=False)
        assert "docs_worker" in correction and "sql_worker" in correction
        # 重计划列表被采用：sql 与 docs 两个任务都真的执行（检索命中一次）。
        assert len(search.queries) == 1
        assert dropped_events(manager.store, sid) == []
    finally:
        await manager.shutdown()
        resources.close()


async def test_plan_drops_unmatched_after_replan(tmp_path: Path) -> None:
    """路径三：重计划后仍不匹配——丢弃并记 task.dropped，其余任务正常完成。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search, sources=empty_sources(tmp_path))
    model.responses = [
        completion(
            calls=[
                tasks_call(
                    "plan_1",
                    [
                        {"kind": "docs", "objective": "查支付 pay_bg_001_0003 的状态"},
                        {"kind": "docs", "objective": "查平台文档里 Webhook 重试策略的规则"},
                    ],
                )
            ]
        ),
        completion(
            calls=[
                tasks_call(
                    "plan_2",
                    [
                        {"kind": "docs", "objective": "查支付 pay_bg_001_0003 的状态"},
                        {"kind": "docs", "objective": "查平台文档里 Webhook 重试策略的规则"},
                    ],
                )
            ]
        ),
        completion(calls=[search_call("s1", "Webhook 重试策略")]),
        grounded("规则结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        # 首轮两个任务都改派（docs→sql 与 docs→docs 维持），重计划仍不匹配的丢弃。
        events = reassigned_events(manager.store, sid)
        assert len(events) == 1
        assert require_text(events[0]["objective"]) == "查支付 pay_bg_001_0003 的状态"
        dropped = dropped_events(manager.store, sid)
        assert len(dropped) == 1
        assert require_text(dropped[0]["objective"]) == "查支付 pay_bg_001_0003 的状态"
        # 其余任务正常完成：规则任务真的执行了检索。
        assert len(search.queries) == 1
    finally:
        await manager.shutdown()
        resources.close()
