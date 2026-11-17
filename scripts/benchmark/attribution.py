"""基准失败题归因：读各臂逐题结果，按主因分类并输出对比报告。

用法：python scripts/benchmark/attribution.py --arm A=var/benchmark/<dir> --arm C=var/benchmark/<dir>
主因按失败信号的首要原因判定，每题只计一类，计数之和等于失败题数。
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, NamedTuple

from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_int,
    require_list,
    require_object,
    require_text,
)

ROOT = Path(__file__).resolve().parents[2]

# 失败主因：优先级从高到低，先命中先归类，避免一题计入多类。
CAUSES = {
    "simulated_user": "模拟用户/对话流程偏差（repeated或unexpected分类）",
    "waiting_for_evidence": "追问结束且证据未齐（缺条件先问人）",
    "over_clarify": "追问结束但证据已齐（本可直接作答）",
    "evidence_miss": "证据漏命中（未检索到指定原文/记录）",
    "conclusion_wrong": "结论错误（证据齐但断言不符）",
}


class Row(NamedTuple):
    question_id: str
    question_type: str
    multi_turn: bool
    doc: str
    record: str
    assertion: str
    evidence_pass: bool
    task_pass: bool
    signals: list[str]
    deviations: list[str]


def require_bool(value: object) -> bool:
    """布尔字段必须真是布尔，避免把字符串或数字当成通过结果。"""
    if not isinstance(value, bool):
        raise TypeError("Expected a boolean")
    return value


def require_texts(value: object) -> list[str]:
    """可选的字符串列表字段；缺失视为空列表。"""
    if value is None:
        return []
    return [require_text(item) for item in require_list(value)]


def ratio(item: JsonObject, hit: str, total: str) -> str:
    """把命中/总数两个计数字段格式化成可读比值。"""
    return f"{require_int(item[hit])}/{require_int(item[total])}"


def load_rows(directory: Path) -> list[Row]:
    """读该臂 multi-agent 逐题结果；缺阶段文件时回落到任意 *_agent.jsonl。"""
    candidates = [
        directory / "multi-agent.jsonl",
        *sorted(directory.glob("*agent*.jsonl")),
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise SystemExit(f"no per-question results under {directory}")
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = require_object(parse_json(line))
        rows.append(
            Row(
                question_id=require_text(item["question_id"]),
                question_type=require_text(item["question_type"]),
                multi_turn=require_bool(item["multi_turn"]),
                doc=ratio(item, "doc_hit", "doc_total"),
                record=ratio(item, "record_hit", "record_total"),
                assertion=ratio(item, "assertion_hit", "assertion_total"),
                evidence_pass=require_bool(item["evidence_pass"]),
                task_pass=require_bool(item["task_pass"]),
                signals=require_texts(item.get("signals")),
                deviations=require_texts(item.get("deviations")),
            )
        )
    return rows


def main_cause(row: Row) -> str | None:
    """返回失败主因；通过的题返回 None。"""
    if row.task_pass:
        return None
    if any(d in ("repeated_category", "unexpected_category") for d in row.deviations):
        return "simulated_user"
    if "waiting_user" in row.signals:
        return "over_clarify" if row.evidence_pass else "waiting_for_evidence"
    if not row.evidence_pass:
        return "evidence_miss"
    return "conclusion_wrong"


def model_of(directory: Path) -> str:
    """从该臂报表首部取模型名，确认各臂同模型。"""
    report = directory / "report.md"
    if not report.exists():
        return "unknown"
    for line in report.read_text(encoding="utf-8").splitlines()[:5]:
        match = re.search(r"模型\s*(?:`)?([^，。`\s]+)", line)
        if match:
            return match.group(1)
    return "unknown"


def render(arms: list[tuple[str, Path]]) -> str:
    """生成对比报告：每臂分类计数、失败明细与臂间差异。"""
    lines = [
        "# 基准失败题归因报告",
        "",
        (
            f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M UTC}。单次运行，"
            "分类基于逐题信号，模型随机性导致的差异仍存在，结论留误差余量。"
        ),
        "",
    ]

    loaded = [(label, path, load_rows(path)) for label, path in arms]
    lines += ["## 各臂模型", ""]
    for label, path, _ in loaded:
        lines += [f"- {label}：{model_of(path)}（`{path}`）"]
    lines += [""]

    lines += ["## 分类总表", ""]
    header = (
        "| 臂 | 题数 | 任务通过 | 证据全命中 | waiting_user | "
        + " | ".join(CAUSES)
        + " |"
    )
    lines += [
        header,
        "| --- | ---: | ---: | ---: | ---: | "
        + " | ".join("---:" for _ in CAUSES)
        + " |",
    ]
    detail_sections: list[str] = []
    for label, _, rows in loaded:
        causes = [main_cause(r) for r in rows]
        cells = [str(sum(1 for c in causes if c == key)) for key in CAUSES]
        waiting = sum(1 for r in rows if "waiting_user" in r.signals)
        lines.append(
            f"| {label} | {len(rows)} | {sum(r.task_pass for r in rows)} | "
            + f"{sum(r.evidence_pass for r in rows)} | {waiting} | "
            + " | ".join(cells)
            + " |"
        )
        detail_sections += ["", f"### {label} 失败明细", ""]
        detail_sections += [
            "| 题号 | 题型 | 多轮 | 证据(文档/记录/断言) | 主因 | 信号 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row, cause in zip(rows, causes, strict=True):
            if cause is None:
                continue
            signal = ",".join(row.signals + [f"dev:{d}" for d in row.deviations]) or "-"
            detail_sections.append(
                f"| {row.question_id} | {row.question_type} | "
                + f"{'是' if row.multi_turn else '否'} | "
                + f"{row.doc} / {row.record} / {row.assertion} | {cause} | {signal} |"
            )
    lines += [""] + detail_sections

    if len(loaded) >= 2:
        lines += ["", "## 臂间差异", ""]
        (l1, _, r1), (l2, _, r2) = loaded[0], loaded[1]
        ids = {r.question_id: r for r in r2}
        left = {r.question_id: r for r in r1}
        flipped_to_pass = [
            f"{q}（{left[q].question_type}）"
            for q in ids
            if left[q].task_pass is False and ids[q].task_pass
        ]
        flipped_to_fail = [
            f"{q}（{left[q].question_type}）"
            for q in ids
            if left[q].task_pass and ids[q].task_pass is False
        ]
        lines += [
            f"- {l2} 相对 {l1} 由失败转通过：{'; '.join(flipped_to_pass) or '无'}",
            f"- {l2} 相对 {l1} 由通过转失败：{'; '.join(flipped_to_fail) or '无'}",
        ]
    return "\n".join(lines) + "\n"


class Arguments(argparse.Namespace):
    """命令行参数；argparse 的属性是 Any，在这里一次性声明类型。"""

    arm: ClassVar[list[str]] = []
    output: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        metavar="LABEL=DIR",
        help="臂标签与输出目录，可重复；第一个臂为对比基准",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(namespace=Arguments())

    arms: list[tuple[str, Path]] = []
    for item in args.arm:
        label, _, raw = item.partition("=")
        if not label or not raw:
            raise SystemExit(f"bad --arm value: {item}")
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        arms.append((label, path))

    output = args.output or (
        ROOT / "var/benchmark" / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-attribution.md"
    )
    output.write_text(render(arms), encoding="utf-8")
    print(f"归因报告：{output}")


if __name__ == "__main__":
    main()
