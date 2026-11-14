"""投影四不变量与长会话场景（T014/T017）。"""

import copy
import json
from typing import cast

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from flowpay.core.chat.projection import ContextPolicy, project_messages
from flowpay.core.json import JsonObject, parse_json, require_object

POLICY = ContextPolicy(char_budget=3000, tool_result_budget=400)
# 收紧的整体预算，驱动"占位替换之后仍超限"的整组删除路径。
TIGHT = ContextPolicy(char_budget=1000, tool_result_budget=400)


def system_message(content: str) -> ChatCompletionSystemMessageParam:
    return {"role": "system", "content": content}


def user_message(content: str) -> ChatCompletionUserMessageParam:
    return {"role": "user", "content": content}


def assistant_call(identifier: str) -> ChatCompletionAssistantMessageParam:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {
                    "name": "query_sql",
                    "arguments": json.dumps({"account": "acct_bg_001"}),
                },
            }
        ],
    }


def tool_message(identifier: str, content: str) -> ChatCompletionToolMessageParam:
    return {"role": "tool", "tool_call_id": identifier, "content": content}


def big_result(rows: int = 4, record_id: str = "pay_bg_001_0003") -> str:
    """大工具结果：rows 列表撑起体积，同时携带可核查的记录编号。"""
    return json.dumps(
        {"rows": [{"id": record_id, "note": "x" * 150} for _ in range(rows)]},
        ensure_ascii=False,
    )


def long_conversation(rounds: int) -> list[ChatCompletionMessageParam]:
    """system + user + N 轮（assistant+大工具结果）+ 收尾 user。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
    ]
    for index in range(rounds):
        messages.append(assistant_call(f"c{index}"))
        messages.append(tool_message(f"c{index}", big_result()))
    messages.append(user_message("结合上面的结果回答"))
    return messages


def as_objects(messages: list[ChatCompletionMessageParam]) -> list[JsonObject]:
    """断言用的宽松视图：异构消息统一按 JSON 对象逐键读取。"""
    return cast("list[JsonObject]", messages)


def test_placeholder_replaces_oversized_tool_results() -> None:
    """超限工具结果被占位摘要替换：role/tool_call_id 原样，编号与行数可核查。"""
    messages = long_conversation(2)
    result = as_objects(project_messages(messages, POLICY))
    placeholder = result[3]
    assert placeholder.get("role") == "tool"
    assert placeholder.get("tool_call_id") == "c0"
    body = require_object(parse_json(str(placeholder.get("content"))))
    assert body["placeholder"] is True
    assert body["target"] == "query_sql"
    assert body["rows"] == 4
    assert body["record_ids"] == ["pay_bg_001_0003"]
    original = body["original_chars"]
    assert isinstance(original, int) and original > POLICY.tool_result_budget
    # 输入列表不被修改——检查点原生消息未压缩（FR-8）。
    assert as_objects(messages)[3]["content"] == big_result()


def test_pairing_is_never_broken() -> None:
    """整组删除与占位替换之后，assistant.tool_calls 与 tool 消息仍逐对完整。"""
    result = as_objects(project_messages(long_conversation(6), TIGHT))
    for index, message in enumerate(result):
        if message.get("role") != "assistant":
            continue
        calls = cast("list[JsonObject]", message.get("tool_calls") or [])
        identifiers = [str(call.get("id")) for call in calls]
        following: list[str] = []
        for later in result[index + 1 :]:
            if later.get("role") != "tool":
                break
            following.append(str(later.get("tool_call_id")))
        assert identifiers == following


def test_protected_messages_survive_deletion() -> None:
    """首条 system、任何 user、最后一段 assistant 交换在删除后原样存活。"""
    messages = long_conversation(6)
    result = as_objects(project_messages(messages, TIGHT))
    assert result[0] == messages[0]  # 首条 system
    assert result[1] == messages[1]  # 开头 user
    assert result[-1] == messages[-1]  # 收尾 user
    assert result[-2] == messages[-2] and result[-3] == messages[-3]  # 最后一段交换
    # 最老的轮次已被整组删除，而不是拆开配对。
    assert all(message.get("tool_call_id") != "c0" for message in result)


def test_malformed_inputs_returned_as_is() -> None:
    """全函数契约：畸形输入一律原样返回，绝不抛异常。"""
    cases: list[list[object]] = [
        [],
        ["garbage"],
        [{"content": "缺 role"}],
        [{"role": "tool", "content": "x" * 1000}],  # 缺 tool_call_id
        [tool_message("x", "x" * 1000)],  # 没有任何 assistant 的孤立工具结果
    ]
    for messages in cases:
        malformed = cast("list[ChatCompletionMessageParam]", messages)
        assert project_messages(malformed, POLICY) == messages


def test_budget_unsatisfiable_keeps_protected() -> None:
    """不可删集合自身超预算：存活不变量优先，预算未达成不算失败（contracts §4 条 5）。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("s" * 2000),
        user_message("u" * 2000),
        {"role": "assistant", "content": "a" * 100},
    ]
    tight = ContextPolicy(char_budget=100, tool_result_budget=10)
    assert project_messages(messages, tight) == messages


def test_replacement_only_when_actually_smaller() -> None:
    """毛边 6a：预算触发但占位更大时保留原样——替换永不使上下文变胖。"""
    small = json.dumps({"rows": [{"id": "pay_bg_001_0003", "note": "y" * 40}]}, ensure_ascii=False)
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
        assistant_call("c0"),
        tool_message("c0", small),
        {"role": "assistant", "content": "结论。"},
    ]
    tiny = ContextPolicy(char_budget=5000, tool_result_budget=50)
    result = as_objects(project_messages(messages, tiny))
    assert result[3]["content"] == small


def test_orphan_tool_follows_dropped_assistant() -> None:
    """毛边 6b：属主 assistant 被删时，非相邻的 tool 一并删除，不留配对悬空。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("开始排查"),
        assistant_call("c0"),
        user_message("补充一句"),
        tool_message("c0", big_result()),
        {"role": "assistant", "content": "结论。"},
    ]
    tight = ContextPolicy(char_budget=600, tool_result_budget=10000)
    result = as_objects(project_messages(messages, tight))
    surviving = {str(m.get("tool_call_id")) for m in result if m.get("role") == "tool"}
    assert surviving == set()
    assert result[0] == messages[0] and result[1] == messages[1] and result[-1] == messages[-1]


def test_long_session_volume_is_bounded() -> None:
    """长会话（T017）：投影后体积受控、输入原样、存活集合与占位编号可核查。"""
    messages = long_conversation(20)
    original = copy.deepcopy(messages)
    result = as_objects(project_messages(messages, POLICY))
    total = sum(len(json.dumps(message, ensure_ascii=False)) for message in result)
    assert total <= POLICY.char_budget
    assert messages == original  # 检查点原生消息未压缩
    assert result[0] == messages[0] and result[1] == messages[1] and result[-1] == messages[-1]
    placeholders = [
        message
        for message in result
        if isinstance(content := message.get("content"), str) and '"placeholder"' in content
    ]
    assert placeholders  # 有替换发生
    for placeholder in placeholders:
        body = require_object(parse_json(str(placeholder.get("content"))))
        # 被压缩证据的记录编号全部保留——跨轮引用可核查，disputed 不误拦。
        assert body["record_ids"] == ["pay_bg_001_0003"]
