# 回归测试：在线校验器装配（specs/010 审查 P1-2）。固定输入复现边界，不作为业务答案来源。
from collections.abc import Sequence
from typing import cast

from flowpay.core.event_log.types import Event
from flowpay.core.json import JsonObject
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.verify.online import ModelAuditor
from tests.core.verify_fixtures import (
    ROUNDS,
    ScriptedModel,
    completion,
    json_payload,
)

DRAFT = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"
SENTENCE = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"

EXTRACTION = {
    "assertions": [{"text": SENTENCE, "refs": ["KB-TS-012#c001"], "record_ids": []}]
}
VERDICT_PARTIAL = {
    "verdicts": [
        {
            "text": SENTENCE,
            "verdict": "partial",
            "overreach": {"dimension": "对象", "note": "原文限企业版工单"},
            "revision": "企业版工单通常 5 个工作日响应 [KB-TS-012#c001]。",
        }
    ]
}


class TurnTracer:
    """当轮 tracer 替身：记录事件种类（装配级断言"校验调用落当轮事件流"）。"""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> Event:
        del payload, call_id, parent_call_id
        self.kinds.append(kind)
        return Event(
            schema_version=1,
            event_id="",
            session_id="",
            seq=0,
            run_id=None,
            call_id=None,
            parent_call_id=None,
            timestamp="",
            type=kind,
            payload={},
        )


async def test_model_auditor_routes_through_turn_tracer() -> None:
    """校验调用经当轮 tracer 落 model.request/response 事件（tracer 每轮注入）。"""
    model = ScriptedModel(
        completion(json_payload(EXTRACTION)),
        completion(json_payload(VERDICT_PARTIAL)),
    )
    auditor = ModelAuditor(model, evidence_budget=8000)
    tracer = TurnTracer()
    audit = await auditor.audit(
        DRAFT,
        "企业版工单响应时长是多少？",
        cast("Sequence[Passage]", ROUNDS),
        tracer=tracer,
        record_excerpts={},
    )
    assert audit.fail_open is False
    assert [action.action for action in audit.actions] == ["replace"]
    assert "model.request" in tracer.kinds and "model.response" in tracer.kinds


async def test_model_auditor_fails_open_on_model_error() -> None:
    """模型调用失败走 core 层 fail-open：放行原文、空判定，事件仍落当轮 tracer。"""
    model = ScriptedModel(RuntimeError("provider down"))
    auditor = ModelAuditor(model, evidence_budget=8000)
    tracer = TurnTracer()
    audit = await auditor.audit(
        DRAFT,
        "企业版工单响应时长是多少？",
        cast("Sequence[Passage]", ROUNDS),
        tracer=tracer,
        record_excerpts={},
    )
    assert audit.fail_open is True
    assert audit.text == DRAFT
    assert audit.actions == ()


async def test_model_auditor_forwards_record_excerpts() -> None:
    """记录摘录透传校验链（FR-1 在线口径）：绑定编号的断言进语义校验而非强制降级。"""
    sentence = "退款已提交 pay_bg_001_0003 [KB-BILL-003#c001]。"
    model = ScriptedModel(
        completion(
            json_payload(
                {
                    "assertions": [
                        {
                            "text": sentence,
                            "refs": ["KB-BILL-003#c001"],
                            "record_ids": ["pay_bg_001_0003"],
                        }
                    ]
                }
            )
        ),
        completion(json_payload({"verdicts": [{"text": sentence, "verdict": "supported"}]})),
    )
    auditor = ModelAuditor(model, evidence_budget=8000)
    tracer = TurnTracer()
    audit = await auditor.audit(
        sentence,
        "这笔退款的状态是什么？",
        cast("Sequence[Passage]", ROUNDS),
        tracer=tracer,
        record_excerpts={"pay_bg_001_0003": '{"payment_id": "pay_bg_001_0003"}'},
    )
    assert audit.fail_open is False
    assert [verdict.verdict for verdict in audit.verdicts] == ["supported"]
    assert len(model.requests) == 2  # 抽取 + 语义校验（摘录缺失时不会发生语义调用）
    # 摘录确实进入校验请求正文（可证伪：硬编码空摘录时该块不会出现）。
    assert '"payment_id"' in str(model.requests[-1]["messages"])
