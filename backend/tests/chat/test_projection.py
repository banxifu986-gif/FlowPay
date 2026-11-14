"""投影四不变量与长会话场景（T014/T017）。"""

import copy
import json

from flowpay.core.chat.projection import ContextPolicy, project_messages

POLICY = ContextPolicy(char_budget=3000, tool_result_budget=400)
# 收紧的整体预算，驱动"占位替换之后仍超限"的整组删除路径。
TIGHT = ContextPolicy(char_budget=1000, tool_result_budget=400)


def system_message(content: str) -> dict[str, str]:
    return {"role": "system", "content": content}


def user_message(content: str) -> dict[str, str]:
    return {"role": "user", "content": content}


def assistant_call(identifier: str, name: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {"name": "query_sql", "arguments": json.dumps({"account": "acct_bg_001"})},
            }
        ],
    }


def tool_message(identifier: str, content: str) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": identifier, "content": content}


def big_result(rows: int = 4, record_id: str = "pay_bg_001_0003") -> str:
    """大工具结果：rows 列表撑起体积，同时携带可核查的记录编号。"""
    return json.dumps(
        {"rows": [{"id": record_id, "note": "x" * 150} for _ in range(rows)]},
        ensure_ascii=False,
    )


def long_conversation(rounds: int) -> list[dict[str, object]]:
    """system + user + N 轮（assistant+大工具结果）+ 收尾 user。"""
    messages: list[dict[str, object]] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
    ]
    for index in range(rounds):
        messages.append(assistant_call(f"c{index}", "query_sql"))
        messages.append(tool_message(f"c{index}", big_result()))
    messages.append(user_message("结合上面的结果回答"))
    return messages


def test_placeholder_replaces_oversized_tool_results() -> None:
    """超限工具结果被占位摘要替换：role/tool_call_id 原样，编号与行数可核查。"""
    messages = long_conversation(2)
    result = project_messages(messages, POLICY)
    placeholder = result[3]
    assert placeholder["role"] == "tool"
    assert placeholder["tool_call_id"] == "c0"
    body = json.loads(str(placeholder["content"]))
    assert body["placeholder"] is True
    assert body["target"] == "query_sql"
    assert body["rows"] == 4
    assert body["record_ids"] == ["pay_bg_001_0003"]
    assert body["original_chars"] > POLICY.tool_result_budget
    # 输入列表不被修改——检查点原生消息未压缩（FR-8）。
    assert messages[3]["content"] == big_result()


def test_pairing_is_never_broken() -> None:
    """整组删除与占位替换之后，assistant.tool_calls 与 tool 消息仍逐对完整。"""
    result = project_messages(long_conversation(6), TIGHT)
    for index, message in enumerate(result):
        if message.get("role") != "assistant":
            continue
        calls = [str(call["id"]) for call in message.get("tool_calls") or []]
        following: list[str] = []
        for later in result[index + 1 :]:
            if later.get("role") != "tool":
                break
            following.append(str(later["tool_call_id"]))
        assert calls == following


def test_protected_messages_survive_deletion() -> None:
    """首条 system、任何 user、最后一段 assistant 交换在删除后原样存活。"""
    messages = long_conversation(6)
    result = project_messages(messages, TIGHT)
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
        assert project_messages(messages, POLICY) == messages  # type: ignore[arg-type]


def test_budget_unsatisfiable_keeps_protected() -> None:
    """不可删集合自身超预算：存活不变量优先，预算未达成不算失败（contracts §4 条 5）。"""
    messages: list[dict[str, str]] = [
        system_message("s" * 2000),
        user_message("u" * 2000),
        {"role": "assistant", "content": "a" * 100},
    ]
    tight = ContextPolicy(char_budget=100, tool_result_budget=10)
    assert project_messages(messages, tight) == messages  # type: ignore[arg-type]


def test_long_session_volume_is_bounded() -> None:
    """长会话（T017）：投影后体积受控、输入原样、存活集合与占位编号可核查。"""
    messages = long_conversation(20)
    original = copy.deepcopy(messages)
    result = project_messages(messages, POLICY)
    total = sum(len(json.dumps(message, ensure_ascii=False)) for message in result)
    assert total <= POLICY.char_budget
    assert messages == original  # 检查点原生消息未压缩
    assert result[0] == messages[0] and result[1] == messages[1] and result[-1] == messages[-1]
    placeholders = [
        message
        for message in result
        if isinstance(message.get("content"), str) and '"placeholder"' in message["content"]
    ]
    assert placeholders  # 有替换发生
    for placeholder in placeholders:
        body = json.loads(str(placeholder["content"]))
        # 被压缩证据的记录编号全部保留——跨轮引用可核查，disputed 不误拦。
        assert body["record_ids"] == ["pay_bg_001_0003"]
