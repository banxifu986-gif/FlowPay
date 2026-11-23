"""specs/009 US1：分级失效处置与降级作答的受控回归。

断言外部可见行为（事件序列、报告正文、轮次终态），不锁内部调用次数；注入
确定性错误必须验证新行为——tool.outage 载荷 failure_class=deterministic、
后续调用不再发起请求、无 run.retry——防止用既有 error_report 路径"空跑通过"
（审查 C1 可证伪形态）。
"""

import json
from pathlib import Path
from typing import cast, override

import pytest

from flowpay.core.event_log.types import Event
from flowpay.core.execution.manager import merge_progress_payloads
from flowpay.core.json import (
    JsonObject,
    require_int,
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
    claim,
    guarded_call,
    resolve_response,
)
from flowpay.workflows.multi_agent.state import (
    SharedTurn,
    SubtaskProgress,
    WorkerReport,
    task_key,
)
from tests.core.principals import INTERNAL, MERCHANT

from .fixtures import (
    Model,
    Search,
    completion,
    conversation,
    hit,
    intent_call,
    search_call,
    tasks_call,
)
from .test_clarification import response_call
from .test_multi_agent import make_manager

# R96B FP-BM-TV-0116 的真实嵌入 400 正文（SiliconFlow code=20015）：白名单锚点。
FOUND_PART = "重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]"
MISSING_PART = "退避规则未能核实：检索链路本轮禁用"
EMBEDDING_400 = ModelHTTPError(
    400, '{"code":20015,"message":"The parameter is invalid. Please check again."}'
)


def test_shared_turn_has_outage_and_progress_fields() -> None:
    """SharedTurn 新增四类字段：禁用登记、子任务断点、单任务工具计数与上限（run 内单例）。"""
    shared = SharedTurn()
    assert shared.tool_outages == set()
    assert shared.task_progress == {}
    assert shared.task_tool_calls == {}
    assert shared.investigation_tool_budget == 16


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


def test_merge_progress_payloads_keeps_finished_over_unfinished() -> None:
    """并行落库顺序无保证：已收束条目不被未收束同名条目覆盖（双向验证）。

    合并裁决在 manager 层（core 不解析 workflow 实体），以原始 JSON 的
    key/finished 字段判定（specs/009 FR-4；审查 I2 收敛双实现后此处测生产实现）。
    """
    key = task_key("doc_worker", "查明重试策略")
    partial: JsonObject = {"key": key, "finished": False, "done_keys": ["search:重试策略"]}
    done: JsonObject = {
        "key": key,
        "finished": True,
        "done_keys": ["search:重试策略", "read:RULE-1"],
    }
    forward = merge_progress_payloads([partial, done])
    assert forward == [done]
    backward = merge_progress_payloads([done, partial])
    assert backward == [done]


def test_merge_progress_payloads_separate_keys_and_repeat_finished() -> None:
    """不同键互不覆盖；同名已收束条目以后写者为准（重放事件按序归一）。"""
    doc_key = task_key("doc_worker", "查明重试策略")
    sql_key = task_key("sql_worker", "查明扣款状态")
    first: JsonObject = {"key": doc_key, "finished": True, "done_keys": ["search:a"]}
    second: JsonObject = {
        "key": doc_key,
        "finished": True,
        "done_keys": ["search:a", "read:B"],
    }
    sql: JsonObject = {"key": sql_key, "finished": True, "done_keys": ["payments account=1"]}
    merged = merge_progress_payloads([first, second, sql])
    assert merged == [second, sql]


def test_claim_double_books_task_tool_calls() -> None:
    """FR-9 双记账：task_key 非空时该任务计数随 claim 递增；去重命中不重复记账。"""
    shared = SharedTurn()
    key = task_key("doc_worker", "查明重试策略")
    assert claim(shared, "search", "重试策略", key) is None
    assert claim(shared, "search", "退避规则", key) is None
    assert shared.task_tool_calls == {key: 2}
    assert claim(shared, "search", "重试策略", key) is not None  # 去重命中，不计入
    assert shared.task_tool_calls[key] == 2
    assert claim(shared, "search", "口径问题") is None  # 不带任务键的 claim 不双记账
    assert shared.task_tool_calls[key] == 2


