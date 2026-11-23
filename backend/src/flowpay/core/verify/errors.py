"""fail-open 包装（specs/010 FR-6）：校验器故障永不阻断回答。

装配、抽取、ID 核对、批量校验、处置任一环节失败（模型调用失败、输出解析失败、
证据装配异常、代码级核对异常）都放行原文：fail_open=True、判定与处置为空、
不抛出、不产生 run.failed、不改变轮次终态。取消随 CancelledError 自然穿透
（BaseException，002 先例）；费用已发生的调用照常入账用量。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from flowpay.core.chat.usage_view import UsageEntry, from_completion
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.verify.actions import apply_actions, map_actions
from flowpay.core.verify.assembly import assemble_evidence
from flowpay.core.verify.contracts import AssertionAudit
from flowpay.core.verify.extractor import extract_assertions
from flowpay.core.verify.verifier import verify_assertions

logger = logging.getLogger(__name__)


async def audit_response(
    chat: ChatClient,
    *,
    text: str,
    question: str,
    passages: Sequence[Passage],
    record_excerpts: Mapping[str, str],
    evidence_budget: int,
    usage_entries: list[UsageEntry] | None = None,
) -> AssertionAudit:
    """一次完整校验：装配 → 抽取 → 批量判定 → 处置，任一环节失败 fail-open。

    证据预算由调用方传入（运营配置面归调用方，本层不读配置）；空白文本零断言
    直通，不发生模型调用。usage_entries 是调用方的用量收集列表（shared 先例），
    成功的模型调用各追加一条。
    """
    if not text.strip():
        return AssertionAudit(text=text)
    try:
        bundle = assemble_evidence(
            text=text,
            question=question,
            passages=passages,
            record_excerpts=record_excerpts,
            evidence_budget=evidence_budget,
        )
        extraction = await extract_assertions(chat, text=text)
        if usage_entries is not None:
            usage_entries.append(from_completion(extraction.completion))
        if not extraction.assertions:
            return AssertionAudit(text=text)  # 零断言放行，不降级
        verification = await verify_assertions(
            chat,
            bundle=bundle,
            assertions=extraction.assertions,
            record_excerpts=record_excerpts,
        )
        if usage_entries is not None and verification.completion is not None:
            usage_entries.append(from_completion(verification.completion))
        actions = map_actions(verification.verdicts, bundle, record_excerpts)
        return AssertionAudit(
            verdicts=verification.verdicts,
            actions=actions,
            fail_open=False,
            text=apply_actions(text, actions),
        )
    except Exception:
        logger.exception("Assertion audit failed; fail-open with the original text")
        return AssertionAudit(fail_open=True, text=text)
