"""multi-agent：意图路由、轻分支、并行调查、追问与跨轮状态的受控回归。"""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import override

import pytest

from flowpay.core.config import Settings
from flowpay.core.data_sources import DataSources
from flowpay.core.event_log import EventStore
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.dataset import dataset_path, snapshot_ready
from flowpay.workflows.multi_agent import MultiAgentWorkflow

from .fixtures import (
    Model,
    Search,
    completion,
    conversation,
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
) -> tuple[SessionManager, RuntimeResources]:
    """真实运行时装配 + 受控模型；与 test_manual_loop 同一套夹具口径。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, sources=sources, retriever=search.for_run)
    workflow = MultiAgentWorkflow(model, retrieval_input, max_concurrency=max_concurrency)
    manager = SessionManager(EventStore(tmp_path / "events.sqlite"), workflow, settings, resources)
    return manager, resources


async def say(manager: SessionManager, sid: str, request_id: str, text: str) -> None:
    """提交一轮用户消息并等它结束。"""
    await manager.submit_message(sid, request_id, text)
    await manager.tasks[sid]


async def test_rule_short_circuit_needs_no_model_call(tmp_path: Path) -> None:
    """带业务编号的消息走规则短路：零分类调用，协调者直接拆解并汇总。"""
    model, search = Model(), Search()
    manager, resources = make_manager(tmp_path, model, search)
    model.responses = [conversation("没有需要调查的任务。"), grounded("排查分支的回答。")]
    try:
        sid = require_text((await manager.create_session())["id"])
        await manager.submit_message(sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        await manager.tasks[sid]
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await manager.submit_message(sid, "first", "你好，你能做什么？")
        await manager.tasks[sid]
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await manager.submit_message(sid, "first", "Webhook 的重试策略是什么？")
        await manager.tasks[sid]
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await manager.submit_message(sid, "first", "Webhook 的重试策略是什么？missing")
        await manager.tasks[sid]
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await manager.submit_message(sid, "first", "谢谢")
        await manager.tasks[sid]
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == "直接回答。\n\n[doc:RULE-1#c001]"
        assert len(model.requests) == 3
    finally:
        await manager.shutdown()
        resources.close()


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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，重试策略是什么")
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
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
    """两个调查任务并行执行的轮内耗时不高于串行（SC-3）。"""
    serial = await run_timed_turn(tmp_path / "serial", 1)
    parallel = await run_timed_turn(tmp_path / "parallel", None)
    assert parallel < serial


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
    ]
    try:
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "waiting_user"
        assert require_text(view["messages"][-1]["content"]) == "请提供账户与环境"
        assert len(model.requests) == 3
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
        completion(
            calls=[tasks_call("plan_2", [{"kind": "sql", "objective": "查这笔支付的通知事件"}])]
        ),
        completion(calls=[ask_call("ask_2", ["account_id", "environment"], "再次索要条件")]),
        grounded("条件已齐，继续调查并给出结论。"),
        grounded("已根据调查报告回答。"),
    ]
    try:
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        waiting = await manager.get_session(sid)
        assert require_text(waiting["status"]) == "waiting_user"
        await say(manager, sid, "second", "生产环境，账户 acct_bg_001")
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "completed"
        # 第二轮调查员的第一次请求带上一轮的 payment_id；重复索要被退回时
        # 提示"用户已经提供"，随后本轮仍以正常回答结束，而不是再次等待。
        assert "pay_bg_001_0003" in str(model.requests[3]["messages"])
        assert "不要重复索要" in str(model.requests[4]["messages"])
    finally:
        await manager.shutdown()
        resources.close()


@pytest.mark.skipif(not snapshot_ready(), reason="business dataset snapshot is not available")
async def test_pending_question_stops_the_other_worker(tmp_path: Path) -> None:
    """一个调查员发起追问后其余调查员自然收束，本轮以追问结束（R5）。"""
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
    ]
    try:
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "waiting_user"
        assert require_text(view["messages"][-1]["content"]) == "请提供账户与环境"
        # 只有协调者和发起追问的调查员消耗了模型调用，另一个调查员没有开工。
        assert len(model.requests) == 2
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "生产环境，账户 acct_bg_001，支付 pay_bg_001_0003")
        await say(manager, sid, "second", "谢谢")
        await say(manager, sid, "third", "acct_bg_001 这里，顺便查查重试策略的文档")
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "completed"
        assert require_text(view["messages"][-1]["content"]) == (
            "修正后的结论。\n\n[doc:RULE-1#c001]"
        )
        assert len(model.requests) == 3
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "生产环境的 Webhook 重试策略是什么？")
        view = await manager.get_session(sid)
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "支付 pay_bg_001_0003 一直没收到通知，帮我看看")
        view = await manager.get_session(sid)
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
                                "filters": {"payment_id": "pay_bg_001_0003"},
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
                                "filters": {"payment_id": "pay_bg_001_0003"},
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
        sid = require_text((await manager.create_session())["id"])
        await say(manager, sid, "first", "生产环境 acct_bg_001 支付 pay_bg_001_0003 没收到通知")
        first = await manager.get_session(sid)
        findings = require_list(first["state"]["findings"])
        assert any("acct_bg_001" in require_text(require_object(f)["target"]) for f in findings)
        await say(manager, sid, "second", "说错了，是 acct_bm_030")
        view = await manager.get_session(sid)
        assert require_text(view["status"]) == "completed"
        findings = require_list(view["state"]["findings"])
        assert findings and all(
            "acct_bg_001" not in require_text(require_object(f)["target"]) for f in findings
        )
        # 第二轮调查员的简报里不再出现旧账户的结论行。
        assert "acct_bg_001" not in str(model.requests[5]["messages"])
    finally:
        await manager.shutdown()
        resources.close()
