"""离线检索评测的纯函数与采集口径（specs/007 US5）。

单元测试只用合成题目与假检索器：题库元数据只在真实诊断运行时作为标签使用，
这里不复制任何题库标准答案或某道题的期望输出。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from scripts.benchmark import retrieval as retrieve

from flowpay.core.json import JsonObject
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.llm.embedding import EmbeddedBatch
from flowpay.core.llm.embedding.settings import EmbeddingIdentity

COLLECTION = "fixture"


def passage(doc_id: str, chunk: int = 1) -> Passage:
    """构造一个可被召回的最小片段；只有 doc_id 参与评测口径。"""
    return Passage(
        id=f"doc:{doc_id}#c{chunk:03d}",
        title=doc_id,
        text="fixture",
        score=0.5,
        source=PassageSource(
            collection_id=COLLECTION,
            doc_id=doc_id,
            chunk_no=chunk,
            path=f"knowledge/{doc_id}.md",
            sha256="fixture",
            locator={},
            metadata={},
        ),
    )


def case(
    identifier: str = "Q-1",
    kind: str = "simple_retrieval",
    mode: str = "single_turn_complete",
    documents: list[str] | None = None,
    evidence: list[str] | None = None,
    facts: list[str] | None = None,
) -> JsonObject:
    """构造一道题的元数据：只填评测需要的字段，不写任何答案内容。"""
    talk: JsonObject = {
        "mode": mode,
        "initial_user_message": "首条消息：现象是什么",
        "available_user_facts": [
            {"fact_id": f"f{index}", "user_value": value} for index, value in enumerate(facts or [])
        ],
    }
    return {
        "question_id": identifier,
        "question_type": kind,
        "question": "单轮问题正文",
        "conversation": talk,
        "required_documents": documents or [],
        "planned_evidence_ids": evidence or [],
    }


def test_required_documents_come_from_documents_and_doc_evidence() -> None:
    """必需文档取 required_documents 与 planned_evidence_ids 的 doc: 项，去重后保留顺序。"""
    documents = retrieve.required_documents_of(
        case(
            documents=["KB-A"],
            evidence=["doc:KB-A#c002", "doc:KB-B", "record:pay_1", "rule:R-1"],
        )
    )
    assert documents == ("KB-A", "KB-B")


def test_single_turn_query_texts_use_question_then_all_facts() -> None:
    """单轮题 initial 取 question；full 在 initial 后追加全部可披露事实。"""
    texts = retrieve.query_texts_of(case(facts=["现象是回调 500", "目标是查为什么"]))
    assert texts[0] == "单轮问题正文"
    assert texts[1] == "单轮问题正文 现象是回调 500 目标是查为什么"


def test_multi_turn_query_texts_use_first_message() -> None:
    """多轮题 initial 取首条消息，而不是题目正文。"""
    texts = retrieve.query_texts_of(case(mode="progressive_disclosure", facts=["环境是生产"]))
    assert texts[0] == "首条消息：现象是什么"
    assert texts[1] == "首条消息：现象是什么 环境是生产"


def test_recall_counts_required_documents_within_top_k() -> None:
    """recall@k = 必需文档中出现在前 k 个片段的比例。"""
    assert retrieve.recall_at(["KB-A", "KB-B"], ["KB-A", "KB-C"], 1) == 0.5
    assert retrieve.recall_at(["KB-A", "KB-B"], ["KB-A", "KB-C"], 2) == 0.5
    assert retrieve.recall_at(["KB-A"], ["KB-C", "KB-A"], 1) == 0.0
    assert retrieve.recall_at(["KB-A"], ["KB-C", "KB-A"], 2) == 1.0


def test_recall_treats_repeated_chunks_as_one_document() -> None:
    """同一文档的多个片段只算一次命中，不会把 recall 抬高到 1 以上。"""
    found = [passage("KB-A", 1), passage("KB-A", 2), passage("KB-B", 1)]
    assert retrieve.document_ids(found) == ["KB-A", "KB-B"]
    assert retrieve.recall_at(["KB-A", "KB-B"], retrieve.document_ids(found), 1) == 0.5
    assert retrieve.recall_at(["KB-A", "KB-B"], retrieve.document_ids(found), 2) == 1.0


def test_recall_without_required_documents_is_zero_and_flagged() -> None:
    """没有必需文档的题不产生召回率，由调用方决定是否计入汇总。"""
    assert retrieve.recall_at([], ["KB-A"], 10) == 0.0
    assert retrieve.has_labels(case()) is False
    assert retrieve.has_labels(case(documents=["KB-A"])) is True


class FakeEmbeddings:
    """按批返回固定向量的嵌入替身；只用于验证预取覆盖了哪些文本。"""

    batch_size: int = 8

    async def embed(self, texts: list[str]) -> EmbeddedBatch:
        """为每个文本返回一条一维向量与对应输入摘要。"""
        identity = cast(
            "EmbeddingIdentity",
            cast(
                "object",
                {
                    "provider": "fixture",
                    "model": "fixture",
                    "revision": "fixture",
                    "base_url": "fixture",
                    "dimensions": 1,
                    "request_options": {},
                },
            ),
        )
        return EmbeddedBatch(
            identity,
            [f"hash_{text}" for text in texts],
            [[float(len(text))] for text in texts],
            [],
        )


class FakeSearcher:
    """按查询文本返回预设片段的检索替身；记录每次调用的口径与条数。"""

    def __init__(self, by_text: dict[str, list[Passage]]) -> None:
        """绑定"查询文本 → 片段列表"的映射，未命中的查询返回空列表。"""
        self.by_text: dict[str, list[Passage]] = by_text
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, collection_id: str, text: str, limit: int) -> list[Passage]:
        """记录调用并返回预设结果，筛选口径不属于本替身职责。"""
        assert collection_id == COLLECTION
        self.calls.append((collection_id, text, limit))
        return self.by_text.get(text, [])[:limit]


async def test_collect_searches_each_dialect_and_path_once() -> None:
    """每道题在四种口径 × 两条路径上各检索一次，limit 固定。"""
    sample = retrieve.sample_of(case(mode="progressive_disclosure", facts=["环境是生产"]))
    vector = FakeSearcher({})
    hybrid = FakeSearcher({})
    rows = await retrieve.collect(
        [sample], {"vector": vector, "hybrid": hybrid}, COLLECTION, limit=20
    )
    assert len(vector.calls) == 4 and len(hybrid.calls) == 4
    assert {call[2] for call in vector.calls + hybrid.calls} == {20}
    # 没有真实臂时 observed/actual 都等于 initial：四种口径只产生两段不同文本。
    assert {call[1] for call in vector.calls} == {sample.initial, sample.full}
    assert rows[0].question_id == "Q-1"


async def test_collect_keeps_ranked_document_ids() -> None:
    """采集结果保存按排名去重的文档编号，供任意 k 复用。"""
    sample = retrieve.sample_of(case(documents=["KB-A"]))
    searcher = FakeSearcher(
        {
            "单轮问题正文": [passage("KB-A", 1), passage("KB-A", 2), passage("KB-B", 1)],
        }
    )
    rows = await retrieve.collect([sample], {"vector": searcher}, COLLECTION, limit=20)
    assert rows[0].documents("initial", "vector") == ["KB-A", "KB-B"]


def row(
    identifier: str,
    kind: str,
    initial: list[str],
    full: list[str],
    path: str = "vector",
    dialect: str = "full",
) -> retrieve.RetrievalRow:
    """构造一条采集结果：默认同时给出该路径上的两种口径。"""
    return retrieve.RetrievalRow(
        identifier,
        kind,
        {f"initial:{path}": initial, f"{dialect}:{path}": full},
    )


def test_aggregate_averages_over_questions_with_labels() -> None:
    """汇总按题平均：没有必需文档的题不进入分母。"""
    rows = [
        row("Q-1", "simple_retrieval", ["KB-C"], ["KB-A"]),
        row("Q-2", "simple_retrieval", [], []),
    ]
    labels = {"Q-1": ("KB-A",), "Q-2": ()}
    aggregate = retrieve.aggregate(rows, labels)
    assert aggregate.counted == 1
    assert aggregate.mean("initial", k=1) == 0.0
    assert aggregate.mean("full", k=1) == 1.0


def test_unretrievable_documents_are_listed() -> None:
    """任何口径与路径都没召回到的必需文档单独列出。"""
    aggregate = retrieve.aggregate(
        [row("Q-1", "simple_retrieval", ["KB-A"], ["KB-A"])],
        {"Q-1": ("KB-A", "KB-Z")},
    )
    assert aggregate.unretrievable == ["KB-Z"]


def test_render_reports_curves_delta_and_paths() -> None:
    """报告包含样本说明、两条路径、两种口径、k 曲线与 full-initial 差值。"""
    aggregate = retrieve.aggregate(
        [row("Q-1", "simple_retrieval", ["KB-C"], ["KB-A", "KB-C"])],
        {"Q-1": ("KB-A",)},
    )
    report = retrieve.render(aggregate, per_type=16, limit=20, hybrid=True)
    assert "样本" in report
    assert "vector" in report and "hybrid" in report
    assert "initial" in report and "full" in report
    assert "recall@10" in report
    assert "full-initial" in report


def test_render_marks_delta_as_query_information_gap() -> None:
    """full-initial 的 recall@10 差值 >= 10pp 时，报告把它判为查询缺信息。"""
    rows = [
        row(qid, "simple_retrieval", [], ["KB-A"], path, dialect="observed")
        for qid in ("Q-1", "Q-2", "Q-3")
        for path in ("vector", "hybrid")
    ]
    aggregate = retrieve.aggregate(rows, {entry.question_id: ("KB-A",) for entry in rows})
    report = retrieve.render(aggregate, per_type=16, limit=20, hybrid=True)
    assert "通过" in report


def test_render_marks_small_delta_as_recall_bottleneck() -> None:
    """两口径都低且差值小于 10pp 时，报告把瓶颈判为召回本身。"""
    rows = [
        row("Q-1", "simple_retrieval", [], [], path, dialect="observed")
        for path in ("vector", "hybrid")
    ]
    aggregate = retrieve.aggregate(rows, {"Q-1": ("KB-A",)})
    report = retrieve.render(aggregate, per_type=16, limit=20, hybrid=True)
    assert "召回侧瓶颈" in report


def test_aggregate_rejects_unknown_dialect() -> None:
    """未知口径直接报错，避免统计口径被静默写错。"""
    aggregate = retrieve.aggregate([row("Q-1", "simple_retrieval", [], [])], {"Q-1": ("KB-A",)})
    with pytest.raises(KeyError):
        aggregate.mean(cast("str", "bogus"), k=10)


# --- observed 口径（T028 重开）：用真实臂里的用户回复构造改写后的查询 ---


def build_arm(root: Path, sessions: Sequence[Sequence[str]]) -> Path:
    """构造一个最小事件臂：每题一个会话，按给定顺序写入 session.created 与 message.user。"""
    events = root / "multi-agent" / "sessions.sqlite"
    events.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(events) as db:
        db.execute(
            "CREATE TABLE events (session_id TEXT, seq INTEGER, event_id TEXT UNIQUE,"
            + " run_id TEXT, type TEXT, data TEXT)"
        )
        seq = 0
        for index, messages in enumerate(sessions):
            session = f"session_{index}"
            seq += 1
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, NULL, 'session.created', ?)",
                (session, seq, f"evt_{seq}", json.dumps({"session_id": session})),
            )
            for content in messages:
                seq += 1
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, NULL, 'message.user', ?)",
                    (
                        session,
                        seq,
                        f"evt_{seq}",
                        json.dumps({"payload": {"content": content}, "type": "message.user"}),
                    ),
                )
    return root


def test_observed_appends_follow_up_user_messages(tmp_path: Path) -> None:
    """observed = 首条消息 + 该会话后续全部用户回复（空白归一化后拼接）。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm(
        tmp_path / "arm",
        [["单轮问题正文", "生产环境，账户 acct_1", "  回调一直 500  ", "查不到发生时间"]],
    )
    observed, stats = retrieve.observed_queries(cases, arm)
    assert observed["Q-1"] == "单轮问题正文 生产环境，账户 acct_1 回调一直 500 查不到发生时间"
    assert stats.matched == 1
    assert stats.skipped == 0


