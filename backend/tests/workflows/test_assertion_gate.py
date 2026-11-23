"""assertion_gate：010 在线校验门的可证伪集成回归（specs/010 US3 T016）。

直接构造 synthesize_node 的 node（gate 以参数注入，graph 装配链在树A 合并后
接通），断言外部可见行为——事件序列、发布文本、模型调用次数；三形态防既有
路径空跑通过：关闭=零新增事件、开启=事件+处置生效、故障=fail-open。
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from flowpay.core.agent import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.event_log import EventStore
from flowpay.core.event_log.contracts import EventSink
from flowpay.core.event_log.types import Event
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.core.verify import (
    Assertion,
    AssertionAction,
    AssertionAudit,
    AssertionVerdict,
    Conflict,
    Overreach,
)
from flowpay.core.verify.actions import apply_actions
from flowpay.core.verify.excerpts import (
    RECORD_EXCERPT_CAP,
    RECORD_EXCERPTS_CHAR_CAP,
    RECORD_EXCERPTS_MAX,
    register_record_excerpts,
)
from flowpay.core.verify.online import ModelAuditor
from flowpay.workflows.multi_agent.assertion_gate import (
    AUDIT_EVENT,
    AssertionAuditor,
    AssertionGate,
    gate_from_settings,
    gate_response,
    select_assertions,
)
from flowpay.workflows.multi_agent.graph import MultiAgentWorkflow
from flowpay.workflows.multi_agent.investigation import synthesize_node
from flowpay.workflows.multi_agent.simple import NO_EVIDENCE, chitchat_node, doc_qa_node
from flowpay.workflows.multi_agent.state import (
    GraphState,
    SharedTurn,
    restore_record_excerpts,
)

from .fixtures import Model, Search, completion, grounded, hit, tasks_call

SESSION = "session_gate"

# 草稿正文（经 audited_response 后形态）：两句带 KB 引用的断言句。
DRAFT = (
    "FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。\n\n"
    "该支付允许重试，见 [doc:KB-API-013#c001]。"
)
REVISION = "企业版下 FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。"
DOWNGRADED = "未能核实：该支付允许重试，见 [doc:KB-API-013#c001]。"


def answer_decision(text: str) -> list[JsonObject]:
    """一次 set_response(mode=answer) 决策调用。"""
    return [
        {
            "id": "decision_1",
            "type": "function",
            "function": {
                "name": "set_response",
                "arguments": json.dumps({"mode": "answer", "answer": text}),
            },
        }
    ]


class StubAuditor:
    """受控校验器：返回预设 AssertionAudit 或抛错，并记录收到的输入。"""

    result: AssertionAudit | Exception
    inputs: list[tuple[str, str, int]]
    excerpts: list[Mapping[str, str]]
    tracers: list[EventSink]

    def __init__(self, result: AssertionAudit | Exception) -> None:
        self.result = result
        self.inputs = []
        self.excerpts = []
        self.tracers = []

    async def audit(
        self,
        text: str,
        question: str,
        passages: Sequence[Passage],
        *,
        tracer: EventSink,
        record_excerpts: Mapping[str, str],
    ) -> AssertionAudit:
        self.inputs.append((text, question, len(passages)))
        self.excerpts.append(record_excerpts)
        self.tracers.append(tracer)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fixture_audit() -> AssertionAudit:
    """处置样例：partial 收紧替换 + insufficient 降级 + contradicted 带 conflict。"""
    return AssertionAudit(
        verdicts=(
            AssertionVerdict(
                assertion=Assertion(
                    text="FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。",
                ),
                verdict="partial",
                overreach=Overreach(dimension="对象", note="原文只适用企业版"),
                revision=REVISION,
            ),
            AssertionVerdict(
                assertion=Assertion(
                    text="该支付允许重试，见 [doc:KB-API-013#c001]。",
                ),
                verdict="insufficient",
            ),
            AssertionVerdict(
                assertion=Assertion(text="另有矛盾句，见 [doc:KB-TS-012#c002]。"),
                verdict="contradicted",
                conflict=Conflict(
                    sources=("doc:KB-TS-012#c002", "doc:KB-BR-003#c001"),
                    point="重试口径冲突",
                ),
            ),
        ),
        actions=(
            AssertionAction(
                sentence="FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。",
                action="replace",
                revision=REVISION,
            ),
            AssertionAction(
                sentence="该支付允许重试，见 [doc:KB-API-013#c001]。",
                action="downgrade",
            ),
        ),
        fail_open=False,
        text=f"{REVISION}\n\n{DOWNGRADED}",
    )


async def run_synthesize(
    tmp_path: Path,
    model: Model,
    gate: AssertionGate | None = None,
    shared: SharedTurn | None = None,
) -> tuple[str, list[Event]]:
    """跑一次 synthesize 节点：返回发布文本与会话全部事件。"""
    store = EventStore(tmp_path / "events.sqlite")
    runtime = AgentRuntime(Tracer(store, SESSION, "run_gate"))
    shared = shared or SharedTurn()
    node = synthesize_node(model, runtime, shared, None, gate=gate)
    state: GraphState = {
        "user_message": "FP-4041 是什么？",
        "saved_state": {},
        "intent": "",
        "tasks": [],
        "reports": [],
        "answer": "",
        "waiting": False,
    }
    outcome = await node(state)
    answer = str(outcome["answer"])
    return answer, list(store.read(SESSION))


def audit_events(events: Sequence[Event]) -> list[JsonObject]:
    """会话事件里的 assertion.audit 载荷清单（按序）。"""
    return [require_object(event["payload"]) for event in events if event["type"] == AUDIT_EVENT]


async def test_gate_off_keeps_009_behavior(tmp_path: Path) -> None:
    """开关关闭（默认 None 与 enabled=False 等价）：零新增事件，行为与 009 一致。"""
    for gate in (None, AssertionGate(enabled=False)):
        model = Model()
        model.responses = [completion(calls=answer_decision(DRAFT))]
        answer, events = await run_synthesize(tmp_path, model, gate)
        assert answer == DRAFT
        assert audit_events(events) == []
        # 零新增模型调用：只有决策这一次请求。
        assert len(model.requests) == 1


async def test_gate_on_replaces_and_downgrades(tmp_path: Path) -> None:
    """开关开启：处置生效——partial 句收紧替换、insufficient 句降级未证实口径。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(calls=answer_decision(DRAFT))]
    answer, events = await run_synthesize(tmp_path, model, gate)
    assert answer == f"{REVISION}\n\n{DOWNGRADED}"
    payloads = audit_events(events)
    assert len(payloads) == 1
    payload = payloads[0]
    # 事件载荷含 verdicts/actions/fail_open（可证伪：校验确实发生并落盘）。
    assert payload["fail_open"] is False
    verdicts = require_list(payload["verdicts"])
    assert len(verdicts) == 3
    assert len(require_list(payload["actions"])) == 2
    conflict = require_object(require_object(verdicts[2])["conflict"])
    assert conflict["point"] == "重试口径冲突"
    assert len(require_list(conflict["sources"])) == 2
    # 校验器收到的是句级筛选后的断言集合与本轮问题；证据池为空共享轮。
    text, question, passage_count = auditor.inputs[0]
    assert "FP-4041 的含义是 Customer 不存在" in text
    assert question == "FP-4041 是什么？"
    assert passage_count == 0
    # 空池回归：shared 无摘录时校验器收到空映射，装配口径不因传参而变化。
    assert auditor.excerpts == [{}]


