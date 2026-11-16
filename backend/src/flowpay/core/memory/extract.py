"""会话收尾的确定性排查结论抽取；纯函数，不调模型、不落库（specs/004）。

按 JsonObject 字段存在性适配两种 workflow 的检查点（core 不依赖 workflows）：
manual-loop 带 actions（tool/target/row_count），multi-agent 带 findings
（tool/target/rows/digest）。distill 蒸馏在范围外——先纯结构化，真机数据不足再立项。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from flowpay.core.chat.projection import clip_text
from flowpay.core.chat.record_ids import record_ids
from flowpay.core.json import JsonObject, require_int, require_list, require_object, require_text

from .contracts import NOTE_BODY_CAP, MemoryNote

# 单条结论摘要里 digest 的字符上限；保证最终回答段不被 digest 行挤掉。
DIGEST_LINE_CAP = 200


def extract_note(
    session_id: str, workflow: str, saved_state: JsonObject, answer: str
) -> MemoryNote | None:
    """从检查点与最终回答抽取一条排查结论；没有实质进展时返回 None。

    调用方已保证 status == completed 且记忆链路已启用；此处只判断「有结论可存」：
    最终回答非空，且 actions / findings 至少一个非空（waiting_user 与失败会话由
    调用方以 extract_skipped 事件区分）。
    """
    if not answer.strip():
        return None
    case = saved_state.get("case")
    known: dict[str, str] = {}
    if case is not None:
        stored_known = require_object(require_object(case)["known"])
        known = {str(name): require_text(value) for name, value in stored_known.items()}
    actions = _actions(saved_state.get("actions"))
    findings = _findings(saved_state.get("findings"))
    if not actions and not findings:
        return None
    body = _body(known, actions, findings, answer)
    return MemoryNote(
        note_id=f"note_{uuid4().hex}",
        session_id=session_id,
        workflow=workflow,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        keys=_keys(known, actions, findings, answer),
        conditions={"known": known},
        body=body,
        embedding=None,
        body_chars=len(body),
    )


def _actions(state: object) -> list[tuple[str, str, int]]:
    """manual-loop 检查点的已执行查询列表；字段缺失或为空返回空列表。"""
    if state is None:
        return []
    rows: list[tuple[str, str, int]] = []
    for item in require_list(state):
        record = require_object(item)
        rows.append(
            (
                require_text(record["tool"]),
                require_text(record["target"]),
                require_int(record["row_count"]),
            )
        )
    return rows


def _findings(state: object) -> list[tuple[str, str, int, str]]:
    """multi-agent 检查点的跨轮结论摘要列表；字段缺失或为空返回空列表。"""
    if state is None:
        return []
    rows: list[tuple[str, str, int, str]] = []
    for item in require_list(state):
        record = require_object(item)
        rows.append(
            (
                require_text(record["tool"]),
                require_text(record["target"]),
                require_int(record["rows"]),
                require_text(record["digest"]),
            )
        )
    return rows


def _body(
    known: dict[str, str],
    actions: list[tuple[str, str, int]],
    findings: list[tuple[str, str, int, str]],
    answer: str,
) -> str:
    """三段正文（已知条件 / 查询与结论 / 最终回答），整体截断到 NOTE_BODY_CAP。"""
    sections: list[str] = []
    if known:
        sections.append(
            "已知条件：\n" + "\n".join(f"- {name} = {value}" for name, value in known.items())
        )
    conclusion_lines = [f"- {tool} {target}（{rows} 行）" for tool, target, rows in actions]
    conclusion_lines += [
        f"- {tool} {target}（{rows} 行）：{clip_text(digest, DIGEST_LINE_CAP)}"
        for tool, target, rows, digest in findings
    ]
    if conclusion_lines:
        sections.append("查询与结论：\n" + "\n".join(conclusion_lines))
    sections.append("最终回答：\n" + answer.strip())
    return clip_text("\n\n".join(sections), NOTE_BODY_CAP)


def _keys(
    known: dict[str, str],
    actions: list[tuple[str, str, int]],
    findings: list[tuple[str, str, int, str]],
    answer: str,
) -> list[str]:
    """结构化键：case 值、查询目标、结论摘要与最终回答里的记录编号，casefold 去重。

    键只收记录编号形态的值（record_ids 模式），"production" 这类环境词不算键，
    避免不同账户的会话因共同环境词互相召回。
    """
    text = " ".join(
        [
            *known.values(),
            *(target for _, target, _ in actions),
            *(f"{target} {digest}" for _, target, _, digest in findings),
            answer,
        ]
    )
    return sorted({value.casefold() for value in record_ids(text)})
