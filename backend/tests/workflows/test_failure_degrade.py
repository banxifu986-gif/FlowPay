"""specs/009 US1：分级失效处置与降级作答的受控回归。

断言外部可见行为（事件序列、报告正文、轮次终态），不锁内部调用次数；注入
确定性错误必须验证新行为——tool.outage 载荷 failure_class=deterministic、
后续调用不再发起请求、无 run.retry——防止用既有 error_report 路径"空跑通过"
（审查 C1 可证伪形态）。
"""

import json
from pathlib import Path
from typing import override

import pytest

from flowpay.core.event_log.types import Event
from flowpay.core.json import (
    JsonObject,
    require_list,
    require_object,
    require_text,
    to_json_object,
)
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.llm.chat.http_error import ModelHTTPError
from flowpay.core.llm.chat.messages import ModelOutputError
from flowpay.workflows.multi_agent.clarification import ClarificationRequest
from flowpay.workflows.multi_agent.investigation import (
    check_chain,
    guarded_call,
    resolve_response,
)
from flowpay.workflows.multi_agent.state import (
    SharedTurn,
    SubtaskProgress,
    WorkerReport,
    merge_task_progress,
    task_key,
)
from tests.core.principals import INTERNAL

from .fixtures import Model, Search, completion, hit, intent_call, search_call, tasks_call
from .test_clarification import response_call
from .test_multi_agent import make_manager

# R96B FP-BM-TV-0116 的真实嵌入 400 正文（SiliconFlow code=20015）：白名单锚点。
FOUND_PART = "重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]"
MISSING_PART = "退避规则未能核实：检索链路本轮禁用"
EMBEDDING_400 = ModelHTTPError(
    400, '{"code":20015,"message":"The parameter is invalid. Please check again."}'
)


def test_shared_turn_has_outage_and_progress_fields() -> None:
    """SharedTurn 新增三类字段：禁用登记、子任务断点、单任务工具计数（run 内单例）。"""
    shared = SharedTurn()
    assert shared.tool_outages == set()
    assert shared.task_progress == {}
    assert shared.task_tool_calls == {}


def test_task_key_is_worker_plus_objective() -> None:
    """幂等键 = worker 类型 + 任务目标：同任务同键，跨 worker 不同键。"""
    assert task_key("doc_worker", "查明重试策略") == task_key("doc_worker", "查明重试策略")
    assert task_key("sql_worker", "查明重试策略") != task_key("doc_worker", "查明重试策略")


def test_subtask_progress_round_trips_through_json() -> None:
    """断点实体经 JSON 往返不失真：恢复按载荷重建，键与字段保持。"""
    progress = SubtaskProgress(
        key=task_key("doc_worker", "查明重试策略"),
        worker="doc_worker",
        objective="查明重试策略",
        done_keys=["search:重试策略", "read:RULE-1"],
        findings=["1 个片段；首片 doc:RULE-1#c001"],
        evidence_refs=["doc:RULE-1#c001"],
        finished=True,
    )
    payload: JsonObject = to_json_object(progress)
    assert SubtaskProgress.restore(payload) == progress


def test_restore_rejects_malformed_progress_payload() -> None:
    """恢复按容忍口径：结构不合法的载荷按 None 处理，不让坏事件打断续跑。"""
    assert SubtaskProgress.restore(None) is None
    assert SubtaskProgress.restore("not-an-object") is None
    assert SubtaskProgress.restore({"worker": "doc_worker"}) is None  # 缺关键键


def test_merge_task_progress_keeps_finished_over_unfinished() -> None:
    """并行落库顺序无保证：已收束条目不被未收束同名条目覆盖（双向验证）。"""
    key = task_key("doc_worker", "查明重试策略")
    partial = SubtaskProgress(key, "doc_worker", "查明重试策略", ["search:重试策略"], [], [], False)
    done = SubtaskProgress(
        key,
        "doc_worker",
        "查明重试策略",
        ["search:重试策略", "read:RULE-1"],
        ["1 个片段；首片 doc:RULE-1#c001"],
        ["doc:RULE-1#c001"],
        True,
    )
    forward = merge_task_progress([partial, done])
    assert forward[key].finished is True
    assert forward[key].done_keys == ["search:重试策略", "read:RULE-1"]
    backward = merge_task_progress([done, partial])
    assert backward[key].finished is True
    assert backward[key].done_keys == ["search:重试策略", "read:RULE-1"]


