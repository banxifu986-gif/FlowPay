#!/usr/bin/env python3
"""驱动多轮题：判断助手是不是在追问，再决定模拟用户披露什么。

`dataset` 的模拟用户按关键词判断"问的是哪一类"，但它不负责判断"这一轮到底是不是追问"。
直接拿关键词当追问信号会误判——告别语和能力介绍都能命中多个类别。因此这里先用三级信号
判定追问，再把类别交给模拟用户。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from flowpay.core.event_log.types import Event
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_list,
    require_object,
    require_text,
)

# manual-loop 的 ask_user 显式记录缺哪几项，007 起 synthesize 决策也会发 clarification.decided；
# 这里把字段名翻译成 benchmark 的类别：条件字段照旧，四个背景主题映射到同名类别。
FIELD_CATEGORY: dict[str, str] = {
    "account_id": "account_context",
    "environment": "account_context",
    "payment_id": "identifiers",
    "request_id": "identifiers",
    "event_id": "identifiers",
    "delivery_id": "identifiers",
    "endpoint_id": "identifiers",
    "customer_id": "identifiers",
    "goal": "goal",
    "symptom_details": "symptom_details",
    "occurrence_time": "occurrence_time",
    "api_version": "api_version",
}

Signal = Literal["waiting_user", "ask_user_tool", "text", "none"]

# 偏离预设流程的情况，逐条记进报表，不当异常处理。
Deviation = Literal[
    "unexpected_category",
    "clarification_not_recognized",
    "max_clarifications_exceeded",
    "answered_without_clarification",
    "repeated_category",
]


@dataclass
class Exchange:
    """一次澄清往返的判定依据，报表可据此回查判错在哪一步。"""

    signal: Signal
    categories: list[str]
    disclosed: list[str]
    reply: str


@dataclass
class Transcript:
    """一道多轮题的完整过程与偏离记录。"""

    exchanges: list[Exchange] = field(default_factory=list)
    deviations: list[Deviation] = field(default_factory=list)

    def note(self, deviation: Deviation) -> None:
        """记录一种偏离；同种只记一次，便于按题计数。"""
        if deviation not in self.deviations:
            self.deviations.append(deviation)


def _declared_fields(payload: JsonObject, key: str) -> list[str]:
    """读载荷里的一个缺项列表；缺失或为 null 视为空。"""
    return [require_text(item) for item in require_list(payload.get(key) or [])]


def asked_fields(events: Sequence[Event], run_id: str | None) -> list[str]:
    """本轮声明缺少的字段，按声明顺序去重。

    两条通道：tool.started 里 ask_user 的 missing/topics，与 clarification.decided
    的 topics/conditions（FR-12）。旧臂没有决策事件，单靠 ask_user 也能读出缺项；
    两类事件都没有时返回空列表。
    """
    fields: list[str] = []
    for event in events:
        if event["run_id"] != run_id:
            continue
        payload = require_object(event["payload"])
        if event["type"] == "tool.started" and payload.get("name") == "ask_user":
            arguments = require_object(parse_json(require_text(payload["arguments"])))
            fields.extend(_declared_fields(arguments, "missing"))
            fields.extend(_declared_fields(arguments, "topics"))
        elif event["type"] == "clarification.decided":
            fields.extend(_declared_fields(payload, "topics"))
            fields.extend(_declared_fields(payload, "conditions"))
    return list(dict.fromkeys(fields))


def looks_like_question(text: str) -> bool:
    """正文是否像在提问：必须真的有问号，避免把陈述句当追问。"""
    return "?" in text or "？" in text


def detect(
    status: str,
    answer: str,
    events: Sequence[Event],
    run_id: str | None,
    keyword_categories: set[str],
) -> tuple[Signal, list[str]]:
    """判定这一轮是不是追问，以及问的是哪些类别。

    三级信号按可靠性排序：waiting_user 精确；ask_user 的 missing 字段精确且自带类别；
    最后才回退到正文关键词，且要求正文真的含问号。回退这一级不可靠，
    调用方需要把用了哪一级写进报表。
    """
    if status == "waiting_user":
        fields = asked_fields(events, run_id)
        mapped = sorted(
            {FIELD_CATEGORY[name] for name in fields if name in FIELD_CATEGORY}
        )
        return "waiting_user", mapped or sorted(keyword_categories)
    fields = asked_fields(events, run_id)
    if fields:
        mapped = sorted(
            {FIELD_CATEGORY[name] for name in fields if name in FIELD_CATEGORY}
        )
        return "ask_user_tool", mapped
    if keyword_categories and looks_like_question(answer):
        return "text", sorted(keyword_categories)
    return "none", []
