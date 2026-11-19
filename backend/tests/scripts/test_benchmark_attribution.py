"""归因脚本的失败主因与等待轮拆分（specs/007 US6）。

用合成行钉死口径：单轮等待与多轮最终轮等待都是 waiting_user 信号，但来源不同，
报表必须能区分，主因归类也不能漏掉它们。
"""

import json
from pathlib import Path

from scripts.benchmark.attribution import Row, main_cause, render, waiting_breakdown


def synthetic_row(
    identifier: str,
    *,
    multi: bool = False,
    evidence_pass: bool = False,
    task_pass: bool = False,
    signals: list[str] | None = None,
    deviations: list[str] | None = None,
) -> Row:
    """构造一条最小逐题结果行；只填归因用到的字段。"""
    return Row(
        question_id=identifier,
        question_type="simple_retrieval",
        multi_turn=multi,
        doc="0/1",
        record="0/0",
        assertion="0/1",
        evidence_pass=evidence_pass,
        task_pass=task_pass,
        signals=signals or [],
        deviations=deviations or [],
    )


def test_single_turn_waiting_counts_as_waiting_for_evidence() -> None:
    """单轮题的等待轮修复后进入 signals，失败主因按追问结束归类。"""
    row = synthetic_row("Q-1", signals=["waiting_user"])
    assert main_cause(row) == "waiting_for_evidence"


def test_single_turn_waiting_with_full_evidence_is_over_clarify() -> None:
    """证据已齐仍以等待结束的单轮题归为过度追问。"""
    row = synthetic_row("Q-1", evidence_pass=True, signals=["waiting_user"])
    assert main_cause(row) == "over_clarify"


def test_multi_turn_final_waiting_counts_as_waiting_for_evidence() -> None:
    """多轮最终轮等待（预算耗尽）不再是 evidence_miss。"""
    row = synthetic_row(
        "Q-2",
        multi=True,
        signals=["waiting_user", "waiting_user"],
        deviations=["max_clarifications_exceeded"],
    )
    assert main_cause(row) == "waiting_for_evidence"


def test_waiting_breakdown_splits_single_middle_and_final() -> None:
    """waiting 信号拆成单轮等待、多轮中间等待与多轮最终等待三类。"""
    rows = [
        synthetic_row("Q-1", signals=["waiting_user"]),
        synthetic_row(
            "Q-2",
            multi=True,
            signals=["waiting_user", "waiting_user"],
            deviations=["max_clarifications_exceeded"],
        ),
        synthetic_row("Q-3", multi=True, signals=["waiting_user"]),
        synthetic_row("Q-4", multi=True, task_pass=True),
    ]
    assert waiting_breakdown(rows) == (1, 1, 1)


def write_arm(directory: Path, rows: list[Row]) -> Path:
    """把合成行落成逐题 jsonl，供 render 从盘上读。"""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "question_id": row.question_id,
                "question_type": row.question_type,
                "multi_turn": row.multi_turn,
                "doc_hit": 0,
                "doc_total": 1,
                "record_hit": 0,
                "record_total": 0,
                "assertion_hit": 0,
                "assertion_total": 1,
                "negative_hit": 0,
                "negative_total": 1,
                "evidence_pass": row.evidence_pass,
                "task_pass": row.task_pass,
                "signals": row.signals,
                "deviations": row.deviations,
            },
            ensure_ascii=False,
        )
        + "\n"
        for row in rows
    ]
    (directory / "multi-agent.jsonl").write_text("".join(lines), encoding="utf-8")
    return directory


def test_render_reports_the_waiting_breakdown(tmp_path: Path) -> None:
    """报表把 waiting_user 拆成单轮/中间/最终三列，便于与事件口径核对。"""
    rows = [
        synthetic_row("Q-1", signals=["waiting_user"]),
        synthetic_row(
            "Q-2",
            multi=True,
            signals=["waiting_user", "waiting_user"],
            deviations=["max_clarifications_exceeded"],
        ),
    ]
    directory = write_arm(tmp_path / "arm", rows)
    report = render([("A", directory)])
    assert "| waiting_user | 单轮等待 | 中间等待 | 最终等待 |" in report
    assert "| A | 2 | 0 | 0 | 2 | 1 | 0 | 1 |" in report
