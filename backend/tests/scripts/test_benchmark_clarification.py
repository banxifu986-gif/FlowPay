"""澄清机制统计（specs/007 US6 T032）：覆盖率三处并集、重复问、等待轮带结论。

全部用合成事件库与合成题目：读真实旧臂只作为基线核对，用例不依赖数据集内容。
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from scripts.benchmark import clarification

QUESTION_TEXT = "后台里能看到一笔支付但接口查不到"


def question(
    identifier: str = "Q-1",
    required: Sequence[str] = ("goal", "identifiers"),
    multi: bool = True,
) -> dict[str, object]:
    """构造一道最小题；单轮题用 question 正文匹配，多轮用首条消息。"""
    return {
        "question_id": identifier,
        "question_type": "multi_hop",
        "question": QUESTION_TEXT,
        "conversation": {
            "mode": "progressive_disclosure" if multi else "single_turn_complete",
            "initial_user_message": QUESTION_TEXT,
            "required_clarification_categories": list(required),
        },
    }


class ArmBuilder:
    """构造最小臂事件库：与真实臂相同的表结构与事件信封。"""

    def __init__(self, root: Path, name: str = "arm") -> None:
        self.path: Path = root / name / "multi-agent" / "sessions.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db: sqlite3.Connection = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE events (session_id TEXT, seq INTEGER, event_id TEXT UNIQUE,"
            + " run_id TEXT, type TEXT, data TEXT)"
        )
        self.seq: int = 0

    def add(
        self, session: str, kind: str, payload: dict[str, object], run: str | None = None
    ) -> None:
        """追加一条事件。"""
        self.seq += 1
        data = json.dumps(
            {"schema_version": 1, "type": kind, "payload": payload}, ensure_ascii=False
        )
        self.db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (session, self.seq, f"evt_{self.seq}", run, kind, data),
        )

    def close(self) -> None:
        """提交并关闭。"""
        self.db.commit()
        self.db.close()


def open_session(builder: ArmBuilder, session: str, first_message: str = QUESTION_TEXT) -> None:
    """开一个会话并写入首条用户消息；匹配按这条消息进行。"""
    builder.add(session, "session.created", {})
    builder.add(session, "message.user", {"content": first_message})


def ask_run(
    builder: ArmBuilder,
    session: str,
    run: str,
    missing: Sequence[str] = (),
    topics: Sequence[str] = (),
    answer: str = "请补充信息？",
    status: str = "waiting_user",
    state: dict[str, object] | None = None,
) -> None:
    """一轮完整的运行：ask_user + 助手正文 + run.finished。"""
    arguments: dict[str, object] = {"missing": list(missing), "question": "？"}
    if topics:
        arguments["topics"] = list(topics)
    builder.add(session, "run.started", {}, run)
    builder.add(
        session,
        "tool.started",
        {"name": "ask_user", "arguments": json.dumps(arguments)},
        run,
    )
    builder.add(session, "message.assistant", {"content": answer}, run)
    builder.add(session, "run.finished", {"status": status, "state": state or {}}, run)


def test_coverage_unions_the_three_sources(tmp_path: Path) -> None:
    """覆盖率取 ask_user、decided、检查点 answers 三处并集（映射成类别）。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    state: dict[str, object] = {"clarification": {"answers": {"occurrence_time": "8月2日中午"}}}
    ask_run(builder, "s1", "run_0", missing=["account_id"], topics=["goal"])
    builder.add(
        "s1",
        "clarification.decided",
        {"mode": "clarify", "topics": ["symptom_details"], "conditions": ["payment_id"]},
        "run_1",
    )
    ask_run(builder, "s1", "run_1", state=state)
    builder.close()
    logs = clarification.read_session_logs(tmp_path / "arm")
    stats, skipped = clarification.analyze([question()], logs)
    assert skipped == 0
    entry = stats[0]
    assert entry.asked is True
    assert entry.covered == frozenset(
        {"account_context", "goal", "symptom_details", "identifiers", "occurrence_time"}
    )
    assert entry.missing == []


