# 回归测试：fail-open 包装（specs/010 FR-6）。固定输入复现边界，不作为业务答案来源。
import asyncio
from collections.abc import Sequence
from typing import cast

import pytest

from flowpay.core.chat.usage_view import UsageEntry
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.verify.errors import audit_response
from tests.core.verify_fixtures import (
    ROUNDS,
    RecordingSink,
    ScriptedModel,
    chat_client,
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


def chat_with(*responses: object) -> tuple[ChatClient, ScriptedModel]:
    """装配脚本化模型（响应或异常按序弹出；取消属 BaseException 也要能注入）。"""
    items = tuple(
        response if isinstance(response, BaseException) else completion(cast("str", response))
        for response in responses
    )
    model = ScriptedModel(*items)
    return chat_client(model, RecordingSink()), model


async def test_audit_full_chain_replaces_and_records_usage() -> None:
    """全链路：抽取+批量校验+处置，替换落最终文本，两次调用入账用量。"""
    chat, model = chat_with(json_payload(EXTRACTION), json_payload(VERDICT_PARTIAL))
    usage: list[UsageEntry] = []
    audit = await audit_response(
        chat,
        text=DRAFT,
        question="企业版工单响应时长是多少？",
        passages=ROUNDS,
        record_excerpts={},
        evidence_budget=8000,
        usage_entries=usage,
    )
    assert audit.fail_open is False
    assert audit.text.startswith("企业版工单通常 5 个工作日响应")
    assert [a.action for a in audit.actions] == ["replace"]
    assert len(audit.verdicts) == 1 and len(usage) == 2
    assert len(model.requests) == 2  # 抽取与批量校验各一次


async def test_audit_zero_assertions_passes_through_without_verification_call() -> None:
    """无断言零判定直接放行：只有抽取一次调用。"""
    chat, model = chat_with(json_payload({"assertions": []}))
    audit = await audit_response(
        chat,
        text="请按以下步骤操作。",
        question="怎么操作？",
        passages=(),
        record_excerpts={},
        evidence_budget=8000,
    )
    assert audit.verdicts == () and audit.actions == ()
    assert audit.text == "请按以下步骤操作。" and audit.fail_open is False
    assert len(model.requests) == 1


async def test_audit_blank_text_short_circuits() -> None:
    """空文本零调用直通。"""
    chat, model = chat_with(RuntimeError("不应被调用"))
    audit = await audit_response(
        chat, text="  ", question="", passages=(), record_excerpts={}, evidence_budget=8000
    )
    assert audit.text == "  " and audit.verdicts == () and model.requests == []


async def test_audit_contradicted_carries_conflict_on_verdict() -> None:
    """矛盾判定：降级处置 + conflict 详情随 verdicts 条目内联（D10 载荷口径）。"""
    verdict_payload = {
        "verdicts": [
            {
                "text": SENTENCE,
                "verdict": "contradicted",
                "conflict": {"sources": ["KB-TS-012#c001"], "point": "原文与表述冲突"},
            }
        ]
    }
    chat, _ = chat_with(json_payload(EXTRACTION), json_payload(verdict_payload))
    audit = await audit_response(
        chat, text=DRAFT, question="?", passages=ROUNDS, record_excerpts={}, evidence_budget=8000
    )
    assert [a.action for a in audit.actions] == ["downgrade"]
    assert audit.verdicts[0].conflict is not None
    assert audit.verdicts[0].conflict.point == "原文与表述冲突"


@pytest.mark.parametrize(
    "kind", ["model_failure", "extract_parse_failure", "verify_parse_failure", "assembly_failure"]
)
async def test_audit_failures_fail_open_with_original_text(kind: str) -> None:
    """任一环节失败：放行原文 + fail_open=True + 判定与处置为空，不抛出。"""
    if kind == "model_failure":
        chat, _ = chat_with(RuntimeError("model offline"))
    elif kind == "extract_parse_failure":
        chat, _ = chat_with("不是 JSON")
    elif kind == "verify_parse_failure":
        chat, _ = chat_with(json_payload(EXTRACTION), "不是 JSON")
    else:
        passage = ROUNDS[0]

        class Exploding:
            """score 读数即炸的片段替身，注入装配异常。"""

            id: str = passage.id
            title: str = passage.title
            text: str = passage.text

            @property
            def score(self) -> float:
                raise RuntimeError("boom")

        chat, _ = chat_with(json_payload(EXTRACTION))
        audit = await audit_response(
            chat,
            text=DRAFT,
            question="?",
            passages=cast("Sequence[Passage]", (Exploding(),)),
            record_excerpts={},
            evidence_budget=8000,
        )
        assert audit.fail_open is True and audit.text == DRAFT
        assert audit.verdicts == () and audit.actions == ()
        return
    audit = await audit_response(
        chat, text=DRAFT, question="?", passages=ROUNDS, record_excerpts={}, evidence_budget=8000
    )
    assert audit.fail_open is True and audit.text == DRAFT
    assert audit.verdicts == () and audit.actions == ()


async def test_id_check_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-3 代码级核对异常同样 fail-open（注入到核对函数）。"""
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("id check boom")

    monkeypatch.setattr("flowpay.core.verify.verifier.screen_assertions", explode)
    chat, _ = chat_with(json_payload(EXTRACTION), json_payload(VERDICT_PARTIAL))
    audit = await audit_response(
        chat, text=DRAFT, question="?", passages=ROUNDS, record_excerpts={}, evidence_budget=8000
    )
    assert audit.fail_open is True and audit.text == DRAFT


async def test_cancelled_error_propagates() -> None:
    """取消穿透：校验器故障兜底不得吞掉取消信号（002 先例）。"""
    chat, _ = chat_with(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await audit_response(
            chat,
            text=DRAFT,
            question="?",
            passages=ROUNDS,
            record_excerpts={},
            evidence_budget=8000,
        )


async def test_failed_chain_after_successful_call_still_records_usage() -> None:
    """费用已发生的调用照常入账：抽取成功、校验故障，仍记一次用量（002 先例）。"""
    chat, _ = chat_with(json_payload(EXTRACTION), RuntimeError("verify offline"))
    usage: list[UsageEntry] = []
    audit = await audit_response(
        chat,
        text=DRAFT,
        question="?",
        passages=ROUNDS,
        record_excerpts={},
        evidence_budget=8000,
        usage_entries=usage,
    )
    assert audit.fail_open is True and len(usage) == 1