def test_merge_task_progress_separate_keys_and_repeat_finished() -> None:
    """不同键互不覆盖；同名已收束条目以后写者为准（重放事件按序归一）。"""
    sql_key = task_key("sql_worker", "查明扣款状态")
    doc_key = task_key("doc_worker", "查明重试策略")
    first = SubtaskProgress(doc_key, "doc_worker", "查明重试策略", ["search:a"], [], [], True)
    second = SubtaskProgress(
        doc_key, "doc_worker", "查明重试策略", ["search:a", "read:B"], [], [], True
    )
    sql = SubtaskProgress(
        sql_key, "sql_worker", "查明扣款状态", ["payments account=1"], [], [], True
    )
    merged = merge_task_progress([first, second, sql])
    assert set(merged) == {doc_key, sql_key}
    assert merged[doc_key].done_keys == ["search:a", "read:B"]


class RecordingTracer:
    """记录事件的追踪替身：守卫单元测试不落库，只看事件序列（满足 EventSink 协议）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, JsonObject]] = []

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> Event:
        _ = call_id, parent_call_id
        self.events.append((kind, payload))
        return {
            "schema_version": 1,
            "event_id": "evt_fixture",
            "session_id": "s1",
            "seq": 0,
            "run_id": None,
            "call_id": None,
            "parent_call_id": None,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "type": kind,
            "payload": payload,
        }


def outage_payloads(events: list[tuple[str, JsonObject]]) -> list[JsonObject]:
    """提取 tool.outage 载荷；供"登记一次、后续不再重复登记"断言。"""
    return [payload for kind, payload in events if kind == "tool.outage"]


def test_check_chain_raises_only_when_disabled() -> None:
    """FR-2 禁用即收束：已禁用链路立即抛失败，未禁用链路放行。"""
    shared = SharedTurn()
    shared.tool_outages.add("embedding")
    try:
        check_chain(shared, "embedding")
        raise AssertionError("已禁用链路必须立即抛失败")
    except RuntimeError as error:
        assert "embedding" in str(error)
    check_chain(shared, "sql:payments")  # 未禁用链路不抛


async def test_guarded_call_registers_deterministic_failure_once() -> None:
    """确定性失败 → 登记禁用 + 发 tool.outage（failure_class=deterministic）→ 重抛原错误。"""
    shared = SharedTurn()
    tracer = RecordingTracer()

    async def broken() -> None:
        raise EMBEDDING_400

    try:
        await guarded_call(shared, tracer, "embedding", broken)
        raise AssertionError("确定性失败必须重抛")
    except ModelHTTPError as error:
        assert error is EMBEDDING_400  # 重抛原错误，交由既有 catch-all 收束
    assert shared.tool_outages == {"embedding"}
    payloads = outage_payloads(tracer.events)
    assert len(payloads) == 1
    assert require_text(payloads[0]["chain"]) == "embedding"
    assert require_text(payloads[0]["failure_class"]) == "deterministic"

    # 已登记后再次失败：任务照常失败，但禁用登记与事件不重复。
    async def broken_again() -> None:
        raise EMBEDDING_400

    try:
        await guarded_call(shared, tracer, "embedding", broken_again)
    except ModelHTTPError:
        pass
    assert len(outage_payloads(tracer.events)) == 1


async def test_guarded_call_passes_transient_failures_through() -> None:
    """瞬时失败（RuntimeError → other）不登记禁用、不发事件，仍重抛走既有收束。"""
    shared = SharedTurn()
    tracer = RecordingTracer()

    async def flaky() -> None:
        raise RuntimeError("search offline")

    try:
        await guarded_call(shared, tracer, "embedding", flaky)
        raise AssertionError("失败必须重抛")
    except RuntimeError:
        pass
    assert shared.tool_outages == set()
    assert outage_payloads(tracer.events) == []


async def test_guarded_call_returns_result_when_chain_healthy() -> None:
    """链路正常时守卫透传结果，不产生任何事件。"""
    shared = SharedTurn()
    tracer = RecordingTracer()

    async def healthy() -> str:
        return "ok"

    assert await guarded_call(shared, tracer, "embedding", healthy) == "ok"
    assert shared.tool_outages == set()
    assert tracer.events == []


class DeterministicSearch(Search):
    """嵌入链路注入结构性 400：请求已发起（knowledge.request 在案）后确定性拒绝。

    形态取自 R96B FP-BM-TV-0116：SiliconFlow 对查询文本返回 code=20015 结构性 400。
    """

    @override
    async def search(
        self,
        collection_id: str,
        text: str,
        limit: int = 5,
        filters: JsonObject | None = None,
    ) -> list[Passage]:
        assert collection_id == "fixture" and self.tracer is not None
        await self.tracer.emit("knowledge.request", {"query": text, "limit": limit})
        self.queries.append((text, limit))
        raise EMBEDDING_400


class FirstQueryOkSearch(DeterministicSearch):
    """首查正常、后续请求结构性 400：构造部分证据与链路禁用并存的场景。"""

    @override
    async def search(
        self,
        collection_id: str,
        text: str,
        limit: int = 5,
        filters: JsonObject | None = None,
    ) -> list[Passage]:
        assert collection_id == "fixture" and self.tracer is not None
        await self.tracer.emit("knowledge.request", {"query": text, "limit": limit})
        self.queries.append((text, limit))
        if len(self.queries) > 1:
            raise EMBEDDING_400
        return [hit()]


async def test_deterministic_outage_disables_chain_for_rest_of_turn(tmp_path: Path) -> None:
    """集成可证伪形态（SC-2）：注入嵌入 400 → tool.outage 登记 → 后续调用不再发起请求。

    断言新行为而非既有 error_report 兜底：tool.outage 载荷
    failure_class=deterministic、knowledge.request 只发起一次（第二个任务对
    已禁用链路零请求）、无 run.retry、整轮正常完成。
    """
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(
            calls=[
                tasks_call(
                    "t1",
                    [
                        {"kind": "docs", "objective": "查明重试策略"},
                        {"kind": "docs", "objective": "查明退避规则"},
                    ],
                )
            ]
        ),
        completion(calls=[search_call("s1", "重试策略")]),
        completion(calls=[search_call("s2", "退避规则")]),
        completion("已根据调查报告回答。\n\n[doc:RULE-1#c001]"),
    ]
    search = DeterministicSearch()
    manager, resources = make_manager(tmp_path, model, search, max_concurrency=1)
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        events = manager.store.read(sid)
        # ① tool.outage 恰一次，载荷 failure_class=deterministic + 链路标识。
        outages = [e for e in events if e["type"] == "tool.outage"]
        assert len(outages) == 1
        payload = require_object(outages[0]["payload"])
        assert require_text(payload["failure_class"]) == "deterministic"
        assert require_text(payload["chain"]) == "embedding"
        # ② 后续调用不再发起请求：knowledge.request 只发起一次。
        assert len([e for e in events if e["type"] == "knowledge.request"]) == 1
        # ③ 无 run.retry：确定性失败不烧重试预算。
        assert not [e for e in events if e["type"] == "run.retry"]
        # ④ 两个任务都按 error_report 收束（目标与失败说明进入汇总请求）。
        assert require_text(view["status"]) == "completed"
        content = json.dumps(model.requests[-1]["messages"], ensure_ascii=False)
        assert "查明重试策略" in content and "查明退避规则" in content
        assert "RuntimeError" in content  # 第二个任务的禁用即收束说明
    finally:
        await manager.shutdown()
        resources.close()


def _answer_call(answer: str) -> JsonObject:
    """构造一次 set_response 作答决策（test_clarification 的 response_call 同形）。"""
    return response_call("answer", answer=answer)


def test_resolve_response_partial_evidence_keeps_model_answer() -> None:
    """部分证据：作答决策原样落地（waiting=False），未证实表述由决策正文携带。"""
    shared = SharedTurn()
    reports = [
        WorkerReport("docs", "重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        WorkerReport("sql", "调查任务「查明扣款状态」未能完成：RuntimeError: offline。"),
    ]
    resolved = resolve_response(
        shared, reports, completion(calls=[_answer_call("扣款状态未能核实：查询链路本轮不可用。")])
    )
    assert resolved.waiting is False
    assert resolved.source == "model"
    assert "未能核实" in resolved.text


def test_resolve_response_all_chains_failed_still_answers_without_waiting() -> None:
    """全部工具失效：报告全是收束说明也按作答落地，不转等待、不转澄清。"""
    shared = SharedTurn()
    reports = [
        WorkerReport("sql", "调查任务「查明扣款」未能完成：RuntimeError: offline。"),
        WorkerReport("docs", "调查任务「查明规则」未能完成：RuntimeError: offline。"),
    ]
    answer = completion(calls=[_answer_call("两个子项均未能核实：调查链路本轮不可用。")])
    resolved = resolve_response(shared, reports, answer)
    assert resolved.waiting is False
    assert resolved.source == "model"


def test_resolve_response_empty_output_raises_without_pending() -> None:
    """无决策且无 pending：空正文按既有口径抛 ModelOutputError（FR-12 触发条件）。"""
    with pytest.raises(ModelOutputError):
        resolve_response(SharedTurn(), [], completion(None))


def test_resolve_response_pending_takes_clarify_priority_on_empty_output() -> None:
    """pending 存在时维持 clarify 优先回退：空输出不抛，按等待轮收束（2026-11-23 拍板）。"""
    shared = SharedTurn()
    shared.pending.append(ClarificationRequest(conditions=("account_id",), question="请提供账户"))
    resolved = resolve_response(shared, [], completion(None))
    assert resolved.waiting is True
    assert resolved.source == "fallback"
    assert resolved.mode == "clarify"


async def test_synthesize_empty_output_retries_once_then_answers(tmp_path: Path) -> None:
    """FR-12（SC-2）：synthesize 空输出 → model.retry 恰一次 → 重试后作答，无 run.failed。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion(None),  # synthesize 首次调用：无决策、空正文
        completion("已根据调查报告回答。"),
    ]
    manager, resources = make_manager(tmp_path, model, Search())
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        events = manager.store.read(sid)
        assert require_text(view["status"]) == "completed"  # 不产生 run.failed
        assert require_text(view["messages"][-1]["content"]) == "已根据调查报告回答。"
        retries = [e for e in events if e["type"] == "model.retry"]
        assert len(retries) == 1  # 确定性重试恰一次
        assert require_text(require_object(retries[0]["payload"])["error_type"]) == (
            "ModelOutputError"
        )
        assert not [e for e in events if e["type"] == "run.retry"]
    finally:
        await manager.shutdown()
        resources.close()


