#!/usr/bin/env python3
"""离线检索评测：量化"题库必需文档能不能被召回到"（specs/007 US5）。

只走嵌入与知识检索，不调用聊天模型。两种查询口径（首条消息 / 首条消息 + 全部可披露
事实）与两条检索路径（纯向量 / 混合）各检索一次 top-N，再按前缀计算 recall@k。
题库的 `required_documents` / `planned_evidence_ids` 只作为评测标签，产品运行时不读它。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from flowpay.core.config import load_settings
from flowpay.core.event_log.types import Event
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_list,
    require_object,
    require_text,
)
from flowpay.core.knowledge import HybridConfig, PostgresVectorStore
from flowpay.core.llm.embedding import EmbeddedBatch, EmbeddingClient
from flowpay.core.llm.embedding.openai_embedding import OpenAiEmbeddingModel
from flowpay.core.llm.embedding.settings import same_vector_space

ROOT = Path(__file__).resolve().parents[2]
# 同时支持 python scripts/benchmark/retrieval.py 与 python -m scripts.benchmark.retrieval。
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from flowpay.core.knowledge.contracts import Passage

from scripts.benchmark.run import blueprints, sample

# 召回评测的 k 曲线；默认单次检索条数覆盖最大的 k。
K_VALUES: tuple[int, ...] = (1, 3, 5, 10, 20)
DIALECTS: tuple[str, ...] = ("initial", "observed", "actual", "full")
PATHS: tuple[str, ...] = ("vector", "hybrid")
DOC_PREFIX = "doc:"

# 判定阈值：full-initial 的 recall@10 提升达到 10pp 即视为"查询缺信息"是主因。
INFO_GAP_PP = 10.0
# 两口径都低于这个 recall@10 时，判为召回本身不足。
LOW_RECALL = 0.5


class Searcher(Protocol):
    """一次检索的入口：给定集合、查询文本与条数，返回按相关度排序的片段。"""

    async def search(
        self, collection_id: str, text: str, limit: int
    ) -> list[Passage]: ...


class StoreSearcher:
    """用预取好的查询向量直接检索：语义与 KnowledgeClient 内部调用完全一致。

    Ollama 单次嵌入请求的固定开销远大于批大小，96 题的两种口径 × 两条路径会把
    同一段文本嵌入四遍；这里先批量预取向量的原因，检索时只做向量/混合查询，
    可见范围仍用 internal 视图（filters=None）。
    """

    def __init__(
        self,
        store: PostgresVectorStore,
        collection_id: str,
        vectors: Mapping[str, EmbeddedBatch],
        hybrid: HybridConfig | None = None,
    ) -> None:
        """绑定存储、集合、预取向量与可选的混合配置。"""
        self.store: PostgresVectorStore = store
        self.collection_id: str = collection_id
        self.vectors: Mapping[str, EmbeddedBatch] = vectors
        self.hybrid: HybridConfig | None = hybrid

    async def search(self, collection_id: str, text: str, limit: int) -> list[Passage]:
        """按预取向量检索一次；混合路径再把原始查询文本交给关键词路。"""
        batch = self.vectors[text]
        if self.hybrid is None:
            return await asyncio.to_thread(
                self.store.search, collection_id, batch, limit, None
            )
        return await asyncio.to_thread(
            self.store.search_hybrid,
            collection_id,
            batch,
            text,
            limit,
            None,
            keyword_limit=self.hybrid.keyword_limit,
            vector_limit=self.hybrid.vector_limit,
            rrf_k=self.hybrid.rrf_k,
        )


class BatchEmbedder(Protocol):
    """批量嵌入入口；预取只需要批大小与按批返回向量的能力。"""

    @property
    def batch_size(self) -> int:
        """每次请求允许的文本条数。"""
        ...

    async def embed(self, texts: list[str]) -> EmbeddedBatch:
        """按批生成向量，保持输入顺序。"""
        ...


class NullSink:
    """空事件出口：离线评测不写事件库，也不产生任何外部追踪。"""

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> Event:
        """返回一条内存事件；评测脚本没有会话与运行上下文，不落盘。"""
        return {
            "schema_version": 1,
            "event_id": "offline-retrieval",
            "session_id": "offline-retrieval",
            "seq": 0,
            "run_id": None,
            "call_id": call_id,
            "parent_call_id": parent_call_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": kind,
            "payload": payload,
        }


@dataclass(frozen=True)
class Case:
    """一道评测样本：四种查询口径 + 必需文档集合。

    observed 是"真实臂里用户实际说过的话"：首条消息加该会话后续的用户回复。
    actual 是产品改写的最终输入：首条消息加最终检查点里实际收集到的主题值
    （SC-5 的验收口径）。没有对应臂时，两者都等于 initial。
    """

    question_id: str
    question_type: str
    initial: str
    observed: str
    actual: str
    full: str
    documents: tuple[str, ...]

    def text(self, dialect: str) -> str:
        """按口径取查询文本；未知口径直接报错。"""
        if dialect == "initial":
            return self.initial
        if dialect == "observed":
            return self.observed
        if dialect == "actual":
            return self.actual
        if dialect == "full":
            return self.full
        raise KeyError(dialect)


@dataclass(frozen=True)
class RetrievalRow:
    """一道题的召回结果：按"口径:路径"保存按排名去重的文档编号。"""

    question_id: str
    question_type: str
    ranked: dict[str, list[str]]

    def documents(self, dialect: str, path: str) -> list[str]:
        """某个口径/路径上按排名去重的文档编号；未采集的组合返回空列表。"""
        return self.ranked.get(f"{dialect}:{path}", [])


def document_ids(passages: Sequence[Passage]) -> list[str]:
    """按片段顺序取文档编号，同文档的多个片段只保留首次出现。"""
    seen: list[str] = []
    for passage in passages:
        doc_id = passage.source.doc_id
        if doc_id not in seen:
            seen.append(doc_id)
    return seen


def recall_at(needed: Sequence[str], found: Sequence[str], k: int) -> float:
    """必需文档中落在前 k 个片段里的比例；没有必需文档时为 0（不计入汇总）。"""
    if not needed:
        return 0.0
    top = set(found[:k])
    return sum(1 for document in needed if document in top) / len(needed)


def required_documents_of(case: JsonObject) -> tuple[str, ...]:
    """必需文档：required_documents 的文档编号加 planned_evidence_ids 的 doc: 项。

    规则编号、记录编号等其他证据标识不参与文档召回评测，直接忽略。
    """
    documents: list[str] = []
    for document in require_list(case.get("required_documents") or []):
        name = require_text(document)
        if name not in documents:
            documents.append(name)
    for evidence in require_list(case.get("planned_evidence_ids") or []):
        item = require_text(evidence)
        if not item.startswith(DOC_PREFIX):
            continue
        name = item[len(DOC_PREFIX) :].split("#", 1)[0]
        if name and name not in documents:
            documents.append(name)
    return tuple(documents)


def has_labels(case: JsonObject) -> bool:
    """这道题是否带有可评测的必需文档标签。"""
    return bool(required_documents_of(case))


def query_texts_of(case: JsonObject) -> tuple[str, str]:
    """两种查询口径：首条消息；以及首条消息加全部可披露事实。"""
    talk = require_object(case["conversation"])
    multi = require_text(talk["mode"]) == "progressive_disclosure"
    initial = require_text(talk["initial_user_message"] if multi else case["question"])
    facts = [
        require_text(require_object(fact)["user_value"])
        for fact in require_list(talk.get("available_user_facts") or [])
    ]
    full = " ".join(
        [" ".join(initial.split()), *(" ".join(fact.split()) for fact in facts)]
    )
    return initial, full


def sample_of(case: JsonObject) -> Case:
    """把题库元数据收成评测样本；只保留检索需要的字段。"""
    initial, full = query_texts_of(case)
    return Case(
        question_id=require_text(case["question_id"]),
        question_type=require_text(case["question_type"]),
        initial=initial,
        observed=initial,
        actual=initial,
        full=full,
        documents=required_documents_of(case),
    )


@dataclass(frozen=True)
class ObservedStats:
    """observed 口径的样本统计：有多少会话匹配到题目、多少被跳过。"""

    matched: int
    skipped: int


def normalize(text: str) -> str:
    """空白归一化：把连续空白折成单个空格，去掉首尾空白。"""
    return " ".join(text.split())


def read_sessions(arm: Path) -> list[tuple[str, list[str]]]:
    """读一个臂会话库：按 session.created 排好的会话，以及每个会话的用户消息序列。"""
    path = arm / "multi-agent" / "sessions.sqlite"
    if not path.exists():
        raise ValueError(f"no session store under {arm}")
    with sqlite3.connect(path) as db:
        rows = cast(
            "list[tuple[str, int, str, str]]",
            db.execute(
                "SELECT session_id, seq, type, data FROM events"
                + " WHERE type IN ('session.created','message.user') ORDER BY seq"
            ).fetchall(),
        )
    starts: list[str] = []
    users: dict[str, list[str]] = {}
    for session_id, _seq, kind, data in rows:
        record = require_object(parse_json(require_text(data)))
        if kind == "session.created":
            if session_id not in starts:
                starts.append(session_id)
            continue
        payload = require_object(record["payload"])
        users.setdefault(session_id, []).append(require_text(payload["content"]))
    return [(session_id, users.get(session_id, [])) for session_id in starts]


def observed_queries(
    cases: Sequence[Case],
    arm: Path | None,
    initial: Mapping[str, str] | None = None,
    questions: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], ObservedStats]:
    """构造 observed 查询：首条用户消息匹配题目后，拼上该会话后续的全部用户回复。

    题库只用于首条消息匹配（initial 或题目正文）；匹配不到的会话跳过并计数，
    绝不猜它属于哪道题。没有臂或没有后续回复时，observed 等于 initial。
    """
    initial_texts = (
        dict(initial)
        if initial is not None
        else {case.question_id: case.initial for case in cases}
    )
    if arm is None:
        return initial_texts, ObservedStats(0, 0)
    matchers: dict[str, str] = {}
    for question_id, text in initial_texts.items():
        matchers.setdefault(normalize(text), question_id)
    for question_id, text in (questions or {}).items():
        matchers.setdefault(normalize(text), question_id)
    observed = dict(initial_texts)
    matched = 0
    skipped = 0
    for _session_id, messages in read_sessions(arm):
        if not messages:
            continue
        question_id = matchers.get(normalize(messages[0]))
        if question_id is None:
            skipped += 1
            continue
        matched += 1
        replies = [normalize(message) for message in messages[1:] if normalize(message)]
        observed[question_id] = " ".join([normalize(messages[0]), *replies])
    return observed, ObservedStats(matched, skipped)


def apply_observed(cases: Sequence[Case], observed: Mapping[str, str]) -> list[Case]:
    """把 observed 文本写回样本；缺失的题保持 initial。"""
    return [
        replace_dialect(case, observed.get(case.question_id, case.initial))
        for case in cases
    ]


def apply_actual(cases: Sequence[Case], actual: Mapping[str, str]) -> list[Case]:
    """把 actual 文本写回样本；缺失的题保持 initial。"""
    return [
        replace_actual(case, actual.get(case.question_id, case.initial))
        for case in cases
    ]


def read_answers(arm: Path) -> list[tuple[str, str, dict[str, str]]]:
    """读臂会话库：每个会话的首条用户消息与最新检查点的已回答主题值。

    会话按首次出现顺序返回；检查点里的 answers 是主题 → 值，最后一次
    run.finished 的状态就是最终澄清状态（跨轮覆盖）。
    """
    path = arm / "multi-agent" / "sessions.sqlite"
    if not path.exists():
        raise ValueError(f"no session store under {arm}")
    with sqlite3.connect(path) as db:
        rows = cast(
            "list[tuple[str, int, str, str]]",
            db.execute(
                "SELECT session_id, seq, type, data FROM events"
                + " WHERE type IN ('session.created','message.user','run.finished')"
                + " ORDER BY seq"
            ).fetchall(),
        )
    starts: list[str] = []
    firsts: dict[str, str] = {}
    answers: dict[str, dict[str, str]] = {}
    for session_id, _seq, kind, data in rows:
        record = require_object(parse_json(require_text(data)))
        if kind == "session.created":
            if session_id not in starts:
                starts.append(session_id)
            continue
        payload = require_object(record["payload"])
        if kind == "message.user":
            firsts.setdefault(session_id, require_text(payload["content"]))
            continue
        try:
            state = require_object(payload.get("state") or {})
            clarification = require_object(state.get("clarification") or {})
            stored = require_object(clarification.get("answers") or {})
        except ValueError:
            continue
        answers[session_id] = {
            str(name): str(value)
            for name, value in stored.items()
            if str(value).strip()
        }
    return [
        (session_id, firsts.get(session_id, ""), answers.get(session_id, {}))
        for session_id in starts
    ]


def actual_queries(
    cases: Sequence[Case],
    arm: Path | None,
    initial: Mapping[str, str] | None = None,
    questions: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], ObservedStats]:
    """构造 actual 查询：首条消息匹配题目后，拼上最终检查点实际收集到的主题值。

    与 observed_queries 同一套匹配规则：首条消息匹配不到的会话跳过并计数，
    不猜它属于哪道题。没有臂时 actual 等于 initial（SC-5 的验收口径只在
    给出 --answers-arm 时生效）。
    """
    initial_texts = (
        dict(initial)
        if initial is not None
        else {case.question_id: case.initial for case in cases}
    )
    if arm is None:
        return initial_texts, ObservedStats(0, 0)
    matchers: dict[str, str] = {}
    for question_id, text in initial_texts.items():
        matchers.setdefault(normalize(text), question_id)
    for question_id, text in (questions or {}).items():
        matchers.setdefault(normalize(text), question_id)
    actual = dict(initial_texts)
    matched = 0
    skipped = 0
    for _session_id, first_message, collected in read_answers(arm):
        if not first_message:
            continue
        question_id = matchers.get(normalize(first_message))
        if question_id is None:
            skipped += 1
            continue
        matched += 1
        values = [normalize(value) for value in collected.values() if normalize(value)]
        actual[question_id] = " ".join(
            [normalize(initial_texts.get(question_id, "")), *values]
        )
    return actual, ObservedStats(matched, skipped)


def replace_dialect(case: Case, observed: str) -> Case:
    """生成 observed 口径更新后的样本副本。"""
    return Case(
        question_id=case.question_id,
        question_type=case.question_type,
        initial=case.initial,
        observed=observed,
        actual=case.actual,
        full=case.full,
        documents=case.documents,
    )


def replace_actual(case: Case, actual: str) -> Case:
    """生成 actual 口径更新后的样本副本。"""
    return Case(
        question_id=case.question_id,
        question_type=case.question_type,
        initial=case.initial,
        observed=case.observed,
        actual=actual,
        full=case.full,
        documents=case.documents,
    )


async def collect(
    cases: Sequence[Case],
    searchers: Mapping[str, Searcher],
    collection_id: str,
    limit: int,
) -> list[RetrievalRow]:
    """逐题逐口径逐路径检索一次，保存按排名去重的文档编号。"""
    rows: list[RetrievalRow] = []
    for case in cases:
        ranked: dict[str, list[str]] = {}
        for dialect in DIALECTS:
            text = case.text(dialect)
            for path in PATHS:
                searcher = searchers.get(path)
                if searcher is None:
                    continue
                ranked[f"{dialect}:{path}"] = document_ids(
                    await searcher.search(collection_id, text, limit)
                )
        rows.append(RetrievalRow(case.question_id, case.question_type, ranked))
    return rows


@dataclass(frozen=True)
class Aggregate:
    """汇总后的召回率：按题型与路径/口径给出各 k 的平均值。"""

    rows: list[RetrievalRow]
    labels: dict[str, tuple[str, ...]]
    counted: int
    unretrievable: list[str]

    def _mean(
        self, rows: Sequence[RetrievalRow], dialect: str, path: str, k: int
    ) -> float:
        """给定题集上 recall@k 的平均；没有标签题时为 0。"""
        if dialect not in DIALECTS:
            raise KeyError(dialect)
        values = [
            recall_at(self.labels[row.question_id], row.documents(dialect, path), k)
            for row in rows
            if self.labels.get(row.question_id)
        ]
        return sum(values) / len(values) if values else 0.0

    def mean(self, dialect: str, path: str = "vector", k: int = 10) -> float:
        """该口径/路径上 recall@k 的按题平均；未知口径直接报错。"""
        return self._mean(self.rows, dialect, path, k)

    def of_type(self, kind: str, dialect: str, path: str, k: int) -> float:
        """某个题型在同一口径/路径上的 recall@k。"""
        return self._mean(
            [row for row in self.rows if row.question_type == kind], dialect, path, k
        )

    def delta_at(
        self, k: int, path: str = "vector", dialect: str = "observed"
    ) -> float:
        """某口径相对 initial 的 recall@k 差值（以百分点表示）。"""
        return (self.mean(dialect, path, k) - self.mean("initial", path, k)) * 100.0


def aggregate(
    rows: Sequence[RetrievalRow], labels: Mapping[str, tuple[str, ...]]
) -> Aggregate:
    """按题汇总；没有必需文档的题不进入分母，从未命中的文档单独列出。"""
    retrieved: set[str] = set()
    for row in rows:
        for path in PATHS:
            for dialect in DIALECTS:
                retrieved.update(row.documents(dialect, path))
    unretrievable: list[str] = []
    for row in rows:
        for document in labels.get(row.question_id, ()):
            if document not in retrieved and document not in unretrievable:
                unretrievable.append(document)
    counted = sum(1 for row in rows if labels.get(row.question_id))
    return Aggregate(list(rows), dict(labels), counted, unretrievable)


def observed_delta(result: Aggregate) -> float:
    """observed 相对 initial 的 recall@10 提升（取两条路径里较大的那条）。"""
    return max(
        result.delta_at(10, "hybrid", "observed"),
        result.delta_at(10, "vector", "observed"),
    )


def actual_delta(result: Aggregate) -> float:
    """actual 相对 initial 的 recall@10 提升（SC-5 的最终产品口径）。"""
    return max(
        result.delta_at(10, "hybrid", "actual"), result.delta_at(10, "vector", "actual")
    )


def full_delta(result: Aggregate) -> float:
    """full 相对 initial 的 recall@10 提升（上界口径，仅作对照）。"""
    return max(
        result.delta_at(10, "hybrid", "full"), result.delta_at(10, "vector", "full")
    )


def verdict(result: Aggregate, use_actual: bool = False) -> str:
    """判定改造是否达标；给了 --answers-arm 时按 actual 口径（SC-5）。

    actual <10pp 按约定停下讨论，不硬凑指标；full 只作上界对照。
    """
    full = full_delta(result)
    if use_actual:
        actual = actual_delta(result)
        if actual >= INFO_GAP_PP:
            return (
                f"通过：actual-initial 的 recall@10 提升 {actual:+.1f}pp"
                f"（阈值 {INFO_GAP_PP:.0f}pp）"
            )
        if full >= INFO_GAP_PP:
            return (
                f"未通过：actual {actual:+.1f}pp 低于阈值（full 上界 {full:+.1f}pp）——"
                "按约定停下讨论补覆盖/调改写/撤换，不硬凑指标"
            )
        return f"未通过：召回侧瓶颈（actual {actual:+.1f}pp，full {full:+.1f}pp 都低于阈值）"
    observed = observed_delta(result)
    if observed >= INFO_GAP_PP:
        return f"通过：observed-initial 的 recall@10 提升 {observed:+.1f}pp（阈值 {INFO_GAP_PP:.0f}pp）"
    if full >= INFO_GAP_PP:
        return (
            f"未通过：澄清覆盖不足（observed {observed:+.1f}pp，"
            f"full 上界 {full:+.1f}pp）——真实对话没拿到这些事实，不能宣称 +10pp"
        )
    return (
        f"未通过：召回侧瓶颈（observed {observed:+.1f}pp，full {full:+.1f}pp 都低于阈值），"
        "T027 的查询改写需要撤换"
    )


def percent(value: float) -> str:
    """把比例格式化成两位小数的百分数，便于阅读。"""
    return f"{value * 100:.1f}%"


def render(
    result: Aggregate,
    per_type: int,
    limit: int,
    hybrid: bool,
    observed: ObservedStats | None = None,
    answers: ObservedStats | None = None,
) -> str:
    """生成 Markdown 诊断报告：样本与方法、四种口径曲线、口径差值、结论与不可召回清单。"""
    kinds = sorted({row.question_type for row in result.rows})
    lines = [
        "# 离线检索诊断报告（specs/007 US5）",
        "",
        f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M UTC}。",
        (
            f"样本：每类前 {per_type} 题（0=全量），计 {len(result.rows)} 题，"
            f"其中有必需文档标签的 {result.counted} 题；单次检索 limit={limit}。"
        ),
    ]
    if observed is not None:
        lines.append(
            f"observed 口径：真实臂会话匹配 {observed.matched} 题、"
            + f"跳过 {observed.skipped} 个会话（首条消息匹配不到题目的会话不计）。"
        )
    if answers is not None:
        lines.append(
            f"actual 口径：新臂会话匹配 {answers.matched} 题、"
            + "跳过 "
            + f"{answers.skipped} 个会话；查询 = 首条消息 + 最终检查点 "
            + "clarification.answers 的主题值（产品 query_with_facts 的离线镜像）。"
        )
    else:
        lines.append("未提供 --answers-arm：actual 口径等于 initial，仅供表结构对齐。")
    lines += [
        (
            "方法：四种查询口径（initial=首条消息；observed=首条消息+该会话后续用户回复；"
            "actual=首条消息+检查点实际收集到的主题值；full=首条消息+全部可披露事实，"
            "仅作上界对照）× 两条检索路径（vector=纯向量；hybrid=关键词+向量 RRF），"
            "各检索一次后按前缀计算 recall@k（片段 source.doc_id 命中即算召回）。"
            f"混合检索{'已开启' if hybrid else '未开启（本次只有纯向量路径）'}；"
            "全程不调用聊天模型。"
        ),
        "",
        "## 全样本平均 recall@k",
        "",
        "| 路径 | 口径 | " + " | ".join(f"@{k}" for k in K_VALUES) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in K_VALUES) + " |",
    ]
    for path in PATHS:
        for dialect in DIALECTS:
            lines.append(
                f"| {path} | {dialect} | "
                + " | ".join(percent(result.mean(dialect, path, k)) for k in K_VALUES)
                + " |"
            )
    lines += ["", "## 分题型 recall@k", ""]
    for path in PATHS:
        lines += [
            f"### {path}",
            "",
            "| 题型 | 口径 | " + " | ".join(f"@{k}" for k in K_VALUES) + " |",
            "| --- | --- | " + " | ".join("---:" for _ in K_VALUES) + " |",
        ]
        for kind in kinds:
            for dialect in DIALECTS:
                lines.append(
                    f"| {kind} | {dialect} | "
                    + " | ".join(
                        percent(result.of_type(kind, dialect, path, k))
                        for k in K_VALUES
                    )
                    + " |"
                )
        lines.append("")
    for dialect in ("observed", "actual", "full"):
        lines += [
            f"## 口径差值（{dialect}-initial）",
            "",
            "| 路径 | " + " | ".join(f"@{k}" for k in K_VALUES) + " |",
            "| --- | " + " | ".join("---:" for _ in K_VALUES) + " |",
        ]
        for path in PATHS:
            lines.append(
                f"| {path} | "
                + " | ".join(
                    f"{result.delta_at(k, path, dialect):+.1f}pp" for k in K_VALUES
                )
                + " |"
            )
        lines.append("")
    lines += [
        "## 结论",
        "",
        verdict(result, use_actual=answers is not None),
        "",
        (
            "判据：observed-initial 的 recall@10 提升 ≥ "
            f"{INFO_GAP_PP:.0f}pp 才算改造达标；full 只是上界对照，不单独作为效果。"
        ),
        "",
        f"## 不可召回文档（任何口径与路径的前 {limit} 个片段里都没有出现）",
        "",
    ]
    if result.unretrievable:
        lines += [f"- {document}" for document in result.unretrievable]
        lines += ["", "这些文档单列为不可召回，不归因于查询改写或混合参数。"]
    else:
        lines.append("无：所有必需文档都在至少一条路径的前 " + f"{limit} 个片段里。")
    return "\n".join(lines) + "\n"


async def prefetch(
    embeddings: BatchEmbedder, texts: Sequence[str]
) -> dict[str, EmbeddedBatch]:
    """把查询文本按批预取成向量，同一文本只嵌入一次（按文本去重）。"""
    unique = list(dict.fromkeys(texts))
    batches: dict[str, EmbeddedBatch] = {}
    for start in range(0, len(unique), embeddings.batch_size):
        chunk = unique[start : start + embeddings.batch_size]
        batch = await embeddings.embed(chunk)
        for text, vector, digest in zip(
            chunk, batch.vectors, batch.input_hashes, strict=True
        ):
            batches[text] = EmbeddedBatch(
                batch.spec, [digest], [vector], batch.usage_batches
            )
    return batches


def labels_of(cases: Sequence[Case]) -> dict[str, tuple[str, ...]]:
    """把题目标签收成 question_id → 必需文档。"""
    return {case.question_id: case.documents for case in cases}


async def diagnose(
    settings_path: Path,
    per_type: int,
    limit: int,
    observed_arm: Path | None = None,
    answers_arm: Path | None = None,
) -> tuple[str, bool]:
    """跑完诊断，返回（报告文本, 是否包含混合路径）。"""
    settings = load_settings(settings_path)
    if settings.embedding is None:
        raise ValueError("Embedding settings are required for retrieval diagnosis")
    collection_id = settings.knowledge_collection_id
    if not collection_id:
        raise ValueError("knowledge_collection_id is not configured")
    dsn = os.environ.get(settings.knowledge_url_env)
    if not dsn:
        raise ValueError(f"{settings.knowledge_url_env} is not set")
    raw_cases = sample(blueprints(), per_type)
    cases = [sample_of(case) for case in raw_cases]
    questions_of = {
        require_text(case["question_id"]): require_text(case["question"])
        for case in raw_cases
    }
    stats: ObservedStats | None = None
    if observed_arm is not None:
        # 题库在这里只用于首条消息匹配与标签；产品代码不读这些元数据。
        observed, stats = observed_queries(cases, observed_arm, questions=questions_of)
        cases = apply_observed(cases, observed)
    answers: ObservedStats | None = None
    if answers_arm is not None:
        actual, answers = actual_queries(cases, answers_arm, questions=questions_of)
        cases = apply_actual(cases, actual)
    hybrid_config = (
        HybridConfig(
            keyword_limit=settings.knowledge_keyword_limit,
            vector_limit=settings.knowledge_vector_limit,
            rrf_k=settings.knowledge_rrf_k,
        )
        if settings.knowledge_hybrid
        else None
    )
    sink = NullSink()
    store = PostgresVectorStore(dsn)
    with ExitStack() as stack:
        stack.callback(store.close)
        collection = await asyncio.to_thread(store.get_collection, collection_id)
        if collection["status"] != "ready":
            raise ValueError("Collection is not ready")
        adapter = OpenAiEmbeddingModel(settings.embedding)
        embeddings = EmbeddingClient(adapter, sink)
        if not same_vector_space(
            collection["build_config"]["embedding"], embeddings.identity
        ):
            raise ValueError("Embedding configuration differs from collection")
        vectors = await prefetch(
            embeddings, [case.text(dialect) for case in cases for dialect in DIALECTS]
        )
        searchers: dict[str, Searcher] = {
            "vector": StoreSearcher(store, collection_id, vectors)
        }
        if hybrid_config is not None:
            searchers["hybrid"] = StoreSearcher(
                store, collection_id, vectors, hybrid_config
            )
        rows = await collect(cases, searchers, collection_id, limit)
    return render(
        aggregate(rows, labels_of(cases)),
        per_type,
        limit,
        hybrid_config is not None,
        stats,
        answers,
    ), (hybrid_config is not None)


class Arguments(argparse.Namespace):
    """命令行参数；argparse 属性为 Any，这里一次性声明类型。"""

    per_type: int = 16
    limit: int = 20
    config: Path = ROOT / "backend/configs/local.toml"
    observed_arm: Path | None = None
    answers_arm: Path | None = None
    output: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-type", type=int, default=16, help="每类题数，0=全量")
    parser.add_argument("--limit", type=int, default=20, help="单次检索条数")
    parser.add_argument("--config", type=Path, default=Arguments.config)
    parser.add_argument(
        "--observed-arm",
        type=Path,
        default=None,
        help="真实臂目录：用其中的用户回复构造 observed 口径；缺省时 observed=initial",
    )
    parser.add_argument(
        "--answers-arm",
        type=Path,
        default=None,
        help="新臂目录：用最终检查点的 clarification.answers 构造 actual 口径（SC-5）",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(namespace=Arguments())
    report, hybrid = asyncio.run(
        diagnose(
            args.config, args.per_type, args.limit, args.observed_arm, args.answers_arm
        )
    )
    output = args.output or (
        ROOT / "var/benchmark" / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-retrieval.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"检索诊断{'（含混合路径）' if hybrid else '（纯向量）'}：{output}")


if __name__ == "__main__":
    main()
