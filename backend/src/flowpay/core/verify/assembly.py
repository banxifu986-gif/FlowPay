"""校验器的证据装配（specs/010 FR-1）：把本轮已登记证据组织成判定的唯一口径。

输入 = 待校验文本 + 本轮用户问题 + 本轮片段集 + 记录摘录映射，全部来自本轮既有
证据，零新增检索调用。片段按 score 降序、按入参字符预算截取（默认 8000 由调用方
传入，本层不读配置）；绑定引用原文不受预算影响（判定主证据优先）。摘录缺失记入
missing_records 参与判定、不阻塞装配；引用不存在于本轮片段集时保留在 refs 里，
由批量校验前的 ID 存在性核对强制判证据不足（FR-3）。

渲染块是抽取与批量校验共用的证据正文：在线档与回放对同一输入得到同一渲染，
保证回放结论可迁移（spec「回放与在线的同构性」）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from flowpay.core.chat.record_ids import BUSINESS_RECORD_ID, RECORD_ID
from flowpay.core.knowledge.contracts import Passage

# 片段引用词面：[doc:KB-TS-012#c001] 与裸 KB-TS-012#c001（模型主流输出，009 冒烟
# 实证）。与 workflows/multi_agent/citation_check.PASSAGE_REF 同义——core 不得依赖
# workflows，装配需要独立识别文本中的绑定引用；两边词形如需调整须同步。
PASSAGE_REF = re.compile(r"\[doc:[^\]]+\]|\bKB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*#c\d+\b")


def passage_refs(text: str) -> tuple[str, ...]:
    """文本中出现的片段引用，规范化为去方括号、去 doc: 前缀的形态；出现顺序去重。"""
    refs: list[str] = []
    for match in PASSAGE_REF.finditer(text):
        token = match.group(0).strip("[]")
        bare = token.removeprefix("doc:")
        if bare and bare not in refs:
            refs.append(bare)
    return tuple(refs)


def bare_id(passage_id: str) -> str:
    """片段 id 的裸形态（doc:KB-X#c1 → KB-X#c1）；引用按此形态对齐。"""
    return passage_id.removeprefix("doc:")


def business_record_ids(text: str) -> tuple[str, ...]:
    """文本里的业务记录编号（完整词形）。

    用宽松 RECORD_ID 取全词，再用强词形 search 过滤：强词形是前缀匹配（specs/009
    US4 备注），fullmatch 会拒绝 0003 这类多数字尾段；api_v2 这类「字母段尾带数字」
    的词形不算记录编号，不进摘录装配。
    """
    return tuple(
        identifier
        for identifier in dict.fromkeys(RECORD_ID.findall(text))
        if BUSINESS_RECORD_ID.search(identifier)
    )


def _matching_passage(ref: str, used: set[str], passages: Sequence[Passage]) -> Passage | None:
    """按引用找片段：doc: 前缀两种形态都对齐；已被绑定的片段不重复取。"""
    for candidate in passages:
        if candidate.id in used:
            continue
        if candidate.id == ref or bare_id(candidate.id) == ref:
            return candidate
    return None


@dataclass(frozen=True)
class EvidenceBundle:
    """装配产物：批量校验的证据集唯一口径（FR-1）。

    refs/record_ids 是待校验文本里出现的引用（绑定口径的来源）；bound_passages 是
    引用能对上的原文，known_ids 是本轮全部片段 id（含裸形态），ID 存在性按它核对
    而非被预算截断后的渲染集。passages 保留本轮全量片段（处置的子集校验按断言
    绑定解析证据原文，不受预算截断影响）。missing_records 的断言由批量校验侧按
    证据不足处置。
    """

    text: str
    question: str
    refs: tuple[str, ...]
    record_ids: tuple[str, ...]
    known_ids: frozenset[str]
    passages: tuple[Passage, ...]
    bound_passages: tuple[Passage, ...]
    ranked_passages: tuple[Passage, ...]
    record_excerpts: tuple[tuple[str, str], ...]
    missing_records: tuple[str, ...]


def assemble_evidence(
    *,
    text: str,
    question: str,
    passages: Sequence[Passage],
    record_excerpts: Mapping[str, str],
    evidence_budget: int,
) -> EvidenceBundle:
    """装配一次校验的证据集；预算只截全轮片段，摘录缺失不阻塞。"""
    refs = passage_refs(text)
    used: set[str] = set()
    bound: list[Passage] = []
    for ref in refs:
        matched = _matching_passage(ref, used, passages)
        if matched is not None:
            bound.append(matched)
            used.add(matched.id)
    ranked: list[Passage] = []
    remaining_chars = evidence_budget
    for candidate in sorted((p for p in passages if p.id not in used), key=lambda p: -p.score):
        if remaining_chars <= 0:
            break
        ranked.append(candidate)
        remaining_chars -= len(candidate.text)
    ids: tuple[str, ...] = ()
    excerpts: list[tuple[str, str]] = []
    missing: list[str] = []
    for identifier in business_record_ids(text):
        ids += (identifier,)
        value = record_excerpts.get(identifier) or ""
        if value.strip():
            excerpts.append((identifier, value))
        else:
            missing.append(identifier)
    return EvidenceBundle(
        text=text,
        question=question,
        refs=refs,
        record_ids=ids,
        known_ids=frozenset(
            identifier for passage in passages for identifier in (passage.id, bare_id(passage.id))
        ),
        passages=tuple(passages),
        bound_passages=tuple(bound),
        ranked_passages=tuple(ranked),
        record_excerpts=tuple(excerpts),
        missing_records=tuple(missing),
    )


def render_evidence(bundle: EvidenceBundle) -> str:
    """把证据集渲染成判定 prompt 的证据正文；空段整体省略，全空返回空串。"""
    sections: list[str] = []
    if bundle.question:
        sections.append(f"## 本轮用户问题\n{bundle.question}")
    if bundle.text:
        sections.append(f"## 待校验文本\n{bundle.text}")
    if bundle.bound_passages:
        lines = [_passage_line(p) for p in bundle.bound_passages]
        sections.append("## 引用片段原文\n" + "\n\n".join(lines))
    if bundle.ranked_passages:
        lines = [_passage_line(p) for p in bundle.ranked_passages]
        sections.append("## 本轮知识片段（按相似度）\n" + "\n\n".join(lines))
    if bundle.record_excerpts:
        body = "\n".join(f"{identifier}：{value}" for identifier, value in bundle.record_excerpts)
        sections.append("## 记录摘录\n" + body)
    if bundle.missing_records:
        body = "、".join(bundle.missing_records)
        sections.append(f"## 证据缺失\n{body}：无对应记录摘录（相关断言按证据不足处理）")
    return "\n\n".join(sections)


def _passage_line(passage: Passage) -> str:
    """单片段渲染：裸 id 便于模型按文本里的引用词形对照，分数保留相似度语境。"""
    title = f" {passage.title}" if passage.title else ""
    return f"[{bare_id(passage.id)}]{title}（相似度 {passage.score:.3f}）\n{passage.text}"