async def test_verifier_failure_fails_open(tmp_path: Path) -> None:
    """校验器故障：放行原文 + fail_open=true + 不产生 run.failed。"""
    auditor = StubAuditor(RuntimeError("verifier exploded"))
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(calls=answer_decision(DRAFT))]
    answer, events = await run_synthesize(tmp_path, model, gate)
    # 原文放行（citation check 后形态，未改写），轮次正常结束。
    assert answer == DRAFT
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is True
    assert not [event for event in events if event["type"] == "run.failed"]
    assert not [event for event in events if event["type"] == "turn.progress"]


async def test_budget_exhausted_still_verifies(tmp_path: Path) -> None:
    """轮次预算耗尽（may_think False）：gate 仍执行完整校验（2026-11-23 e2e）。

    校验器在线调用走独立 ChatClient、不消耗轮次预算（specs/009 FR-9 口径不变），
    因此预算耗尽不应跳过校验——否则完整排查轮（预算先被 worker 调查耗尽）在
    线上永远走 fail-open、从不被真正校验。
    """
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(calls=answer_decision(DRAFT))]
    shared = SharedTurn(budget=Budget(12, 16, 12, 0))
    answer, events = await run_synthesize(tmp_path, model, gate, shared)
    # 校验确实发生：auditor 被调用，处置生效发布，判定/动作完整落事件。
    assert len(auditor.inputs) == 1
    assert answer == f"{REVISION}\n\n{DOWNGRADED}"
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is False
    assert len(require_list(payloads[0]["verdicts"])) == 3
    assert len(require_list(payloads[0]["actions"])) == 2