async def test_synthesize_double_empty_output_finishes_with_placeholder(tmp_path: Path) -> None:
    """FR-12（SC-3）：重试仍空 → 诚实占位完成（携带已查结论 + 未能核实），无 run.failed。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion(None),  # 首次空输出
        completion(None),  # 重试仍空
    ]
    manager, resources = make_manager(tmp_path, model, Search())
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        events = manager.store.read(sid)
        assert require_text(view["status"]) == "completed"  # 占位完成而非 run.failed
        answer = require_text(view["messages"][-1]["content"])
        assert "已查到的结论" in answer and "HTTP 410" in answer  # 已查部分照常作答
        assert "未能核实" in answer  # 统一未证实口径
        retries = [e for e in events if e["type"] == "model.retry"]
        assert len(retries) == 1
        decided = [e for e in events if e["type"] == "clarification.decided"]
        assert require_text(require_object(decided[-1]["payload"])["source"]) == "fallback"
        assert require_object(require_object(decided[-1]["payload"]))["mode"] == "answer"
    finally:
        await manager.shutdown()
        resources.close()


async def test_partial_evidence_answers_with_unverified_mark(tmp_path: Path) -> None:
    """部分证据 + 链路禁用（SC-3）：已查子项照常作答，失败子项标注未能核实。

    一个任务查到证据、另一个任务被确定性禁用收束：整轮正常完成、证据面板
    仍带已查片段、无 run.failed——降级作答契约的三来源之一（禁用收束）。
    """
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(
            calls=[
                tasks_call(
                    "t1",
                    [
                        {"kind": "docs", "objective": "查明重试策略"},
                        {"kind": "docs", "objective": "查明退避规则"},
                    ],
                )
            ]
        ),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion(calls=[search_call("s2", "退避规则")]),  # 第二个任务：禁用即收束
        completion(f"{FOUND_PART}；{MISSING_PART}。"),
    ]
    search = FirstQueryOkSearch()
    manager, resources = make_manager(tmp_path, model, search, max_concurrency=1)
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        events = manager.store.read(sid)
        assert require_text(view["status"]) == "completed"  # 不产生 run.failed
        answer = require_object(view["messages"][-1])
        assert "HTTP 410" in require_text(answer["content"])  # 已查子项照常作答
        assert "未能核实" in require_text(answer["content"])  # 失败子项统一口径
        assert require_list(answer.get("evidence") or [])  # 已查证据仍可展开
        assert len([e for e in events if e["type"] == "tool.outage"]) == 1
        assert not [e for e in events if e["type"] == "run.retry"]
    finally:
        await manager.shutdown()
        resources.close()