def test_observed_without_arm_falls_back_to_initial() -> None:
    """没有给臂时 observed 就是 initial，统计里也不计匹配。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    observed, stats = retrieve.observed_queries(cases, None, initial={"Q-1": "单轮问题正文"})
    assert observed["Q-1"] == "单轮问题正文"
    assert stats.matched == 0


def test_observed_without_follow_ups_falls_back_to_initial(tmp_path: Path) -> None:
    """单轮题会话里只有首条消息：observed 仍是 initial。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm(tmp_path / "arm", [["单轮问题正文"]])
    observed, stats = retrieve.observed_queries(cases, arm)
    assert observed["Q-1"] == "单轮问题正文"
    assert stats.matched == 1
    assert stats.skipped == 0


def test_unmatched_sessions_are_skipped_and_counted(tmp_path: Path) -> None:
    """首条消息匹配不到题目时跳过该会话并计数，不猜它属于哪道题。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm(
        tmp_path / "arm",
        [["这段对话不属于任何题库题目", "补充信息"], ["单轮问题正文", "补充事实"]],
    )
    observed, stats = retrieve.observed_queries(cases, arm)
    assert observed["Q-1"] == "单轮问题正文 补充事实"
    assert stats.skipped == 1
    assert stats.matched == 1


def test_render_reports_observed_and_full_deltas() -> None:
    """报告同时给出 observed-initial 与 full-initial 差值，并列出三种口径曲线。"""
    rows = [
        row("Q-1", "simple_retrieval", ["KB-C"], ["KB-A"], dialect="observed"),
    ]
    aggregate = retrieve.aggregate(rows, {"Q-1": ("KB-A",)})
    rows = [
        retrieve.RetrievalRow(
            "Q-1",
            "simple_retrieval",
            {
                "initial:vector": ["KB-C"],
                "observed:vector": ["KB-A"],
                "full:vector": ["KB-A"],
            },
        )
    ]
    aggregate = retrieve.aggregate(rows, {"Q-1": ("KB-A",)})
    report = retrieve.render(
        aggregate, per_type=16, limit=20, hybrid=True, observed=retrieve.ObservedStats(1, 0)
    )
    assert "observed" in report and "full" in report and "initial" in report
    assert "observed-initial" in report
    assert "full-initial" in report
    assert "匹配 1" in report and "跳过 0" in report
    assert "通过" in report


def test_verdict_reports_insufficient_coverage() -> None:
    """observed 差值不足而 full 达标时，结论是"澄清覆盖不足"，不能宣称 +10pp。"""
    rows = [
        retrieve.RetrievalRow(
            qid,
            "simple_retrieval",
            {
                "initial:hybrid": [],
                "observed:hybrid": [],
                "full:hybrid": ["KB-A"],
            },
        )
        for qid in ("Q-1", "Q-2")
    ]
    result = retrieve.aggregate(rows, {row.question_id: ("KB-A",) for row in rows})
    assert "澄清覆盖不足" in retrieve.verdict(result)
    assert retrieve.verdict(result).startswith("未通过")


def test_verdict_reports_recall_bottleneck_when_both_low() -> None:
    """observed 与 full 都低时判为召回侧瓶颈，T027 需要撤换。"""
    rows = [
        retrieve.RetrievalRow(
            "Q-1",
            "simple_retrieval",
            {"initial:hybrid": [], "observed:hybrid": [], "full:hybrid": []},
        )
    ]
    result = retrieve.aggregate(rows, {"Q-1": ("KB-A",)})
    assert "召回侧瓶颈" in retrieve.verdict(result)


# --- T024 重开：collect 必须按方言取文本，不能把 observed 当成 full ---


def three_dialect_case() -> retrieve.Case:
    """四种口径文本互不相同的样本；observed/actual 与 full 明显不同。"""
    return retrieve.Case(
        question_id="Q-1",
        question_type="simple_retrieval",
        initial="首条消息",
        observed="首条消息 用户实际回复",
        actual="首条消息 实际收集到的主题值",
        full="首条消息 全部可披露事实",
        documents=("KB-A",),
    )


async def test_collect_sends_each_dialect_text_verbatim() -> None:
    """四条口径各检索一次，observed 收到的是 case.observed（不是 full）。"""
    sample = three_dialect_case()
    searcher = FakeSearcher({})
    rows = await retrieve.collect([sample], {"vector": searcher}, COLLECTION, limit=20)
    texts = [call[1] for call in searcher.calls]
    assert len(texts) == 4
    assert texts == [sample.initial, sample.observed, sample.actual, sample.full]
    assert sample.observed != sample.full
    assert rows[0].question_id == "Q-1"


async def test_prefetch_covers_all_dialect_texts() -> None:
    """向量预取覆盖 initial / observed / actual / full 四段文本，且按文本去重。"""
    sample = three_dialect_case()
    fake = FakeEmbeddings()
    batches = await retrieve.prefetch(fake, [sample.text(dialect) for dialect in retrieve.DIALECTS])
    assert set(batches) == {sample.initial, sample.observed, sample.actual, sample.full}


# --- actual 口径（T037）：用新臂最终检查点的 clarification.answers 构造产品改写输入 ---


def build_arm_with_state(
    root: Path, sessions: Sequence[tuple[Sequence[str], dict[str, str] | None]]
) -> Path:
    """构造带检查点的最小事件臂：每会话首条消息 + 最后一次 run.finished 的状态。"""
    events = root / "multi-agent" / "sessions.sqlite"
    events.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(events) as db:
        db.execute(
            "CREATE TABLE events (session_id TEXT, seq INTEGER, event_id TEXT UNIQUE,"
            + " run_id TEXT, type TEXT, data TEXT)"
        )
        seq = 0
        for index, (messages, answers) in enumerate(sessions):
            session = f"session_{index}"
            seq += 1
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, NULL, 'session.created', ?)",
                (session, seq, f"evt_{seq}", json.dumps({"session_id": session})),
            )
            for content in messages:
                seq += 1
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, NULL, 'message.user', ?)",
                    (
                        session,
                        seq,
                        f"evt_{seq}",
                        json.dumps({"payload": {"content": content}, "type": "message.user"}),
                    ),
                )
            if answers is not None:
                seq += 1
                state = {"clarification": {"answers": answers}}
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, 'run_1', 'run.finished', ?)",
                    (
                        session,
                        seq,
                        f"evt_{seq}",
                        json.dumps({"payload": {"status": "waiting_user", "state": state}}),
                    ),
                )
    return root


def test_actual_queries_join_checkpoint_answers(tmp_path: Path) -> None:
    """actual = 首条消息 + 最终检查点 clarification.answers 的主题值（SC-5 产品口径）。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm_with_state(
        tmp_path / "arm",
        [
            (
                ["单轮问题正文", "补充了一句"],
                {"goal": "确认为什么退款没成功", "occurrence_time": "8 月 2 日"},
            )
        ],
    )
    actual, stats = retrieve.actual_queries(cases, arm)
    assert actual["Q-1"] == "单轮问题正文 确认为什么退款没成功 8 月 2 日"
    assert stats.matched == 1
    assert stats.skipped == 0


