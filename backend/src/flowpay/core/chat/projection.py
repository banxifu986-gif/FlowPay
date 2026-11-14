"""请求前上下文投影：把检查点原生消息压缩成实际发送的消息。

投影是纯函数与全函数：不持久化、不修改输入、绝不抛异常——任何畸形输入原样
返回。两层机制按序应用（specs/002 research.md R-1）：超限的旧工具结果先做占位
替换（role 与 tool_call_id 原样，配对不破），替换后仍超预算再从最老完整轮次
整组删除。首条 system、任何 user 消息与最后一段 assistant 交换永不删除；不可
删集合自身超预算时预算未达成不算失败，存活不变量优先（contracts/interfaces.md §4）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from flowpay.core.chat.record_ids import record_ids
from flowpay.core.json import JsonObject, parse_json


@dataclass(frozen=True)
class ContextPolicy:
    """投影策略；None 表示现行为，由调用方短路（不进入本模块）。"""

    char_budget: int
    tool_result_budget: int


def project_messages(
    messages: list[ChatCompletionMessageParam], policy: ContextPolicy
) -> list[ChatCompletionMessageParam]:
    """返回投影后的新消息列表；输入列表不被修改，任何异常输入原样返回。"""
    if not messages:
        return messages
    try:
        return cast(
            "list[ChatCompletionMessageParam]",
            _project(cast("list[JsonObject]", messages), policy),
        )
    except Exception:
        # 全函数契约：投影失败的最坏结果是大上下文原样发出，而不是本轮调查崩溃。
        return messages


def _project(messages: list[JsonObject], policy: ContextPolicy) -> list[JsonObject]:
    """两层压缩：先占位替换超限的旧工具结果，仍超预算再整组删最老可删轮次。"""
    projected = _replace_oversized_tools(messages, policy.tool_result_budget)
    if _total_chars(projected) > policy.char_budget:
        projected = _drop_oldest_rounds(projected, policy.char_budget)
    return projected


def _role(message: JsonObject) -> object:
    """消息角色；缺 role 的畸形消息按未知处理，不参与任何删除判定。"""
    return message.get("role")


def _chars(message: object) -> int:
    """消息的近似体积；序列化失败的畸形消息按 0 计，宁可保留不误删。"""
    try:
        return len(json.dumps(message, ensure_ascii=False))
    except (TypeError, ValueError):
        return 0


def _total_chars(messages: list[JsonObject]) -> int:
    """全部消息的近似总体积。"""
    return sum(_chars(message) for message in messages)


def _tool_id(message: JsonObject) -> str | None:
    """工具消息携带的 tool_call_id；非字符串视为缺失。"""
    value = message.get("tool_call_id")
    return value if isinstance(value, str) else None


def _last_assistant_index(messages: list[JsonObject]) -> int:
    """最后一条 assistant 消息的下标；没有 assistant 时为 -1。"""
    last = -1
    for index, message in enumerate(messages):
        if _role(message) == "assistant":
            last = index
    return last


def _tool_names(messages: list[JsonObject]) -> dict[str, str]:
    """tool_call_id 到工具名的映射，占位摘要的 target 由此回查。"""
    names: dict[str, str] = {}
    for message in messages:
        if _role(message) != "assistant":
            continue
        calls = cast("list[JsonObject]", message.get("tool_calls") or [])
        for call in calls:
            function = call.get("function")
            call_id = call.get("id")
            if isinstance(call_id, str) and isinstance(function, dict):
                name = cast("JsonObject", function).get("name")
                if isinstance(name, str):
                    names[call_id] = name
    return names


def _live_tool_ids(messages: list[JsonObject]) -> set[str]:
    """最后一段 assistant 交换里的 tool_call_id 集合；这些工具结果保持原样。"""
    last = _last_assistant_index(messages)
    live: set[str] = set()
    for message in messages[last + 1 :]:
        tool_id = _tool_id(message)
        if tool_id is not None:
            live.add(tool_id)
    return live


def _row_count(text: str) -> int | None:
    """工具结果里 rows 列表的长度；解析不出记 None，不猜。"""
    try:
        parsed = parse_json(text)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        rows = cast("JsonObject", parsed).get("rows")
        if isinstance(rows, list):
            return len(cast("list[object]", rows))
    return None


def _placeholder_for(message: JsonObject, tool_name: str) -> JsonObject:
    """被压缩工具结果的替身；记录编号清单保留可核查性（不变量 3）。"""
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    placeholder: JsonObject = {
        "placeholder": True,
        "target": tool_name,
        "rows": _row_count(text),
        "record_ids": sorted(set(record_ids(text))),
        "original_chars": len(text),
    }
    return {
        "role": "tool",
        "tool_call_id": message.get("tool_call_id"),
        "content": json.dumps(placeholder, ensure_ascii=False),
    }


def _replace_oversized_tools(messages: list[JsonObject], budget: int) -> list[JsonObject]:
    """把超出单条预算的旧工具结果换成占位摘要；role 与 tool_call_id 原样保留。

    预算只是触发条件：占位摘要先构造后比较体积，不比原结果小时保留原样——
    替换本身不得使上下文变胖（contracts §4 条 6）。
    """
    live = _live_tool_ids(messages)
    names = _tool_names(messages)
    result: list[JsonObject] = []
    for message in messages:
        tool_id = _tool_id(message)
        if (
            _role(message) == "tool"
            and tool_id is not None
            and tool_id not in live
            and _chars(message) > budget
        ):
            placeholder = _placeholder_for(message, names.get(tool_id, ""))
            result.append(placeholder if _chars(placeholder) < _chars(message) else message)
        else:
            result.append(message)
    return result


def _drop_oldest_rounds(messages: list[JsonObject], budget: int) -> list[JsonObject]:
    """从最老的可删轮次整组删除（assistant + 其尾随 tool 结果），直到预算满足或无可删。

    可删组只含 assistant 与 tool 消息；夹在轮次之间的 system 消息、首条 system、
    任何 user 与最后一段交换都不在组内，天然存活。
    """
    last_assistant = _last_assistant_index(messages)
    groups: list[list[int]] = []
    index = 0
    while index < len(messages):
        if _role(messages[index]) == "assistant" and index != last_assistant:
            group = [index]
            follow = index + 1
            while follow < len(messages) and _role(messages[follow]) == "tool":
                group.append(follow)
                follow += 1
            groups.append(group)
            index = follow
        else:
            index += 1
    sizes = {position: _chars(message) for position, message in enumerate(messages)}
    total = sum(sizes.values())
    dropped: set[int] = set()
    for group in groups:
        if total <= budget:
            break
        total -= sum(sizes[position] for position in group)
        dropped.update(group)
    # 病态排序（tool 消息与其 assistant 不相邻）下，删 assistant 会留下引用悬空的
    # tool 消息，供应商会拒绝整个请求；属主被删时把这些 tool 一并删除（contracts §4 条 6）。
    dropped_ids: set[str] = set()
    for position in dropped:
        if _role(messages[position]) == "assistant":
            calls = cast("list[JsonObject]", messages[position].get("tool_calls") or [])
            dropped_ids.update(
                call_id for call in calls if isinstance((call_id := call.get("id")), str)
            )
    for position, message in enumerate(messages):
        if _role(message) == "tool" and _tool_id(message) in dropped_ids:
            dropped.add(position)
    return [message for position, message in enumerate(messages) if position not in dropped]