async def test_zero_assertion_sentences_skip_verifier(tmp_path: Path) -> None:
    """无断言句（纯指引）：不调校验器，事件按零判定记录，原文放行。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    text = "请提供账户 ID 后继续排查。"
    model = Model()
    model.responses = [completion(calls=answer_decision(text))]
    answer, events = await run_synthesize(tmp_path, model, gate)
    assert answer == text
    assert auditor.inputs == []
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["verdicts"] == []


async def test_waiting_turn_is_not_gated(tmp_path: Path) -> None:
    """澄清追问正文（系统拼接）：不校验、零事件，与 009 一致。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    decision: list[JsonObject] = [
        {
            "id": "decision_1",
            "type": "function",
            "function": {
                "name": "set_response",
                "arguments": json.dumps({"mode": "clarify", "question": "请提供账户 ID。"}),
            },
        }
    ]
    model = Model()
    model.responses = [completion(calls=decision)]
    answer, events = await run_synthesize(tmp_path, model, gate)
    assert "请提供账户 ID" in answer
    assert auditor.inputs == []
    assert audit_events(events) == []


async def test_gate_response_unit_paths(tmp_path: Path) -> None:
    """gate_response 的关闭/降级/回填路径（单元级）：事件载体与幂等回填。"""
    store = EventStore(tmp_path / "unit.sqlite")
    tracer = Tracer(store, "unit", "run_unit")

    # 关闭态零事件。
    assert (
        await gate_response(DRAFT, None, waiting=False, question="q", passages=(), tracer=tracer)
        == DRAFT
    )
    assert store.read("unit") == []

    # 降级幂等：降级句带未证实口径，再过门被筛选豁免，不二次改写；收紧替换句
    # 仍是断言句，其幂等由校验器把 revision 判 supported 承诺（树A 判定质量）。
    downgraded = apply_actions(DRAFT, fixture_audit().actions)
    assert downgraded == f"{REVISION}\n\n{DOWNGRADED}"
    assert select_assertions(downgraded) == [REVISION]
    assert not any(DOWNGRADED in sentence for sentence in select_assertions(downgraded))


async def test_gate_from_settings_defaults() -> None:
    """Settings 开关读取：字段缺失默认关闭；字段存在时按值装配。"""

    class SettingsStub:
        """树A T013 字段的替身（getattr 兜底契约的对偶验证）。"""

        assertion_audit_enabled: bool = True
        assertion_audit_evidence_budget: int = 1234

    auditor = StubAuditor(fixture_audit())
    gate = gate_from_settings(SettingsStub(), cast_auditor(auditor))
    assert gate is not None and gate.enabled and gate.budget == 1234

    class DisabledStub:
        assertion_audit_enabled: bool = False

    assert gate_from_settings(DisabledStub()) is None
    # 字段完全缺失（树A 合并前的 Settings）：默认关闭，零装配。
    assert gate_from_settings(object()) is None


def cast_auditor(auditor: StubAuditor) -> AssertionAuditor:
    """把受控校验器收口成调用面类型（Protocol 是结构化类型，直接传也兼容）。"""
    return auditor


# ---- P1-2 清偿回归：装配链把 gate 透传到 workflow（bootstrap → router → run）----