async def test_task_budget_collapse_carries_findings_and_keeps_global_budget(
    tmp_path: Path,
) -> None:
    """FR-9 集成（SC-3 预算收束来源）：单任务达上限即收束——携带已查发现、发事件、落断点。

    两任务上限=2：各自执行 2 次检索后收束（task.budget_exhausted 载荷 calls=2、
    limit=2、键各归各任务）；knowledge.request 共 4 次——全局预算（12）远未触顶，
    证收束只对单任务生效；两任务断点 finished=True（收束即断点）；整轮 completed。
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
        completion(calls=[search_call("s2", "重试策略二")]),
        completion(calls=[search_call("s3", "退避规则")]),
        completion(calls=[search_call("s4", "退避规则二")]),
        completion("已根据调查报告回答。"),
    ]
    manager, resources = make_manager(
        tmp_path, model, Search(), max_concurrency=1, investigation_tool_budget=2
    )
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        events = manager.store.read(sid)
        assert require_text(view["status"]) == "completed"
        exhausted = [e for e in events if e["type"] == "task.budget_exhausted"]
        assert len(exhausted) == 2
        keys: set[str] = set()
        for event in exhausted:
            payload = require_object(event["payload"])
            assert require_int(payload["calls"]) == 2
            assert require_int(payload["limit"]) == 2
            keys.add(require_text(payload["key"]))
        assert keys == {
            task_key("doc_worker", "查明重试策略"),
            task_key("doc_worker", "查明退避规则"),
        }
        # 4 次检索全部发生：收束只对单任务生效，其余任务照常执行。
        assert len([e for e in events if e["type"] == "knowledge.request"]) == 4
        # 收束即断点：两任务都落 finished=True 的 turn.progress。
        payloads = progress_events(events)
        assert len(payloads) == 2
        assert all(payload["finished"] is True for payload in payloads)
    finally:
        await manager.shutdown()
        resources.close()


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
        # ④ 两个任务都按 error_report 收束（目标与安全失败文案进入汇总请求；
        # 提示词卫生：原始异常类型与文本不进模型请求，链路禁用事实由 tool.outage
        # 事件承载，报告只带 classify_failure 的安全文案）。
        assert require_text(view["status"]) == "completed"
        content = json.dumps(model.requests[-1]["messages"], ensure_ascii=False)
        assert "查明重试策略" in content and "查明退避规则" in content
        assert "RuntimeError" not in content
        assert "记录已保留" in content
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


# --- specs/010 空缺项 clarify 收敛（2026-11-23 e2e 实测）：缺项与问题全空的
# clarify 决策按作答落地，正文流经既有校验门；防回归锁住两类真实追问形态。


def _conclusion_reports() -> list[WorkerReport]:
    """一段结论式调查报告：句子自带引用标记，不触发 009 引用降级。"""
    return [WorkerReport("sql", "### 调查结论\n\n支付状态见 [doc:KB-TS-012#c001]。")]


def test_resolve_response_empty_clarify_converges_to_answer() -> None:
    """缺项与问题全空的 clarify 按作答落地：waiting=False、正文为结论摘要。"""
    shared = SharedTurn()
    resolved = resolve_response(
        shared, _conclusion_reports(), completion(calls=[response_call("clarify", question="")])
    )
    assert resolved.waiting is False
    assert resolved.mode == "answer"
    assert resolved.source == "model"
    # 正文是 clarify_body 的结论摘要形态，不含追问段。
    assert "已查到的结论" in resolved.text
    assert "还需你补充" not in resolved.text


def test_resolve_response_clarify_with_question_keeps_waiting() -> None:
    """缺项全空但模型问题非空：仍是真实追问内容，维持 clarify/waiting 现状。"""
    shared = SharedTurn()
    resolved = resolve_response(
        shared,
        _conclusion_reports(),
        completion(calls=[response_call("clarify", question="请提供接收端访问日志。")]),
    )
    assert resolved.waiting is True
    assert resolved.mode == "clarify"


def test_resolve_response_clarify_with_pending_keeps_waiting() -> None:
    """缺项非空（pending 待回复）时维持 clarify/waiting：即使问题为空也不收敛。"""
    shared = SharedTurn()
    shared.pending.append(ClarificationRequest(conditions=("account_id",), question="请提供账户"))
    resolved = resolve_response(
        shared, [], completion(calls=[response_call("clarify", question="")])
    )
    assert resolved.waiting is True
    assert resolved.mode == "clarify"
    assert "还需你补充" in resolved.text


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


async def test_placeholder_finish_citation_checks_report_lines(tmp_path: Path) -> None:
    """FR-12 占位完成同样经引用校验（SC-5 无豁口，审查 I1）。

    worker 报告含无引用断言句时，占位正文原样嵌入会裸奔输出——降级为统一
    未证实口径后才落最终作答；有引用的报告句不受影响。
    """
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("退款是允许的。"),  # worker 报告：无引用断言句
        completion(None),  # synthesize 首次空输出
        completion(None),  # 重试仍空 → 占位完成
    ]
    manager, resources = make_manager(tmp_path, model, Search())
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        assert require_text(view["status"]) == "completed"
        answer = require_text(view["messages"][-1]["content"])
        assert "未能核实：退款是允许的。" in answer  # 占位里的断言句被降级
        assert "已查到的结论" in answer
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


def progress_events(events: list[Event]) -> list[JsonObject]:
    """提取 turn.progress 载荷；供断点落盘断言。"""
    return [
        require_object(require_object(e)["payload"])
        for e in events
        if require_object(e)["type"] == "turn.progress"
    ]


async def test_worker_completion_persists_turn_progress(tmp_path: Path) -> None:
    """FR-4 断点落盘：worker 子任务收尾写 turn.progress，载荷为 SubtaskProgress 全量字段。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion("已根据调查报告回答。"),
    ]
    manager, resources = make_manager(tmp_path, model, Search())
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        events = manager.store.read(sid)
        payloads = progress_events(events)
        assert len(payloads) == 1  # 单任务收尾恰一次
        payload = payloads[0]
        assert require_text(payload["worker"]) == "doc_worker"
        assert require_text(payload["objective"]) == "查明重试策略"
        assert require_text(payload["key"]) == task_key("doc_worker", "查明重试策略")
        assert payload["finished"] is True
        # 已查询键并入 SharedTurn.done 语义：search 键在案。
        assert "search:重试策略" in require_list(payload["done_keys"])
        assert require_list(payload["findings"])
        assert require_list(payload["evidence_refs"])
    finally:
        await manager.shutdown()
        resources.close()


