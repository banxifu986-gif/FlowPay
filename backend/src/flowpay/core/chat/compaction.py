"""检查点消息流的 LLM 摘要式压缩：纯函数层（切点规划、信封组装、合并 prompt）。

压缩只改写检查点消息流（写路径）；投影继续在请求前对当前消息流做确定性裁剪
（读路径），两层互不感知（specs/003 research.md R-11）。调模型与事件留痕的编排
入口在 core/agent/turn.py，本模块绝不 import 编排依赖。

可删整组与投影（projection.py）同一定义：一条 assistant 消息加它之后连续的
tool 结果；组合不变量由 test_compaction.py::test_projection_after_compaction_
holds_invariants 钉住。摘要信封是普通 system 消息，天然位于投影的不可删集合。
"""

from __future__ import annotations

import json
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from flowpay.core.chat.record_ids import record_ids
from flowpay.core.json import JsonObject, parse_json, require_list, require_object

SUMMARY_KIND = "session_summary"
# 压缩时保留的最近完整交换数；模块常量不进配置面（plan.md Design Summary）。
KEEP_RECENT_ROUNDS = 2
# 摘要请求输入的防御性上限：超过则只取最老部分进摘要，正常触发线下永不命中。
SUMMARY_INPUT_CAP = 120000
# 摘要输入里单条 tool 结果的截断预算，防止"摘要请求比被摘要上下文还大"（R-6）。
TOOL_RESULT_SUMMARY_CAP = 2000

_PREVIOUS_TAG = "previous-summary"
# 排查域五段结构（data-model §2）：模型按此模板产出叙事正文。
SUMMARY_TEMPLATE = (
    "请把以下排查对话压缩为进度摘要，正文不超过 {budget} 个字符，按五段组织：\n"
    "### 已排除假设（每条附排除依据的记录编号）\n"
    "### 当前主假设（最可能原因与指向它的证据编号）\n"
    "### 已确认事实（逐条「编号：关键字段值」，编号原样保留）\n"
    "### 已知条件与约束（范围与改口历史）\n"
    "### 下一步排查项与未能确认\n"
    "规则：只使用对话中出现过的编号，不新增；保留全部既有事实与已排除假设；"
    "只输出摘要正文。"
)


def is_summary_message(message: JsonObject | ChatCompletionMessageParam) -> bool:
    """判定会话摘要信封；非 system、非 JSON、缺 kind、kind 不符一律 False。

    畸形 system 消息按普通消息对待：保留原位、不参与替换，由调用方兜底。
    参数取宽松视图：协议类型与宽松 JSON 视图都能直接传入，调用方无需转换。
    """
    if message.get("role") != "system":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    try:
        body = require_object(parse_json(content))
    except ValueError:
        return False
    return body.get("kind") == SUMMARY_KIND


def select_compaction_range(
    messages: list[JsonObject],
    trigger_chars: int,
    keep_recent_rounds: int,
    *,
    force: bool = False,
) -> list[list[int]] | None:
    """返回待压缩整组的下标分组；不满足触发条件或无收益时返回 None。

    全函数：任何畸形输入返回 None，绝不抛异常。触发看消息流总体积（与投影
    同一字符口径）；保留最近 keep_recent_rounds 个完整交换（至少 1 个，切点
    纪律），其余从最老开始整组压缩。force=True 跳过体积判定（reactive 补救
    已确认超限，specs/004），但切点纪律与「无收益返回 None」不变。
    """
    if (trigger_chars <= 0 and not force) or not messages:
        return None
    try:
        return _select(messages, trigger_chars, keep_recent_rounds, force=force)
    except Exception:
        return None


def _select(
    messages: list[JsonObject],
    trigger_chars: int,
    keep_recent_rounds: int,
    *,
    force: bool = False,
) -> list[list[int]] | None:
    if not force and sum(_chars(message) for message in messages) <= trigger_chars:
        return None
    groups = _groups(messages)
    keep = max(1, keep_recent_rounds)
    if len(groups) <= keep:
        return None
    return groups[: len(groups) - keep]


def compact_messages(
    messages: list[JsonObject],
    groups: list[list[int]],
    summary_text: str,
    summary_budget: int,
) -> tuple[list[ChatCompletionMessageParam], list[str], int]:
    """用摘要信封替换被压缩组，返回 (新消息流, 提取的 record_ids, 压缩轮数)。

    编号由代码从被删消息的 user/tool 正文提取（research R-3），排序去重；摘要
    正文超出 summary_budget 由代码截断（clarify 2026-11-14 决议 B）；新摘要插在
    旧摘要位置或区间起点中更靠前者，流内至多一条摘要。全函数：空组原样带回，
    畸形输入不抛异常，输入列表不被修改。产出与投影同款协议类型列表。
    """
    try:
        return _compact(messages, groups, summary_text, summary_budget)
    except Exception:
        return cast("list[ChatCompletionMessageParam]", list(messages)), [], 0