def test_gate_assembly_wiring_enabled_and_disabled() -> None:
    """开启态 gate 带 auditor 透传到 workflow 属性；关闭态装配为 None。"""
    on = SimpleNamespace(assertion_audit_enabled=True, assertion_audit_evidence_budget=8000)
    gate = gate_from_settings(on, auditor=StubAuditor(fixture_audit()))
    workflow = MultiAgentWorkflow(
        Model(), lambda q: q, investigation_tool_budget=16, assertion_gate=gate
    )
    assert workflow.assertion_gate is gate
    off = SimpleNamespace(assertion_audit_enabled=False, assertion_audit_evidence_budget=8000)
    assert gate_from_settings(off, auditor=StubAuditor(fixture_audit())) is None


async def test_dispute_retry_passes_gate(tmp_path: Path) -> None:
    """审查 P1-3：DISPUTE 重试作答同样过 gate——重试路径零校验发布的缝堵上。

    首轮草稿含 seen 外编号（编造核对触发重试）；重试文本被 gate 处置发布，
    audit 恰一次且发生在收敛之后（处置锚点是重试文本的句子）。
    """
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    invented = (
        "订单 pay_bg_001_0003 已入账，见 [doc:KB-TS-012#c001]。"  # seen 外编号触发 DISPUTE
    )
    model = Model()
    model.responses = [
        completion(calls=answer_decision(f"{DRAFT}\n\n{invented}")),
        completion(calls=answer_decision(DRAFT)),  # 重试：干净文本
    ]
    answer, events = await run_synthesize(tmp_path, model, gate)
    assert len(model.requests) == 2  # 编造核对重试确实发生
    assert answer == f"{REVISION}\n\n{DOWNGRADED}"  # 重试文本经 gate 处置发布
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is False


# ---- 直答路径清偿回归：doc_qa 与 chitchat 也是作答类发布出口（specs/010 US1）----


def light_state(user_message: str) -> GraphState:
    """直答节点的最小图状态：不经 route/plan，直接驱动目标节点。"""
    return {
        "user_message": user_message,
        "saved_state": {},
        "intent": "",
        "tasks": [],
        "reports": [],
        "answer": "",
        "waiting": False,
    }


async def run_doc_qa(
    tmp_path: Path,
    model: Model,
    gate: AssertionGate | None,
    user_message: str = "Webhook 的重试策略是什么？",
    shared: SharedTurn | None = None,
) -> tuple[str, list[Event], Search]:
    """跑一次 doc_qa 节点：返回发布文本、会话全部事件与检索记录。"""
    store = EventStore(tmp_path / "doc_qa.sqlite")
    search = Search()
    runtime = AgentRuntime(
        Tracer(store, SESSION, "run_gate"), knowledge=search, collection_id="fixture"
    )
    _ = search.for_run(runtime.tracer)
    node = doc_qa_node(model, lambda query: query, runtime, shared or SharedTurn(), gate=gate)
    outcome = await node(light_state(user_message))
    return str(outcome["answer"]), list(store.read(SESSION)), search


async def run_chitchat(
    tmp_path: Path, model: Model, gate: AssertionGate | None
) -> tuple[str, list[Event]]:
    """跑一次 chitchat 节点：返回发布文本与会话全部事件。"""
    store = EventStore(tmp_path / "chitchat.sqlite")
    runtime = AgentRuntime(Tracer(store, SESSION, "run_gate"))
    node = chitchat_node(model, runtime, SharedTurn(), gate=gate)
    outcome = await node(light_state("你好，你能做什么？"))
    return str(outcome["answer"]), list(store.read(SESSION))