async def test_turn_progress_written_once_per_task_across_parallel_workers(tmp_path: Path) -> None:
    """Send 并行两任务：各收尾恰一次，幂等键不重复（防重复收尾）。"""
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
        completion(calls=[search_call("s2", "退避规则")]),
        completion("退避规则：同样按文档处理 [doc:RULE-1#c001]。"),
        completion("已根据调查报告回答。"),
    ]
    manager, resources = make_manager(tmp_path, model, Search(), max_concurrency=1)
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        payloads = progress_events(manager.store.read(sid))
        assert sorted(require_text(p["objective"]) for p in payloads) == [
            "查明退避规则",
            "查明重试策略",
        ]
        keys = [require_text(p["key"]) for p in payloads]
        assert len(keys) == len(set(keys))  # 幂等键不重复
    finally:
        await manager.shutdown()
        resources.close()


def test_dispatch_filters_finished_subtasks_on_resume() -> None:
    """FR-4 节点幂等：恢复轮 dispatch 按注入的 task_progress 跳过已收束子任务。"""
    from flowpay.workflows.multi_agent.graph import dispatch
    from flowpay.workflows.multi_agent.state import GraphState, InvestigationTask

    done_payload = to_json_object(
        SubtaskProgress(
            key=task_key("doc_worker", "查明重试策略"),
            worker="doc_worker",
            objective="查明重试策略",
            done_keys=["search:重试策略"],
            findings=[],
            evidence_refs=[],
            finished=True,
        )
    )
    state: GraphState = {
        "user_message": "帮我看看",
        "saved_state": {"task_progress": [done_payload]},
        "intent": "troubleshoot",
        "tasks": [
            InvestigationTask(kind="docs", objective="查明重试策略"),
            InvestigationTask(kind="sql", objective="查明扣款状态"),
        ],
        "reports": [],
        "answer": "",
        "waiting": False,
    }
    sends = dispatch(state)
    assert isinstance(sends, list) and len(sends) == 1
    payload = cast("GraphState", sends[0].arg)
    remaining = [task.objective for task in payload["tasks"]]
    assert remaining == ["查明扣款状态"]  # 已收束子任务被过滤，其余照常分发