def _compact(
    messages: list[JsonObject],
    groups: list[list[int]],
    summary_text: str,
    summary_budget: int,
) -> tuple[list[ChatCompletionMessageParam], list[str], int]:
    size = len(messages)
    # 群组下标由调用方传入；畸形值在比较处抛 TypeError，由 compact_messages 的
    # 全函数包装兜住——这里不做 isinstance 防御，保持集合推导干净。
    dropped = {index for group in groups for index in group if 0 <= index < size}
    if not dropped:
        return cast("list[ChatCompletionMessageParam]", list(messages)), [], 0
    found: list[str] = []
    for index in sorted(dropped):
        message = messages[index]
        role = message.get("role")
        content = message.get("content")
        # assistant 的历史转述不计入（与 known_record_ids 同一口径，turn.py）。
        if role in {"user", "tool"} and isinstance(content, str):
            found.extend(record_ids(content))
    # 旧信封携带的编号跨压缩链继承：其原始消息早已删除，不并入则历史编号断裂
    # （specs/003 research.md R-8 对策，对齐 pi 的文件清单累积）。
    for message in messages:
        if is_summary_message(message):
            legacy = require_object(parse_json(str(message.get("content")))).get("record_ids")
            if legacy is not None:
                found.extend(item for item in require_list(legacy) if isinstance(item, str))
    text = _clip(summary_text, max(0, summary_budget))
    envelope = json.dumps(
        {
            "kind": SUMMARY_KIND,
            "text": text,
            "record_ids": sorted(set(found)),
            "rounds": len(groups),
        },
        ensure_ascii=False,
    )
    summary: JsonObject = {"role": "system", "content": envelope}
    start = min(dropped)
    old_positions = [index for index, message in enumerate(messages) if is_summary_message(message)]
    insert_before = min([start, *old_positions])
    kept = [
        (index, message)
        for index, message in enumerate(messages)
        if index not in dropped and not is_summary_message(message)
    ]
    position = sum(1 for index, _ in kept if index < insert_before)
    result = [message for _, message in kept]
    # 病态排序（tool 消息与其 assistant 不相邻）下，删 assistant 会留下引用悬空的
    # tool 消息；属主被删时把这些孤儿一并删除，与投影的清扫纪律对齐（projection
    # contracts §4 条 6）。这类流本就不可发送，清扫是防御对称而非新语义。
    dropped_owner_ids: set[str] = set()
    for index in dropped:
        message = messages[index]
        if message.get("role") == "assistant":
            calls = cast("list[JsonObject]", message.get("tool_calls") or [])
            for call in calls:
                call_id = call.get("id")
                if isinstance(call_id, str):
                    dropped_owner_ids.add(call_id)
    result = [
        message
        for message in result
        if not (message.get("role") == "tool" and message.get("tool_call_id") in dropped_owner_ids)
    ]
    result.insert(position, summary)
    return (
        cast("list[ChatCompletionMessageParam]", result),
        sorted(set(found)),
        len(groups),
    )


def summary_user_prompt(
    messages: list[JsonObject], previous_summary: str | None, char_budget: int
) -> str:
    """构造摘要请求的 user 正文：序列化对话 + 既有摘要标签 + 上限声明。

    tool 结果截断到短预算、整段输入截断到 SUMMARY_INPUT_CAP，防止摘要请求
    比被摘要上下文还大（research R-6）。全函数：畸形消息跳过，绝不抛异常。
    """
    try:
        return _prompt(messages, previous_summary, char_budget)
    except Exception:
        return SUMMARY_TEMPLATE.format(budget=char_budget)


def _prompt(messages: list[JsonObject], previous_summary: str | None, char_budget: int) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "tool":
            label = "[Tool result]"
            text = _clip(content if isinstance(content, str) else "", TOOL_RESULT_SUMMARY_CAP)
        elif role == "user":
            label, text = "[User]", content if isinstance(content, str) else ""
        elif role == "assistant":
            label = "[Assistant]"
            calls = cast("list[JsonObject]", message.get("tool_calls") or [])
            names = "、".join(
                str(cast("JsonObject", call.get("function") or {}).get("name")) for call in calls
            )
            body = content if isinstance(content, str) else ""
            text = f"{body}（发起工具调用：{names}）" if names else body
        else:
            continue  # system 消息不进摘要输入：简报由检查点字段每轮重建。
        if text:
            lines.append(f"{label}: {text}")
    conversation = "\n".join(lines)[:SUMMARY_INPUT_CAP]
    parts = [SUMMARY_TEMPLATE.format(budget=max(0, char_budget))]
    if previous_summary:
        parts.append(f"<{_PREVIOUS_TAG}>\n{previous_summary}\n</{_PREVIOUS_TAG}>")
    parts.append(conversation)
    return "\n\n".join(parts)


def _clip(text: str, budget: int) -> str:
    """字符截断；预算不足一个字符时返回空串（防御负数与 0）。"""
    return text[:budget] if budget > 0 else ""


def _chars(message: JsonObject) -> int:
    """消息的近似体积，与投影同一字符口径（json.dumps 长度，畸形记 0）。"""
    try:
        return len(json.dumps(message, ensure_ascii=False))
    except (TypeError, ValueError):
        return 0


def _groups(messages: list[JsonObject]) -> list[list[int]]:
    """可压缩整组的下标分组；与投影的可删组同一定义（assistant+尾随 tool）。

    与投影的差异只在调用侧：投影把最后一段交换排除在可删组外，压缩把它
    包含在组列表里、由 select 的保留切片（keep>=1）兜住——切点纪律一致。
    """
    groups: list[list[int]] = []
    index = 0
    while index < len(messages):
        if messages[index].get("role") == "assistant":
            group = [index]
            follow = index + 1
            while follow < len(messages) and messages[follow].get("role") == "tool":
                group.append(follow)
                follow += 1
            groups.append(group)
            index = follow
        else:
            index += 1
    return groups
