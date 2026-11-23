"""处置映射（specs/010 FR-5，research D5）：四档判定 → 处置动作的纯函数映射。

支持→放行；部分支持→句级替换收紧表述：原句全文按句级切分等值匹配，命中多处取
第一处，匹配失败该断言降级不替换；替换句的数字与编号必须为原句 ∪ 绑定证据的
子集，越界降级（防线②：改写不得引入未经校验的新事实）。证据不足→降级
「未能核实：原句」（沿用统一口径，幂等）；矛盾→同样降级，conflict 详情随判定
与事件携带（D10），不在回答中呈现分歧。处置零新增模型调用：收紧表述由校验调用
一次性给出。

两步分离：map_actions 产出动作清单（含事件载荷），apply_actions 把动作落到文本
（锚点等值匹配的首处替换；匹配不到的锚点跳过——宁可放行不误改）。词面与
workflows/multi_agent/citation_check 同口径——core 不得依赖 workflows，改动须同步。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from flowpay.core.chat.record_ids import BUSINESS_RECORD_ID
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.verify.assembly import EvidenceBundle, bare_id, business_record_ids, passage_refs
from flowpay.core.verify.contracts import Assertion, AssertionAction, AssertionVerdict

# 统一未证实口径（与 citation_check.UNVERIFIED_MARK 同词面）。
DOWNGRADE_MARK = "未能核实"

# Markdown 列表前缀；降级时原样保留在句前（与 citation_check.BULLET_PREFIX 同义）。
BULLET_PREFIX = re.compile(r"^(?:[-*+]\s+)")

# 句级切分：按中英文句号、感叹号，分隔符保留在句尾（与 citation_check.split_sentences
# 同义——处置锚点与筛选层必须同一份切分口径）。
_SENTENCE = re.compile(r"[^。！!?]+[。！!?]?")

# 数字词形：含小数；「3.5」与「3」不同词形，替换句不得把 3 写成 3.5。
NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def split_sentences(text: str) -> list[str]:
    """句级切分（处置锚点匹配口径）：逐行再切句，与筛选层同一份词面。"""
    return [sentence for line in text.splitlines() for sentence in _SENTENCE.findall(line)]


def downgrade_text(sentence: str) -> str:
    """一句的降级文本：「未能核实：原句」，Markdown 列表前缀原样保留。"""
    body = sentence
    bullet = BULLET_PREFIX.match(body)
    if bullet:
        body = body[len(bullet.group(0)) :]
    return f"{bullet.group(0) if bullet else ''}{DOWNGRADE_MARK}：{body}"


def map_actions(
    verdicts: Sequence[AssertionVerdict],
    bundle: EvidenceBundle,
    record_excerpts: Mapping[str, str],
) -> tuple[AssertionAction, ...]:
    """四档判定逐条映射处置动作；纯函数，零模型调用。

    partial 缺 revision 也降级（FR-4 防线，verifier 已归一，此处兜底）；已带统一
    口径的句子不再改写（处置幂等）。
    """
    actions: list[AssertionAction] = []
    for item in verdicts:
        sentence = item.assertion.text
        if item.verdict == "supported":
            actions.append(AssertionAction(sentence=sentence, action="keep"))
            continue
        if DOWNGRADE_MARK in sentence:
            # 幂等：处置后文本再过校验器不二次改写。
            actions.append(
                AssertionAction(sentence=sentence, action="keep", breakpoint_hint=_hint(item))
            )
            continue
        if (
            item.verdict == "partial"
            and item.revision
            # 句级等值匹配（D5）：锚点必须整句出现在待校验文本里，匹配失败降级不替换。
            and _locatable(sentence, bundle.text)
            and _within_corpus(item.revision, _bound_corpus(item.assertion, bundle, record_excerpts))
        ):
            actions.append(
                AssertionAction(
                    sentence=sentence,
                    action="replace",
                    revision=item.revision,
                    breakpoint_hint=_hint(item),
                )
            )
            continue
        actions.append(
            AssertionAction(
                sentence=sentence,
                action="downgrade",
                revision=downgrade_text(sentence),
                breakpoint_hint=_hint(item),
            )
        )
    return tuple(actions)


def apply_actions(text: str, actions: Sequence[AssertionAction]) -> str:
    """把处置动作落到文本：锚点等值匹配的首处替换；匹配不到的锚点跳过。

    keep 是空操作；replace/downgrade 以 sentence 全文为锚（句级等值，D5），列表
    前缀容忍，找不到就保留原文——宁可放行不误改。锚点处已是目标文本时跳过：
    锚点文本可能存活于替换结果内部（如降级口径包着原句），重复应用不叠加口径。
    """
    for action in actions:
        if action.action == "keep" or not action.sentence:
            continue
        revision = action.revision if action.action == "replace" else None
        if action.action == "downgrade":
            revision = action.revision or downgrade_text(action.sentence)
        if not revision:
            continue
        start = text.find(action.sentence)
        span = len(action.sentence)
        if start < 0:
            body = _body(action.sentence)
            start = text.find(body)
            span = len(body)
        # 幂等护栏：锚点处已是目标文本（锚点可存活于替换结果内部），或锚点紧邻
        # 统一口径前缀（已被降级包住），都不再叠加改写。
        already = text[start : start + len(revision)] == revision or text[:start].endswith(
            f"{DOWNGRADE_MARK}："
        )
        if start < 0 or already:
            continue
        text = text[:start] + revision + text[start + span :]
    return text


def _body(sentence: str) -> str:
    """句子去掉 Markdown 列表前缀的正文。"""
    return BULLET_PREFIX.sub("", sentence, count=1)


def _locatable(sentence: str, text: str) -> bool:
    """句级等值匹配（D5）：锚点（或其去前缀正文）整句出现在待校验文本里。"""
    return _body(sentence) in {_body(s) for s in split_sentences(text)}


def _hint(item: AssertionVerdict) -> str | None:
    """处置动作的断点提示：越界与矛盾生产侧可见，证据不足留给回放归类。"""
    if item.verdict == "partial":
        return "overreach"
    if item.verdict == "contradicted":
        return "contradicted"
    return None


def _bound_corpus(
    assertion: Assertion, bundle: EvidenceBundle, record_excerpts: Mapping[str, str]
) -> str:
    """替换句的合法语料：原句 ∪ 绑定证据原文（断言引用的片段与记录摘录）。"""
    parts = [assertion.text]
    for ref in assertion.refs:
        passage = _passage_for(ref, bundle.passages)
        if passage is not None:
            parts.append(passage.text)
    for identifier in assertion.record_ids:
        value = record_excerpts.get(identifier) or ""
        if value.strip():
            parts.append(value)
    return "\n".join(parts)


def _passage_for(ref: str, passages: Sequence[Passage]) -> Passage | None:
    """按引用找片段原文：doc: 前缀两种形态都对齐。"""
    for passage in passages:
        if passage.id == ref or bare_id(passage.id) == ref:
            return passage
    return None


def _within_corpus(revision: str, corpus: str) -> bool:
    """替换句的数字与记录编号、片段引用必须为语料子集（spec 防线②）。"""
    numbers = set(NUMBER_TOKEN.findall(corpus))
    if any(token not in numbers for token in NUMBER_TOKEN.findall(revision)):
        return False
    corpus_refs = set(passage_refs(corpus))
    if any(ref not in corpus_refs for ref in passage_refs(revision)):
        return False
    corpus_ids = set(business_record_ids(corpus))
    return all(identifier in corpus_ids for identifier in business_record_ids(revision))