async def test_doc_qa_gate_audits_cited_answer(tmp_path: Path) -> None:
    """docs 直答带引用断言句过门：处置生效发布，恰一条 audit 事件（US1/FR-7）。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(DRAFT)]
    answer, events, search = await run_doc_qa(tmp_path, model, gate)
    # 处置改写确实作用于发布文本：partial 句收紧替换、insufficient 句降级。
    assert answer == f"{REVISION}\n\n{DOWNGRADED}"
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is False
    assert len(require_list(payloads[0]["verdicts"])) == 3
    assert len(require_list(payloads[0]["actions"])) == 2
    # 校验器收到句级断言集合、本轮问题与登记进共享池的检索片段。
    text, question, passage_count = auditor.inputs[0]
    assert "FP-4041 的含义是 Customer 不存在" in text
    assert question == "Webhook 的重试策略是什么？"
    assert passage_count == 1
    assert len(search.queries) == 1


async def test_doc_qa_no_evidence_zero_verdict_audit(tmp_path: Path) -> None:
    """docs 零命中直答过门：零断言放行，零判定 audit 事件，不调校验器。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(DRAFT)]  # 不应被消费：零命中早退不调模型
    answer, events, search = await run_doc_qa(
        tmp_path, model, gate, "Webhook 的重试策略是什么？missing"
    )
    assert answer == NO_EVIDENCE
    assert auditor.inputs == []
    assert len(search.queries) == 1
    assert len(model.requests) == 0
    payloads = audit_events(events)
    assert len(payloads) == 1
    assert payloads[0]["fail_open"] is False
    assert payloads[0]["verdicts"] == []