def test_answers_alone_do_not_count_as_asking(tmp_path: Path) -> None:
    """只有检查点 answers 的题不算发过问，不进入覆盖率分母。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    state: dict[str, object] = {"clarification": {"answers": {"goal": "想确认是不是权限问题"}}}
    ask_run(builder, "s1", "run_0", state=state)
    builder.close()
    stats, _ = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    entry = stats[0]
    assert entry.asked is False
    assert "goal" in entry.covered


def test_declined_asks_do_not_count_as_asking(tmp_path: Path) -> None:
    """worker 提议但 synthesize 改判作答的 ask_user 不算向用户索要。

    规格 Clarifications：只有最终采纳且用户可见的 clarify 决策才算问过；
    改判轮的问题用户从未看到，计入索要会虚报覆盖与重复。
    """
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    # 轮 0：worker 提问后改判作答（decided mode=answer，status completed）
    builder.add(
        "s1",
        "tool.started",
        {
            "name": "ask_user",
            "arguments": json.dumps({"missing": ["account_id"], "question": "？"}),
        },
        "run_0",
    )
    builder.add(
        "s1",
        "clarification.decided",
        {"mode": "answer", "source": "model", "topics": [], "conditions": []},
        "run_0",
    )
    builder.add("s1", "message.assistant", {"content": "直接作答"}, "run_0")
    builder.add("s1", "run.finished", {"status": "completed", "state": {}}, "run_0")
    # 轮 1：采纳的 clarify（decided mode=clarify，status waiting_user）
    builder.add(
        "s1",
        "clarification.decided",
        {
            "mode": "clarify",
            "source": "model",
            "topics": ["goal"],
            "conditions": ["payment_id"],
        },
        "run_1",
    )
    builder.add(
        "s1",
        "message.assistant",
        {"content": "已查到的结论：规则如下。\n\n还需你补充：目标。"},
        "run_1",
    )
    builder.add("s1", "run.finished", {"status": "waiting_user", "state": {}}, "run_1")
    builder.close()
    stats, _ = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    entry = stats[0]
    assert entry.asked is True
    # 改判轮的 account_id 不计入：已问过的只有采纳轮的 goal 与 identifiers
    assert entry.covered == frozenset({"goal", "identifiers"})
    assert entry.repeated == []
    assert entry.waiting_rounds == 1


def test_known_conditions_count_as_covered(tmp_path: Path) -> None:
    """用户自发提供的条件在检查点 case.known 里，计为已覆盖（SC-1 不算漏问）。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    state: dict[str, object] = {
        "clarification": {"answers": {}},
        "case": {"known": {"account_id": "acct_bg_001"}},
    }
    ask_run(builder, "s1", "run_0", missing=["payment_id"], state=state)
    builder.close()
    stats, _ = clarification.analyze(
        [question(required=("goal", "identifiers", "account_context"))],
        clarification.read_session_logs(tmp_path / "arm"),
    )
    entry = stats[0]
    assert entry.asked is True
    assert entry.covered == frozenset({"account_context", "identifiers"})
    assert entry.missing == ["goal"]


def test_answers_are_unioned_across_runs(tmp_path: Path) -> None:
    """早期检查点收集到的主题在后期被误清时仍计为已覆盖（计量取跨轮并集）。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    state_first: dict[str, object] = {
        "clarification": {
            "answers": {"symptom_details": "回调 500"},
            "asked_topics": ["symptom_details"],
        }
    }
    ask_run(builder, "s1", "run_0", missing=["account_id"], state=state_first)
    state_second: dict[str, object] = {
        "clarification": {
            "answers": {},
            "unavailable_topics": ["symptom_details"],
            "asked_topics": ["symptom_details"],
        }
    }
    ask_run(builder, "s1", "run_1", missing=["account_id"], state=state_second)
    builder.close()
    stats, _ = clarification.analyze(
        [question(required=("symptom_details", "account_context"))],
        clarification.read_session_logs(tmp_path / "arm"),
    )
    entry = stats[0]
    assert "symptom_details" in entry.covered
    assert entry.missing == []


def test_repeated_category_across_runs(tmp_path: Path) -> None:
    """同一类别在两轮及以上被索要即计一次重复。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    ask_run(builder, "s1", "run_0", missing=["account_id"])
    ask_run(builder, "s1", "run_1", missing=["environment"])
    builder.close()
    stats, _ = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    assert stats[0].repeated == ["account_context"]


def test_bad_structures_do_not_raise(tmp_path: Path) -> None:
    """答案为空、坏结构与缺失字段都不抛异常，按空处理。"""
    builder = ArmBuilder(tmp_path)
    builder.add("s1", "session.created", {})
    builder.add("s1", "message.user", {"content": QUESTION_TEXT})
    builder.add(
        "s1",
        "clarification.decided",
        {"mode": "answer", "source": "fallback"},
        "run_0",
    )
    builder.add(
        "s1",
        "tool.started",
        {"name": "ask_user", "arguments": "不是 JSON"},
        "run_0",
    )
    builder.add(
        "s1",
        "run.finished",
        {"status": "waiting_user", "state": {"clarification": {"answers": "坏结构"}}},
        "run_0",
    )
    builder.add(
        "s1",
        "run.finished",
        {"status": "completed", "state": {"clarification": {}}},
        "run_1",
    )
    builder.close()
    logs = clarification.read_session_logs(tmp_path / "arm")
    stats, _ = clarification.analyze([question()], logs)
    entry = stats[0]
    assert entry.asked is False
    assert entry.covered == frozenset()
    assert logs[0].answers == []