def test_actual_uses_latest_checkpoint(tmp_path: Path) -> None:
    """同会话多次 run.finished：以最后一次检查点的 answers 为准。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm_with_state(
        tmp_path / "arm",
        [(["单轮问题正文"], {"goal": "第一轮的值"})],
    )
    # 追加第二次 run.finished（answers 变化）
    with sqlite3.connect(arm / "multi-agent" / "sessions.sqlite") as db:
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?, 'run_2', 'run.finished', ?)",
            (
                "session_0",
                99,
                "evt_99",
                json.dumps(
                    {
                        "payload": {
                            "status": "waiting_user",
                            "state": {"clarification": {"answers": {"goal": "最新值"}}},
                        }
                    }
                ),
            ),
        )
    actual, _ = retrieve.actual_queries(cases, arm)
    assert actual["Q-1"] == "单轮问题正文 最新值"


def test_actual_without_arm_falls_back_to_initial() -> None:
    """没有 --answers-arm 时 actual 就是 initial，不计匹配。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    actual, stats = retrieve.actual_queries(cases, None)
    assert actual["Q-1"] == "单轮问题正文"
    assert stats.matched == 0


def test_actual_unmatched_sessions_are_skipped(tmp_path: Path) -> None:
    """首条消息匹配不到题目的会话跳过并计数。"""
    cases = [retrieve.sample_of(case(documents=["KB-A"]))]
    arm = build_arm_with_state(tmp_path / "arm", [(["别的会话"], {"goal": "值"})])
    actual, stats = retrieve.actual_queries(cases, arm)
    assert actual["Q-1"] == "单轮问题正文"
    assert stats.matched == 0
    assert stats.skipped == 1


