"""assertion_gate：010 在线校验门的可证伪集成回归（specs/010 US3 T016）。

直接构造 synthesize_node 的 node（gate 以参数注入，graph 装配链在树A 合并后
接通），断言外部可见行为——事件序列、发布文本、模型调用次数；三形态防既有
路径空跑通过：关闭=零新增事件、开启=事件+处置生效、故障=fail-open。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

from flowpay.core.agent import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.event_log import EventStore
from flowpay.core.event_log.contracts import EventSink
from flowpay.core.event_log.types import Event
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.verify import (
    Assertion,
    AssertionAction,
    AssertionAudit,
    AssertionVerdict,
    Conflict,
    Overreach,
)
from flowpay.workflows.multi_agent.assertion_gate import (
    AUDIT_EVENT,
    AssertionAuditor,
    AssertionGate,
    apply_actions,
    gate_from_settings,
    gate_response,
    select_assertions,
)
from flowpay.workflows.multi_agent.graph import MultiAgentWorkflow
from flowpay.workflows.multi_agent.investigation import synthesize_node
from flowpay.workflows.multi_agent.state import GraphState, SharedTurn

from .fixtures import Model, completion

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
    tracers: list[EventSink]

    def __init__(self, result: AssertionAudit | Exception) -> None:
        self.result = result
        self.inputs = []
        self.tracers = []

    async def audit(
        self,
        text: str,
        question: str,
        passages: Sequence[Passage],
        *,
        tracer: EventSink,
    ) -> AssertionAudit:
        self.inputs.append((text, question, len(passages)))
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


async def test_budget_exhausted_skips_verification(tmp_path: Path) -> None:
    """轮次预算耗尽：跳过校验（不调校验器）并发等效 fail-open 事件。"""
    auditor = StubAuditor(fixture_audit())
    gate = AssertionGate(enabled=True, auditor=cast_auditor(auditor))
    model = Model()
    model.responses = [completion(calls=answer_decision(DRAFT))]
    shared = SharedTurn(budget=Budget(12, 16, 12, 0))
    answer, events = await run_synthesize(tmp_path, model, gate, shared)
    assert answer == DRAFT
    assert auditor.inputs == []
    payloads = audit_events(events)
    assert len(payloads) == 1 and payloads[0]["fail_open"] is True


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
