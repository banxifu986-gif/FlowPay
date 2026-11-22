#!/usr/bin/env python3
"""澄清机制统计：读臂事件库与题库，输出覆盖率、重复问与等待轮带结论抽检。

覆盖率取采纳轮（synthesize 最终采纳的 clarify 决策；旧臂回退 ask_user +
waiting_user）字段 ∪ 检查点已回答主题 ∪ 检查点已提供条件（case.known）的
类别并集；映射成 benchmark 类别后与题库`required_clarification_categories`
比较。覆盖率只统计确实发过问的**多轮题**：单轮题没有必需类别，进分子/分母
会把整题全覆盖虚高；worker 提议但改判作答、被 Clarifier 拒绝/去重的项都
不算索要（用户从未看到）。单轮等待仍计入等待轮总数与带结论判定。旧臂没有
决策事件、检查点也没有 clarification 段，一律按空容忍，数字仅作基线参考。

用法：python scripts/benchmark/clarification.py --arm C=var/benchmark/<dir> [--output <md>]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

from flowpay.core.json import JsonObject, parse_json, require_object, require_text
from flowpay.dataset import require_dataset

ROOT = Path(__file__).resolve().parents[2]
# 同时支持 python scripts/benchmark/clarification.py 与 -m 方式调用。
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark import conversation

# 等待轮带结论的判定标记：与 clarify_body 的确定性拼接一致（FR-4），按用户可见
# 正文判定，不按事件的 has_reports 推断。
SUMMARY_MARKERS = ("已查到的结论", "尚未查到结论")

# 未证实口径（specs/009 FR-7/SC-5）：作答里查不到的子项以此统一表述；与
# citation_check.UNVERIFIED_MARK 是同一词面，这里按字符串判定避免引入后端
# workflows 层依赖（scripts 只通过事件与正文投影，不 import 业务实现）。
UNVERIFIED_MARK = "未能核实"

# 统计需要的事件类型；旧臂没有 clarification.decided 也能容忍。
SESSION_TYPES = (
    "session.created",
    "message.user",
    "message.assistant",
    "tool.started",
    "clarification.decided",
    "run.finished",
)


def normalize(text: str) -> str:
    """空白归一化：连续空白折成单个空格，去掉首尾空白。"""
    return " ".join(text.split())


def string_items(value: object) -> list[str]:
    """容忍坏结构的字符串列表读取：不是列表或元素不是字符串就当空。"""
    if not isinstance(value, list):
        return []
    return [item for item in cast("list[object]", value) if isinstance(item, str)]


def categories_of(fields: Sequence[str]) -> set[str]:
    """把原始字段名映射成 benchmark 类别；映射外的字段忽略。"""
    return {
        conversation.FIELD_CATEGORY[name]
        for name in fields
        if name in conversation.FIELD_CATEGORY
    }


def has_summary(text: str) -> bool:
    """等待轮正文是否带结论：正文里出现确定性拼接的摘要段标记。"""
    return any(marker in text for marker in SUMMARY_MARKERS)


def unverified_rounds(texts: Sequence[str]) -> int:
    """正文中含统一未证实口径的轮数（specs/009 SC-5 数据源）。

    只做词面判定：出现"未能核实"即计一轮，不区分等待轮与作答轮——
    口径约束的是"查不到的子项以统一口径出现"，出现位置由后续逐题对照回查。
    """
    return sum(1 for text in texts if UNVERIFIED_MARK in text)


@dataclass
class SessionLog:
    """一个会话里与澄清相关的事件投影。"""

    session_id: str
    first_message: str = ""
    # run_id -> 该轮 ask_user 声明的 missing/topics
    asks: dict[str, list[str]] = field(default_factory=dict)
    # run_id -> 该轮 clarify 决策声明的 topics/conditions
    decided: dict[str, list[str]] = field(default_factory=dict)
    # run_id -> 该轮最终决策的模式（answer/clarify）；旧臂没有决策事件
    decided_modes: dict[str, str] = field(default_factory=dict)
    # run_id -> 该轮 run.finished 的状态
    statuses: dict[str, str] = field(default_factory=dict)
    # run_id -> 等待轮的用户可见正文
    waiting_texts: dict[str, str] = field(default_factory=dict)
    # 本会话全部助手正文（按轮去重）：未证实口径统计的数据源（SC-5）。
    assistant_texts: list[str] = field(default_factory=list)
    # 最终检查点 clarification.answers 的主题（已回答项）
    answers: list[str] = field(default_factory=list)
    # 跨轮 answers 并集：后期被误清的主题仍算收集过（计量口径，不改状态语义）
    collected_answers: list[str] = field(default_factory=list)
    # 最终检查点 case.known 的条件字段（用户已提供的条件）
    known: list[str] = field(default_factory=list)
    # 会话最后一个 run.finished 是否以 waiting_user 结束（多轮最终轮等待）
    final_waiting: bool = False


def checkpoint_answers(payload: JsonObject) -> list[str] | None:
    """读 run.finished 检查点的已回答主题；没有 clarification 段时返回 None。

    坏结构不抛异常：状态不是对象或 clarification 段缺失都不改写已回答列表，
    段存在但 answers 坏结构时按空处理。
    """
    try:
        state = require_object(payload.get("state") or {})
    except ValueError:
        return None
    if "clarification" not in state:
        return None
    try:
        clarification = require_object(state.get("clarification") or {})
        answers = require_object(clarification.get("answers") or {})
    except ValueError:
        return []
    return list(answers)


def checkpoint_known(payload: JsonObject) -> list[str]:
    """读检查点里用户已提供的条件字段；坏结构按空处理。

    SC-1：用户已提供的信息不要求再问，不能算漏问——自发给出的条件值落在
    Case.known，不在澄清状态里，覆盖率并集必须读它。
    """
    try:
        state = require_object(payload.get("state") or {})
        case = require_object(state.get("case") or {})
        known = require_object(case.get("known") or {})
    except ValueError:
        return []
    # JSON 对象的键本来就是字符串，不需要再过滤。
    return list(known)


def arguments_of(payload: JsonObject) -> JsonObject:
    """读工具调用的 arguments；不是 JSON 对象就当空，坏结构不阻塞统计。"""
    raw = payload.get("arguments")
    if not isinstance(raw, str):
        return {}
    try:
        return require_object(parse_json(raw))
    except ValueError:
        return {}


def read_session_logs(arm: Path) -> list[SessionLog]:
    """读一个臂的事件库，投影出每个会话的澄清相关记录。

    会话按首次出现顺序返回；匹配不到题目的会话由调用方按跳过计数。
    """
    path = arm / "multi-agent" / "sessions.sqlite"
    if not path.exists():
        raise ValueError(f"no session store under {arm}")
    with sqlite3.connect(path) as db:
        rows = cast(
            "list[tuple[str, int, str, str | None, str]]",
            db.execute(
                "SELECT session_id, seq, type, run_id, data FROM events"
                + f" WHERE type IN ({', '.join('?' * len(SESSION_TYPES))})"
                + " ORDER BY session_id, seq",
                SESSION_TYPES,
            ).fetchall(),
        )
    logs: dict[str, SessionLog] = {}
    # (session, run) -> 该轮最近一条助手正文；等待轮正文取 run.finished 之前的最后一条。
    last_assistant: dict[tuple[str, str], str] = {}
    for session_id, _seq, kind, run_id, data in rows:
        log = logs.setdefault(session_id, SessionLog(session_id))
        try:
            record = require_object(parse_json(data))
        except ValueError:
            continue
        try:
            payload = require_object(record.get("payload") or {})
        except ValueError:
            payload = {}
        if kind == "message.user":
            if not log.first_message:
                log.first_message = require_text(payload.get("content"))
        elif kind == "message.assistant":
            content = require_text(payload.get("content"))
            if run_id:
                last_assistant[(session_id, run_id)] = content
            log.assistant_texts.append(content)
        elif kind == "tool.started" and payload.get("name") == "ask_user":
            arguments = arguments_of(payload)
            fields = string_items(arguments.get("missing")) + string_items(
                arguments.get("topics")
            )
            log.asks.setdefault(run_id or "", []).extend(fields)
        elif kind == "clarification.decided":
            fields = string_items(payload.get("topics")) + string_items(
                payload.get("conditions")
            )
            log.decided.setdefault(run_id or "", []).extend(fields)
            if isinstance(payload.get("mode"), str):
                log.decided_modes[run_id or ""] = str(payload["mode"])
        elif kind == "run.finished":
            waiting = payload.get("status") == "waiting_user"
            if isinstance(payload.get("status"), str):
                log.statuses[run_id or ""] = str(payload["status"])
            if waiting and run_id:
                log.waiting_texts[run_id] = last_assistant.get((session_id, run_id), "")
            log.final_waiting = waiting
            answers = checkpoint_answers(payload)
            if answers is not None:
                log.answers = answers
                # 跨轮并集：主题一旦收集过就计入覆盖，即便后期被 unavailable 误清
                # （观测口径；产品状态语义不变，仍以恢复规则为准）。
                for topic in answers:
                    if topic not in log.collected_answers:
                        log.collected_answers.append(topic)
            for name in checkpoint_known(payload):
                if name not in log.known:
                    log.known.append(name)
    return list(logs.values())


@dataclass(frozen=True)
class QuestionStats:
    """一道题的澄清机制统计；matched 之外的字段只在匹配到的题上有意义。"""

    question_id: str
    question_type: str
    multi_turn: bool
    asked: bool
    covered: frozenset[str]
    required: frozenset[str]
    repeated: list[str]
    waiting_rounds: int
    waiting_summaries: int
    waiting_final: bool
    # 含统一未证实口径的轮数（specs/009 SC-5 数据源）；含全部轮次正文。
    unverified_rounds: int = 0

    @property
    def missing(self) -> list[str]:
        """必需类别里尚未覆盖的项，排序后供报表引用。"""
        return sorted(self.required - self.covered)


def initial_message_of(case: JsonObject) -> str:
    """本题首条用户消息：多轮取 initial_user_message，单轮取题目正文。"""
    talk = require_text_of_conversation(case)
    if talk.get("mode") == "progressive_disclosure":
        return require_text(talk["initial_user_message"])
    return require_text(case["question"])


def require_text_of_conversation(case: JsonObject) -> JsonObject:
    """取题目对话元数据；不是对象时报错，不退回强转。"""
    return require_object(case["conversation"])


def adopted_asks(log: SessionLog) -> dict[str, list[str]]:
    """各轮真正摆到用户面前的索要字段。

    采纳口径：synthesize 最终采纳的 clarify 决策（decided mode=clarify，含确定性
    回退）；旧臂没有决策事件，回退 ask_user 字段 + waiting_user。worker 提议但
    模型改判作答的问题用户从未看到（specs/007 Clarifications），被 Clarifier
    拒绝/去重的项也不在正文里（事件记的是原始参数）——都不算索要。
    """
    adopted: dict[str, list[str]] = {}
    for run_id in sorted(set(log.asks) | set(log.decided)):
        mode = log.decided_modes.get(run_id)
        if mode == "clarify":
            fields = log.decided.get(run_id, [])
        elif mode is None and log.statuses.get(run_id) == "waiting_user":
            fields = log.asks.get(run_id, [])
        else:
            continue
        if fields:
            adopted[run_id] = fields
    return adopted


def stats_of(question_id: str, case: JsonObject, log: SessionLog) -> QuestionStats:
    """把一个会话的投影与题目标签合成逐题统计。"""
    talk = require_text_of_conversation(case)
    # 每轮索要的类别；重复问按"同一类别出现在多少个不同轮"计，只用采纳轮。
    run_categories: dict[str, set[str]] = {}
    for run, fields in adopted_asks(log).items():
        run_categories.setdefault(run, set()).update(categories_of(fields))
    covered: set[str] = set()
    for categories in run_categories.values():
        covered |= categories
    covered |= categories_of(log.collected_answers)
    covered |= categories_of(log.known)
    asked = bool(run_categories)
    repeated = sorted(
        category
        for category in {c for cats in run_categories.values() for c in cats}
        if sum(1 for cats in run_categories.values() if category in cats) >= 2
    )
    return QuestionStats(
        question_id=question_id,
        question_type=require_text(case["question_type"]),
        multi_turn=talk.get("mode") == "progressive_disclosure",
        asked=asked,
        covered=frozenset(covered),
        required=frozenset(string_items(talk.get("required_clarification_categories"))),
        repeated=repeated,
        waiting_rounds=len(log.waiting_texts),
        waiting_summaries=sum(
            1 for text in log.waiting_texts.values() if has_summary(text)
        ),
        waiting_final=log.final_waiting
        and talk.get("mode") == "progressive_disclosure",
        unverified_rounds=unverified_rounds(log.assistant_texts),
    )


def analyze(
    questions: Sequence[JsonObject], logs: Sequence[SessionLog]
) -> tuple[list[QuestionStats], int]:
    """把会话按首条消息匹配到题目后逐题统计；返回统计列表与跳过数。

    首条消息匹配不到任何题目的会话跳过并计数，绝不猜它属于哪道题。
    """
    matchers: dict[str, str] = {}
    by_id: dict[str, JsonObject] = {}
    for case in questions:
        question_id = require_text(case["question_id"])
        by_id[question_id] = case
        matchers.setdefault(normalize(initial_message_of(case)), question_id)
    stats: list[QuestionStats] = []
    skipped = 0
    for log in logs:
        question_id = matchers.get(normalize(log.first_message))
        if question_id is None:
            skipped += 1
            continue
        stats.append(stats_of(question_id, by_id[question_id], log))
    return stats, skipped


def render(results: Sequence[tuple[str, list[QuestionStats], int]]) -> str:
    """生成对比报告：每臂的覆盖率、类别缺失、重复问与等待轮带结论。"""
    lines = [
        "# 澄清机制统计报告",
        "",
        (
            f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M UTC}。覆盖率取采纳轮"
            "（synthesize 最终采纳的 clarify 决策；旧臂回退 ask_user + waiting_user）"
            "字段 ∪ 检查点已回答主题 ∪ 检查点已提供条件（case.known）的类别并集，"
            "只统计确实发过问的多轮题（SC-1）：worker 提议但改判作答的问题用户从未"
            "看到，不算索要；已回答或已提供的项不算漏问。重复问只在采纳轮之间计。"
            "等待轮带结论按用户可见正文判定。旧臂没有决策事件与检查点澄清段，"
            "数字仅作基线参考。"
        ),
        "",
    ]
    for label, stats, skipped in results:
        # SC-1 口径：覆盖率与重复问只看发过问的多轮题。
        asked = [entry for entry in stats if entry.asked and entry.multi_turn]
        fully = [entry for entry in asked if not entry.missing]
        required_cells = sum(len(entry.required) for entry in asked)
        covered_cells = sum(len(entry.covered & entry.required) for entry in asked)
        waiting = sum(entry.waiting_rounds for entry in stats)
        summaries = sum(entry.waiting_summaries for entry in stats)
        multi_waiting = sum(entry.waiting_rounds for entry in stats if entry.multi_turn)
        single_waiting = waiting - multi_waiting
        final_waiting = sum(1 for entry in stats if entry.waiting_final)
        repeats = [(entry.question_id, c) for entry in asked for c in entry.repeated]
        lines += [
            f"## {label}",
            "",
            f"- 会话 {len(stats) + skipped}，匹配 {len(stats)}，跳过 {skipped}",
            f"- 多轮发问题数 {len(asked)}；整题全覆盖 {len(fully)}/{len(asked)}"
            + (
                f"；类别级覆盖 {covered_cells}/{required_cells}"
                if required_cells
                else ""
            ),
            f"- 重复问 {len(repeats)} 处",
            (
                f"- 等待轮 {waiting}（多轮 {multi_waiting}；"
                f"其中多轮最终轮 {final_waiting}），带结论 {summaries}，"
                f"不带结论 {waiting - summaries}"
            ),
            f"- 单轮等待轮数 {single_waiting}（不计入覆盖分母）",
            "",
        ]
        # 未证实口径（specs/009 SC-5）：含"未能核实"的轮数与题数，供验收对照。
        unverified = sum(entry.unverified_rounds for entry in stats)
        unverified_questions = [entry for entry in stats if entry.unverified_rounds]
        lines += [
            f"- 未证实口径：{unverified} 轮（{len(unverified_questions)} 题）",
            "",
        ]
        categories = sorted({c for entry in asked for c in entry.required})
        if categories:
            lines += [
                "| 类别 | 需要该类的发问题数 | 已覆盖 | 缺失 |",
                "| --- | ---: | ---: | ---: |",
            ]
            for category in categories:
                needing = [entry for entry in asked if category in entry.required]
                missing = [entry for entry in needing if category not in entry.covered]
                lines.append(
                    f"| {category} | {len(needing)} | {len(needing) - len(missing)} | {len(missing)} |"
                )
            lines.append("")
        if repeats:
            lines += ["重复问明细：", ""]
            lines += [f"- {qid}：{category}" for qid, category in repeats]
            lines.append("")
        without = [
            entry for entry in stats if entry.waiting_rounds > entry.waiting_summaries
        ]
        if without:
            lines += [
                "等待轮不带结论的题（含单轮）："
                + "、".join(entry.question_id for entry in without),
                "",
            ]
        uncovered = [entry for entry in asked if entry.missing]
        if uncovered:
            lines += ["未覆盖全部必需类别的发问题：", ""]
            lines += [
                f"- {entry.question_id}：缺 {'、'.join(entry.missing)}"
                for entry in uncovered
            ]
            lines.append("")
    return "\n".join(lines) + "\n"


def blueprints() -> list[JsonObject]:
    """读取题库全部题目；与 run.py 同一份数据源。"""
    source = require_dataset() / "benchmark/design/benchmark_blueprints.jsonl"
    lines = source.read_text(encoding="utf-8").splitlines()
    return [require_object(parse_json(line)) for line in lines if line.strip()]


class Arguments(argparse.Namespace):
    """命令行参数；argparse 的属性是 Any，在这里一次性声明类型。"""

    arm: ClassVar[list[str]] = []
    output: Path | None = None


def main() -> None:
    """按臂统计并写出 Markdown 报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        metavar="LABEL=DIR",
        help="臂标签与输出目录，可重复",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(namespace=Arguments())

    questions = blueprints()
    results: list[tuple[str, list[QuestionStats], int]] = []
    for item in args.arm:
        label, _, raw = item.partition("=")
        if not label or not raw:
            raise SystemExit(f"bad --arm value: {item}")
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        stats, skipped = analyze(questions, read_session_logs(path))
        results.append((label, stats, skipped))

    output = args.output or (
        ROOT / "var/benchmark" / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-clarification.md"
    )
    output.write_text(render(results), encoding="utf-8")
    print(f"澄清机制统计：{output}")


if __name__ == "__main__":
    main()