def test_waiting_summary_judged_by_visible_text(tmp_path: Path) -> None:
    """等待轮带结论按用户可见正文判定，不用 has_reports 推断。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    ask_run(
        builder,
        "s1",
        "run_0",
        missing=["account_id"],
        answer="已查到的结论：规则如下。\n\n还需你补充：账户。",
    )
    ask_run(builder, "s1", "run_1", missing=["payment_id"], answer="请提供 Payment ID？")
    builder.close()
    stats, _ = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    entry = stats[0]
    assert entry.waiting_rounds == 2
    assert entry.waiting_summaries == 1


def test_unmatched_sessions_are_skipped(tmp_path: Path) -> None:
    """首条消息匹配不到题目的会话跳过并计数，不猜它属于哪道题。"""
    builder = ArmBuilder(tmp_path)
    builder.add("s1", "session.created", {})
    builder.add("s1", "message.user", {"content": "这段对话不属于任何题目"})
    builder.close()
    stats, skipped = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    assert stats == []
    assert skipped == 1


def test_single_turn_questions_match_the_question_text(tmp_path: Path) -> None:
    """单轮题的首条消息是题目正文，多轮题是 initial_user_message。"""
    builder = ArmBuilder(tmp_path)
    builder.add("s1", "session.created", {})
    builder.add("s1", "message.user", {"content": QUESTION_TEXT})
    ask_run(builder, "s1", "run_0", missing=["account_id"])
    builder.close()
    stats, _ = clarification.analyze(
        [question(multi=False)], clarification.read_session_logs(tmp_path / "arm")
    )
    assert len(stats) == 1
    assert stats[0].asked is True


def test_final_waiting_round_is_flagged(tmp_path: Path) -> None:
    """多轮会话最后一轮以等待结束时记为多轮最终轮等待（SC-6 核对用）。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    ask_run(builder, "s1", "run_0", missing=["account_id"], status="completed")
    ask_run(builder, "s1", "run_1", missing=["payment_id"])
    builder.close()
    stats, _ = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    assert stats[0].waiting_final is True


def test_single_turn_waiting_not_in_coverage(tmp_path: Path) -> None:
    """单轮等待题（required 为空、asked=true）不进覆盖分子/分母（SC-1）。

    覆盖率只统计确实发过问的多轮题；单轮题没有必需类别，算进分母会把
    整题全覆盖虚高。
    """
    builder = ArmBuilder(tmp_path)
    builder.add("s1", "session.created", {})
    builder.add("s1", "message.user", {"content": QUESTION_TEXT})
    ask_run(builder, "s1", "run_0", missing=["account_id"])
    builder.close()
    stats, skipped = clarification.analyze(
        [question(multi=False)], clarification.read_session_logs(tmp_path / "arm")
    )
    entry = stats[0]
    assert entry.asked is True
    assert entry.multi_turn is False
    report = clarification.render([("C", stats, skipped)])
    assert "多轮发问题数 0" in report
    assert "整题全覆盖 0/0" in report
    assert "单轮等待轮数 1" in report


def test_waiting_totals_still_include_single_turn(tmp_path: Path) -> None:
    """等待轮总数与带结论判定仍含单轮等待（SC-6 口径不变）。"""
    builder = ArmBuilder(tmp_path)
    builder.add("s1", "session.created", {})
    builder.add("s1", "message.user", {"content": QUESTION_TEXT})
    ask_run(builder, "s1", "run_0", missing=["account_id"], answer="请补充信息？")
    builder.close()
    stats, _ = clarification.analyze(
        [question(multi=False)], clarification.read_session_logs(tmp_path / "arm")
    )
    assert stats[0].waiting_rounds == 1
    assert stats[0].waiting_summaries == 0
    report = clarification.render([("C", stats, 0)])
    assert "等待轮 1" in report
    assert "不带结论 1" in report


def test_render_reports_coverage_repeats_and_waitings(tmp_path: Path) -> None:
    """报告包含匹配/跳过、多轮发问题数、覆盖率、重复问与等待轮带结论。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    ask_run(builder, "s1", "run_0", missing=["account_id"])
    builder.close()
    stats, skipped = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    report = clarification.render([("C", stats, skipped)])
    assert "匹配 1" in report and "跳过 0" in report
    assert "多轮发问题数 1" in report
    assert "0/1" in report
    assert "重复问" in report
    assert "等待轮 1" in report
    assert "多轮最终轮 1" in report
    assert "单轮等待轮数 0" in report
    assert "| goal | 1 | 0 | 1 |" in report


def test_render_reports_unverified_rounds(tmp_path: Path) -> None:
    """报告列出含统一未证实口径的轮数与题数（specs/009 SC-5 数据源）。"""
    builder = ArmBuilder(tmp_path)
    open_session(builder, "s1")
    ask_run(builder, "s1", "run_0", missing=["account_id"])
    # 助手正文一处含"未能核实"。
    builder.add(
        "s1",
        "message.assistant",
        {"content": f"{clarification.UNVERIFIED_MARK}：手续费金额。"},
        "run_1",
    )
    builder.close()
    stats, skipped = clarification.analyze(
        [question()], clarification.read_session_logs(tmp_path / "arm")
    )
    assert stats[0].unverified_rounds == 1
    report = clarification.render([("C", stats, skipped)])
    assert "未证实口径：1 轮（1 题）" in report
