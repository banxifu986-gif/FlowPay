"""批量语义校验（specs/010 FR-3/FR-4）：LLM 一次调用逐断言判定四档。

判定前先做代码级核对（FR-3）：引用不存在于本轮证据集、记录摘录缺失、无任何绑定
的断言直接判证据不足，不经语义校验（同时补上片段引用 ID 真伪缺口）。partial 必须
附 overreach 与 revision，缺失视同 insufficient（spec FR-4 默认假设）；contradicted
附 conflict，缺失保留矛盾判定但详情为空——降级处置不受影响，矛盾档的断点信号不丢。
证据集口径 = 证据装配结果（EvidenceBundle 渲染块）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from openai.types.chat import ChatCompletion

from flowpay.core.chat.record_ids import BUSINESS_RECORD_ID
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.llm.chat.messages import answer_text
from flowpay.core.verify.assembly import EvidenceBundle, render_evidence
from flowpay.core.verify.contracts import (
    Assertion,
    AssertionVerdict,
    Conflict,
    Overreach,
)
from flowpay.core.verify.extractor import decode_json

VERIFY_SYSTEM_PROMPT = (
    "你是 FlowPay 技术支持助手的逐断言事实核查员。对照证据逐条判定断言，"
    '只输出 JSON 对象：{"verdicts": [{"text": "...", "verdict": "supported|partial|'
    'contradicted|insufficient", "overreach": null, "revision": null, "conflict": null}]}。\n'
    "四档判定：\n"
    "- supported：证据原文完整支持断言的全部事实与限定（对象、时间、数字、条件）；\n"
    "- partial：方向正确但表述越界，必须附 overreach（dimension 取 对象/时间/数字/条件 之一，"
    "note 说明原文支持什么、表述越界到什么）与 revision（收紧到原文支持范围的完整原句，"
    "保留原句引用标记，数字与编号只能来自原句或证据）；\n"
    "- contradicted：证据与断言冲突，必须附 conflict（sources=冲突双方出处，point=冲突点）；\n"
    "- insufficient：证据不足以判定。\n"
    "典型越界样例：\n"
    "- 适用对象扩大：原文「企业版工单通常 5 个工作日响应」被写成「所有用户最多等 5 天」→"
    "dimension=对象；\n"
    "- 把「通常」写成保证：原文「通常 5 个工作日」被写成「保证 5 个工作日内响应」→"
    "dimension=条件；\n"
    "- 时限限定丢失：原文「5 个工作日」被写成「5 天」→dimension=时间。\n"
    "规则：text 原样回传断言文本；不确定就判 insufficient，宁可降级不误放行；"
    "除 JSON 外不要输出任何其他文字。"
)

# 越界维度词形与契约 OverreachDimension 一致。
_DIMENSIONS = ("对象", "时间", "数字", "条件")


@dataclass(frozen=True)
class Verification:
    """一次批量校验的产物：判定清单与原始响应（无语义调用时响应为空）。"""

    verdicts: tuple[AssertionVerdict, ...]
    completion: ChatCompletion | None


def _listing(assertions: Sequence[Assertion]) -> str:
    """待判定断言清单：绑定信息随清单一起给模型，摘录型断言才有判定依据。"""
    return json.dumps(
        [
            {"text": a.text, "refs": list(a.refs), "record_ids": list(a.record_ids)}
            for a in assertions
        ],
        ensure_ascii=False,
    )


def _user_prompt(
    bundle: EvidenceBundle,
    assertions: Sequence[Assertion],
    record_excerpts: Mapping[str, str],
) -> str:
    """校验请求正文：证据块（装配渲染）+ 断言绑定摘录 + 断言清单。

    断言绑定的记录编号若不在装配渲染里（模型抄录了正文词形之外的编号），把
    调用方映射里的对应摘录补进证据块——FR-1 口径按断言绑定装配摘录。
    """
    rendered = render_evidence(bundle)
    bound = {identifier for a in assertions for identifier in a.record_ids}
    shown = {identifier for identifier, _ in bundle.record_excerpts}
    supplemental = [
        f"{identifier}：{record_excerpts[identifier]}"
        for identifier in sorted(bound - shown)
        if identifier in record_excerpts and record_excerpts[identifier].strip()
    ]
    if supplemental:
        rendered += "\n\n## 断言记录摘录\n" + "\n".join(supplemental)
    return f"{rendered}\n\n## 待判定断言\n{_listing(assertions)}"


def _overreach(raw: object) -> Overreach | None:
    """越界定位解析；维度不在四档词形或说明空白视为缺失。"""
    if not isinstance(raw, dict):
        return None
    dimension = raw.get("dimension")
    note = raw.get("note")
    if dimension not in _DIMENSIONS or not (isinstance(note, str) and note.strip()):
        return None
    return Overreach(dimension=cast("str", dimension), note=note)


def _conflict(raw: object) -> Conflict | None:
    """矛盾详情解析；出处与冲突点任一缺失视为无详情（判定档位保留）。"""
    if not isinstance(raw, dict):
        return None
    sources = [value for value in require_list(raw.get("sources", [])) if isinstance(value, str)]
    point = raw.get("point")
    if not sources or not (isinstance(point, str) and point.strip()):
        return None
    return Conflict(sources=tuple(sources), point=point)


def parse_verdicts(
    completion: ChatCompletion, pending: Sequence[Assertion]
) -> dict[str, AssertionVerdict]:
    """解析批量校验响应：按 text 回传对齐断言；结构坏上抛，条目坏归一证据不足。"""
    body = require_object(decode_json(answer_text(completion)))
    items = require_list(body.get("verdicts"))
    by_text = {assertion.text: assertion for assertion in pending}
    found: dict[str, AssertionVerdict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        assertion = by_text.get(text) if isinstance(text, str) else None
        if assertion is None:
            continue  # 对不上的条目跳过，对应断言由调用方兜底
        found[text] = _verdict(assertion, item)
    return found


def _verdict(assertion: Assertion, record: JsonObject) -> AssertionVerdict:
    """单条判定解析；partial 缺 overreach/revision 视同 insufficient（FR-4）。"""
    word = record.get("verdict")
    if word == "supported":
        return AssertionVerdict(assertion, "supported")
    if word == "partial":
        overreach = _overreach(record.get("overreach"))
        revision = record.get("revision")
        if overreach is None or not (isinstance(revision, str) and revision.strip()):
            return AssertionVerdict(assertion, "insufficient")
        return AssertionVerdict(assertion, "partial", overreach=overreach, revision=revision)
    if word == "contradicted":
        return AssertionVerdict(
            assertion, "contradicted", conflict=_conflict(record.get("conflict"))
        )
    return AssertionVerdict(assertion, "insufficient")


def screen_assertions(
    assertions: Sequence[Assertion],
    bundle: EvidenceBundle,
    record_excerpts: Mapping[str, str],
) -> tuple[dict[str, AssertionVerdict], tuple[Assertion, ...]]:
    """FR-3 代码级核对：返回（强制证据不足的判定，待语义校验的断言）。

    摘录核对按调用方映射查断言绑定的编号（FR-1：断言中记录编号对应摘录），
    而非装配时按正文词形预筛的渲染集。
    """
    forced: dict[str, AssertionVerdict] = {}
    pending: list[Assertion] = []
    for assertion in assertions:
        if _binding_gap(assertion, bundle, record_excerpts) is not None:
            forced[assertion.text] = AssertionVerdict(assertion, "insufficient")
        else:
            pending.append(assertion)
    return forced, tuple(pending)


def _binding_gap(
    assertion: Assertion, bundle: EvidenceBundle, record_excerpts: Mapping[str, str]
) -> str | None:
    """断言绑定缺口：未知引用 / 摘录缺失 / 无任何绑定；可判定返回 None。"""
    if any(ref not in bundle.known_ids for ref in assertion.refs):
        return "ref-missing"
    if not assertion.refs and not assertion.record_ids:
        return "no-binding"
    if any(
        identifier not in record_excerpts or not record_excerpts[identifier].strip()
        for identifier in assertion.record_ids
        if BUSINESS_RECORD_ID.search(identifier)
    ):
        return "excerpt-missing"
    return None


async def verify_assertions(
    chat: ChatClient,
    *,
    bundle: EvidenceBundle,
    assertions: Sequence[Assertion],
    record_excerpts: Mapping[str, str],
) -> Verification:
    """批量校验一次调用：代码级核对在前，语义校验只送可判定的断言。

    全部断言被强制证据不足（或无断言）时不发生语义调用；对不上回传的断言按
    证据不足兜底——宁可降级不误放行。
    """
    if not assertions:
        return Verification(verdicts=(), completion=None)
    forced, pending = screen_assertions(assertions, bundle, record_excerpts)
    completion: ChatCompletion | None = None
    judged: dict[str, AssertionVerdict] = {}
    if pending:
        completion, _ = await chat.call(
            [
                {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(bundle, pending, record_excerpts)},
            ],
            output="internal",
        )
        judged = parse_verdicts(completion, pending)
    verdicts = tuple(
        forced.get(assertion.text)
        or judged.get(assertion.text)
        or AssertionVerdict(assertion, "insufficient")
        for assertion in assertions
    )
    return Verification(verdicts=verdicts, completion=completion)