def test_dispatch_tolerates_corrupted_progress_container() -> None:
    """恢复注入的 task_progress 容器形状损坏：按空处理，不抛错阻断续跑（审查 M5）。

    容器损坏只损失断点过滤——两任务照常分发，与坏载荷条目（restore 按 None
    跳过）同一容忍口径。
    """
    from flowpay.workflows.multi_agent.graph import dispatch
    from flowpay.workflows.multi_agent.state import GraphState, InvestigationTask

    state: GraphState = {
        "user_message": "帮我看看",
        "saved_state": {"task_progress": "corrupted-shape"},
        "intent": "troubleshoot",
        "tasks": [
            InvestigationTask(kind="docs", objective="查明重试策略"),
            InvestigationTask(kind="sql", objective="查明扣款状态"),
        ],
        "reports": [],
        "answer": "",
        "waiting": False,
    }
    sends = dispatch(state)
    assert isinstance(sends, list) and len(sends) == 2


async def test_resume_continues_from_breakpoint_without_requery(tmp_path: Path) -> None:
    """US2 集成（SC-4 可证伪）：多子任务轮次中断→显式继续，恢复后零重查。

    两任务收束后 synthesize 硬失败（run.failed）：断点已随 turn.progress 落盘；
    显式继续后 plan 复产出相同目标 → dispatch 按断点过滤 → 两个 worker 不再
    执行（knowledge.request 总数不变）、恢复轮日计量为 0、并发名额守恒。
    """
    from datetime import UTC, datetime

    objectives: list[JsonObject] = [
        {"kind": "docs", "objective": "查明重试策略"},
        {"kind": "docs", "objective": "查明退避规则"},
    ]
    model = Model()
    model.responses = [
        # 轮 1：两个子任务都收束（断点落盘），synthesize 硬失败。
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", objectives)]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion(calls=[search_call("s2", "退避规则")]),
        completion("退避规则：同样按文档处理 [doc:RULE-1#c001]。"),
        RuntimeError("synthesize boom"),
        # 轮 2（显式继续）：相同目标重新拆解 → dispatch 过滤 → 直接汇总。
        completion(calls=[intent_call("i2", "troubleshoot")]),
        completion(calls=[tasks_call("t2", objectives)]),
        completion("已根据调查报告回答。\n\n[doc:RULE-1#c001]"),
        # 并发守恒检查轮：新会话闲聊直答。
        completion(calls=[intent_call("i3", "chitchat")]),
        conversation("你好！我可以帮你排查支付与 Webhook 问题。"),
    ]
    manager, resources = make_manager(tmp_path, model, Search(), max_concurrency=1)
    try:
        sid = require_text((await manager.create_session(MERCHANT))["id"])
        await manager.submit_message(sid, "one", "帮我看看这笔支付为什么没收到通知", MERCHANT)
        await manager.tasks[sid]
        failed = await manager.get_session(sid, MERCHANT)
        assert require_text(failed["status"]) == "failed"  # 中断形态
        events = manager.store.read(sid)
        assert len(progress_events(events)) == 2  # 两个子任务断点已落盘

        await manager.resume_run(sid, "resume", MERCHANT)
        await manager.tasks[sid]
        view = await manager.get_session(sid, MERCHANT)
        events = manager.store.read(sid)
        assert require_text(view["status"]) == "completed"  # 恢复轮正常完成
        # ① SC-4 零重查：knowledge.request 总数保持轮 1 的 2 次（恢复轮零请求）。
        assert len([e for e in events if e["type"] == "knowledge.request"]) == 2
        # ② 恢复轮日回合计量为 0：两个 run.started 落库但只计 1。
        assert len([e for e in events if e["type"] == "run.started"]) == 2
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert manager.store.daily_run_count(day) == 1
        # ③ 并发名额守恒：恢复结束后商户可继续提交（未净减）。
        other = require_text((await manager.create_session(MERCHANT))["id"])
        await manager.submit_message(other, "next", "你好，你能做什么？", MERCHANT)
        await manager.tasks[other]
        assert require_text((await manager.get_session(other, MERCHANT))["status"]) == "completed"
    finally:
        await manager.shutdown()
        resources.close()