def test_verdict_judges_actual_when_requested() -> None:
    """给了 --answers-arm 时，验收判据切到 actual-initial 的 recall@10。"""
    rows = [
        retrieve.RetrievalRow(
            qid,
            "simple_retrieval",
            {
                "initial:vector": [],
                "initial:hybrid": [],
                "actual:vector": ["KB-A"],
                "actual:hybrid": ["KB-A"],
                "full:vector": ["KB-A"],
                "full:hybrid": ["KB-A"],
            },
        )
        for qid in ("Q-1", "Q-2", "Q-3")
    ]
    aggregate = retrieve.aggregate(rows, {entry.question_id: ("KB-A",) for entry in rows})
    # actual-initial = 100pp → 通过；不给 actual 口径时按 observed 判仍是未通过
    assert "通过" in retrieve.verdict(aggregate, use_actual=True)
    assert "未通过" in retrieve.verdict(aggregate)


def test_render_reports_actual_when_answers_arm_given() -> None:
    """带 actual 统计的报告：actual 行、actual-initial 差值与 actual 判据。"""
    rows = [
        retrieve.RetrievalRow(
            "Q-1",
            "simple_retrieval",
            {
                "initial:vector": [],
                "initial:hybrid": [],
                "actual:vector": ["KB-A"],
                "actual:hybrid": ["KB-A"],
                "observed:vector": [],
                "observed:hybrid": [],
                "full:vector": ["KB-A"],
                "full:hybrid": ["KB-A"],
            },
        )
    ]
    aggregate = retrieve.aggregate(rows, {"Q-1": ("KB-A",)})
    report = retrieve.render(
        aggregate,
        per_type=16,
        limit=20,
        hybrid=True,
        answers=retrieve.ObservedStats(1, 0),
    )
    assert "actual" in report
    assert "actual-initial" in report
    assert "通过" in report