async def test_chitchat_gate_zero_verdict_audit(tmp_path: Path) -> None:
    """闲聊直答过门：零断言放行记零判定事件，不调校验器，正文不变。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    text = "你好！我可以帮你排查支付与 Webhook 问题。"
    model.responses = [completion(text)]
    answer, events = await run_chitchat(tmp_path, model, gate)
    assert answer == text
    assert auditor.inputs == []
    payloads = audit_events(events)
    assert len(payloads) == 1
    assert payloads[0]["fail_open"] is False
    assert payloads[0]["verdicts"] == []


async def test_chitchat_gate_off_zero_events(tmp_path: Path) -> None:
    """gate 关闭（None）：闲聊发布零新增事件、文本不变，行为与开启前一致。"""
    for gate in (None, AssertionGate(enabled=False)):
        model = Model()
        text = "你好！我可以帮你排查支付与 Webhook 问题。"
        model.responses = [completion(text)]
        answer, events = await run_chitchat(tmp_path, model, gate)
        assert answer == text
        assert audit_events(events) == []


# ---- 记录摘录在线装配（specs/010 FR-1）：worker 业务查询工具结果原文按编号
# 登记进共享摘录池，经 gate → auditor 全链传入，消除记录支撑断言的必然降级 ----


# 一行业务记录的工具结果原文；摘录即该行的 JSON 原文（按记录行裁剪）。
RECORD_ROW = {"payment_id": "pay_bg_001_0003", "status": "refunded", "amount": 100}
RECORD_EXCERPT = json.dumps(RECORD_ROW, ensure_ascii=False)
# 句式含断言线索词（"为"），才会被句级筛选送进校验器（citation_check 口径）。
RECORD_DRAFT = "支付 pay_bg_001_0003 状态为已退款，见 [doc:RULE-1#c001]。"


def test_register_record_excerpts_is_idempotent() -> None:
    """同一编号重复登记覆盖为新值，登记序不变（幂等）。"""
    pool: dict[str, str] = {}
    register_record_excerpts(pool, [RECORD_ROW])
    assert pool == {"pay_bg_001_0003": RECORD_EXCERPT}
    register_record_excerpts(pool, [RECORD_ROW])
    assert pool == {"pay_bg_001_0003": RECORD_EXCERPT}
    # 覆盖新值但保持原登记位（淘汰序按最早登记，不被覆盖刷新）。
    register_record_excerpts(pool, [{"payment_id": "evt_bg_001_0002", "type": "captured"}])
    register_record_excerpts(pool, [{**RECORD_ROW, "status": "failed"}])
    assert list(pool) == ["pay_bg_001_0003", "evt_bg_001_0002"]
    updated = {**RECORD_ROW, "status": "failed"}
    assert pool["pay_bg_001_0003"] == json.dumps(updated, ensure_ascii=False)


def test_register_record_excerpts_keys_each_business_id_in_row() -> None:
    """行内出现的每个业务编号都登记该行的 JSON 原文；非业务词形不登记。"""
    pool: dict[str, str] = {}
    delivery = {"delivery_id": "del_bg_002_0001", "event_id": "evt_bg_001_0009", "api": "v2"}
    register_record_excerpts(pool, [delivery])
    assert set(pool) == {"del_bg_002_0001", "evt_bg_001_0009"}
    assert pool["evt_bg_001_0009"] == json.dumps(delivery, ensure_ascii=False)
    # 字段名与错误码不是业务记录编号，不产生摘录条目（宁可漏登不错登）。
    register_record_excerpts(pool, [{"error_code": "FP-4021", "api": "v2"}])
    assert set(pool) == {"del_bg_002_0001", "evt_bg_001_0009"}


def test_register_record_excerpts_clips_single_entry() -> None:
    """单条摘录封顶：超长行截断，摘录是判定语境不是原文回放（回放同口径）。"""
    pool: dict[str, str] = {}
    register_record_excerpts(pool, [{"payment_id": "pay_bg_001_0003", "note": "长" * 3000}])
    assert len(pool["pay_bg_001_0003"]) == RECORD_EXCERPT_CAP


def test_register_record_excerpts_evicts_oldest_by_count() -> None:
    """条数超限：从最早登记的编号开始淘汰，最新登记保留。"""
    pool: dict[str, str] = {}
    rows = [
        {"payment_id": f"pay_bg_{index:04d}_0001"}
        for index in range(RECORD_EXCERPTS_MAX + 10)
    ]
    register_record_excerpts(pool, rows)
    assert len(pool) == RECORD_EXCERPTS_MAX
    assert "pay_bg_0000_0001" not in pool  # 最旧的先淘汰
    assert "pay_bg_0073_0001" in pool  # 最新登记保留


def test_register_record_excerpts_evicts_oldest_by_chars() -> None:
    """总字符超限：同样从最旧淘汰，双封顶取更严者。"""
    pool: dict[str, str] = {}
    note = "x" * 1800
    rows = [{"payment_id": f"pay_bg_{index:04d}_0001", "note": note} for index in range(30)]
    register_record_excerpts(pool, rows)
    assert sum(len(value) for value in pool.values()) <= RECORD_EXCERPTS_CHAR_CAP
    assert "pay_bg_0000_0001" not in pool
    assert "pay_bg_0029_0001" in pool


def test_restore_record_excerpts_roundtrip() -> None:
    """摘录池随检查点恢复；没有检查点（旧会话/首轮）从空开始。"""
    pool = {"pay_bg_001_0003": RECORD_EXCERPT, "req_bm_030_01": "status=200"}
    assert restore_record_excerpts(dict(pool)) == pool
    assert restore_record_excerpts(None) == {}


async def test_gate_passes_shared_record_excerpts(tmp_path: Path) -> None:
    """shared 摘录池随 gate 传入校验器（受控校验器记录摘录输入，可证伪）。"""
    shared = SharedTurn()
    # 编造核对材料：编号已在 seen 里，正文不被 DISPUTE 重试链改写（与校验无关）。
    shared.seen.add("pay_bg_001_0003")
    shared.record_excerpts["pay_bg_001_0003"] = RECORD_EXCERPT
    auditor = StubAuditor(AssertionAudit())
    gate = AssertionGate(enabled=True, auditor=auditor)
    model = Model()
    model.responses = [completion(calls=answer_decision(RECORD_DRAFT))]
    answer, events = await run_synthesize(tmp_path, model, gate, shared)
    assert answer == RECORD_DRAFT
    assert auditor.excerpts == [{"pay_bg_001_0003": RECORD_EXCERPT}]
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is False


async def test_doc_qa_gate_passes_record_excerpts(tmp_path: Path) -> None:
    """docs 直答同样把 shared 摘录池传给校验器（与 synthesize 同一口径）。"""
    shared = SharedTurn()
    shared.record_excerpts["pay_bg_001_0003"] = RECORD_EXCERPT
    auditor = StubAuditor(AssertionAudit())
    gate = AssertionGate(enabled=True, auditor=auditor)
    model = Model()
    model.responses = [completion(RECORD_DRAFT)]
    answer, _events, _search = await run_doc_qa(tmp_path, model, gate, shared=shared)
    assert answer == RECORD_DRAFT
    assert auditor.excerpts == [{"pay_bg_001_0003": RECORD_EXCERPT}]


async def test_record_excerpt_reaches_semantic_verification(tmp_path: Path) -> None:
    """摘录池有该编号原文：记录支撑断言进入语义校验（supported 发布原文）。

    用真校验器全链驱动（决策 → 抽取 → 批量校验），语义校验调用确实发生——
    摘录缺失时该断言在代码级被强制证据不足、不发生语义调用（对照下一用例）。
    """
    shared = SharedTurn()
    shared.retrieved.add([hit()])
    # 编造核对材料：编号已在 seen 里，正文不被 DISPUTE 重试链改写（与校验无关）。
    shared.seen.add("pay_bg_001_0003")
    shared.record_excerpts["pay_bg_001_0003"] = RECORD_EXCERPT
    model = Model()
    model.responses = [
        completion(calls=answer_decision(RECORD_DRAFT)),
        completion(
            json.dumps(
                {
                    "assertions": [
                        {
                            "text": RECORD_DRAFT,
                            "refs": ["RULE-1#c001"],
                            "record_ids": ["pay_bg_001_0003"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ),
        completion(
            json.dumps({"verdicts": [{"text": RECORD_DRAFT, "verdict": "supported"}]})
        ),
    ]
    gate = AssertionGate(enabled=True, auditor=ModelAuditor(model, evidence_budget=8000))
    answer, events = await run_synthesize(tmp_path, model, gate, shared)
    assert answer == RECORD_DRAFT
    assert len(model.requests) == 3  # 决策 + 抽取 + 语义校验
    payloads = audit_events(events)
    verdicts = require_list(payloads[0]["verdicts"])
    assert require_object(verdicts[0])["verdict"] == "supported"


async def test_missing_record_excerpt_still_forces_insufficient(tmp_path: Path) -> None:
    """空池回归：绑定业务编号但无摘录 → 代码级核对强制证据不足 → 降级发布。"""
    shared = SharedTurn()
    shared.retrieved.add([hit()])
    # 编造核对材料：编号已在 seen 里，正文不被 DISPUTE 重试链改写（与校验无关）。
    shared.seen.add("pay_bg_001_0003")
    model = Model()
    model.responses = [
        completion(calls=answer_decision(RECORD_DRAFT)),
        completion(
            json.dumps(
                {
                    "assertions": [
                        {
                            "text": RECORD_DRAFT,
                            "refs": ["RULE-1#c001"],
                            "record_ids": ["pay_bg_001_0003"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ),
    ]
    gate = AssertionGate(enabled=True, auditor=ModelAuditor(model, evidence_budget=8000))
    answer, events = await run_synthesize(tmp_path, model, gate, shared)
    assert answer == f"未能核实：{RECORD_DRAFT}"
    assert len(model.requests) == 2  # 摘录缺失不发生语义校验调用
    payloads = audit_events(events)
    verdicts = require_list(payloads[0]["verdicts"])
    assert require_object(verdicts[0])["verdict"] == "insufficient"


class NoopTracer:
    """只吞事件的追踪替身；检查点回归不校验事件载荷。"""

    async def emit(self, *_args: object, **_kwargs: object) -> None:
        return None


async def test_record_excerpts_checkpoint_roundtrip() -> None:
    """跨轮：摘录池写入检查点，恢复轮按原值带回 SharedTurn（恢复语义不变）。"""
    model = Model()
    model.responses = [
        completion(calls=[tasks_call("plan_1", [])]),
        grounded("已根据已有结论作答。"),
    ]
    saved: JsonObject = {"record_excerpts": {"pay_bg_001_0003": RECORD_EXCERPT}}
    workflow = MultiAgentWorkflow(model, retrieval_input)
    tracer = cast("Tracer", cast(object, NoopTracer()))
    runtime = AgentRuntime(tracer, collection_id="fixture")
    result = await workflow.run(
        TurnInput("支付 pay_bg_001_0003 一直没收到通知，帮我看看", [], saved), runtime
    )
    checkpoint = require_object(result.saved_state)
    assert checkpoint["record_excerpts"] == {"pay_bg_001_0003": RECORD_EXCERPT}