# --- specs/009 US3 T017：resolve_response 输出前的引用校验接入 ---


def test_resolve_response_downgrades_bare_assertion_in_model_answer() -> None:
    """FR-6：模型作答里的无标记断言句在输出前被确定性降级为未证实口径。"""
    shared = SharedTurn()
    answer = completion(calls=[_answer_call("该笔支付的结果是退款完成。")])
    resolved = resolve_response(shared, [], answer)
    assert resolved.waiting is False
    assert resolved.text == "未能核实：该笔支付的结果是退款完成。"


def test_resolve_response_keeps_cited_answer_untouched() -> None:
    """带引用标记的结论句原样输出（编号真伪归既有编造核对机制）。"""
    shared = SharedTurn()
    text = "该笔支付已退款，见 pay_bg_001_0003。"
    resolved = resolve_response(shared, [], completion(calls=[_answer_call(text)]))
    assert resolved.text == text


def test_resolve_response_leaves_clarify_body_alone() -> None:
    """clarify 轮正文是系统确定性拼接（pending 优先拍板），不经引用校验改写。"""
    shared = SharedTurn()
    shared.pending.append(ClarificationRequest(conditions=("account_id",), question="请提供账户"))
    resolved = resolve_response(shared, [], completion(None))
    assert resolved.waiting is True
    assert resolved.text.startswith("目前尚未查到结论")


async def test_synthesize_answer_passes_citation_check(tmp_path: Path) -> None:
    """集成：synthesize 输出经引用校验，裸奔断言被降级后成为最终作答。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")]),
        completion(calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明重试策略"}])]),
        completion(calls=[search_call("s1", "重试策略")]),
        completion("重试策略：HTTP 410 后禁用端点 [doc:RULE-1#c001]。"),
        completion(calls=[response_call("answer", answer="该笔支付的结果是退款完成。")]),
    ]
    manager, resources = make_manager(tmp_path, model, Search())
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "first", "帮我看看这笔支付为什么没收到通知", INTERNAL)
        await manager.tasks[sid]
        view = await manager.get_session(sid, INTERNAL)
        answer = require_text(view["messages"][-1]["content"])
        assert answer == "未能核实：该笔支付的结果是退款完成。"
        decided = [e for e in manager.store.read(sid) if e["type"] == "clarification.decided"]
        assert require_object(decided[-1]["payload"])["mode"] == "answer"
    finally:
        await manager.shutdown()
        resources.close()
