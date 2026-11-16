"""condense_briefings：简报封顶合并的纯函数行为与投影组合不变量（specs/004 US6）。"""

import copy

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionUserMessageParam

from flowpay.core.chat.projection import ContextPolicy, project_messages
from flowpay.workflows.manual_loop import MERGED_BRIEFING_HEADER, condense_briefings

CAP = 3


def system_msg(content: str) -> ChatCompletionMessageParam:
    return {"role": "system", "content": content}


def user_msg(content: str) -> ChatCompletionUserMessageParam:
    return {"role": "user", "content": content}


def briefing_msg(turn: int) -> ChatCompletionMessageParam:
    """构造一条形似简报的 system 消息；turn 越大越新。"""
    return system_msg(f"已知条件（用户此前提供，直接使用，不要再次索要）：\n- 第 {turn} 轮条件")


def stream(briefings: int) -> list[ChatCompletionMessageParam]:
    """构造消息流：system 提示词 + 穿插 user + N 条简报 + 最新 user。"""
    messages: list[ChatCompletionMessageParam] = [system_msg("你是 FlowPay 技术支持助手。")]
    for index in range(briefings):
        if index > 0:
            messages.append(user_msg(f"第 {index} 轮补充说明"))
        messages.append(briefing_msg(index))
    messages.append(user_msg("结合已知条件继续"))
    return messages


def briefings_in(messages: list[ChatCompletionMessageParam]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str):
            if content.startswith("已知条件"):
                texts.append(content)
    return texts


def test_cap_zero_returns_input_unchanged() -> None:
    """cap=0：返回原对象，消息流逐字不变（003 US3 口径）。"""
    messages = stream(6)
    assert condense_briefings(messages, 0) is messages
    assert len(briefings_in(messages)) == 6


def test_over_cap_merges_oldest_in_place() -> None:
    """超限：最旧的 len-cap+1 条合并为一条，占据首条原位置，其余原样。"""
    messages = stream(5)
    result = condense_briefings(messages, CAP)
    texts = briefings_in(result)
    assert len(texts) == CAP
    assert texts[0].startswith(MERGED_BRIEFING_HEADER)
    # 合并条目按时间序拼接：被合并 3 条的正文都在，未合并的不在。
    assert "第 0 轮条件" in texts[0]
    assert "第 1 轮条件" in texts[0]
    assert "第 2 轮条件" in texts[0]
    assert "第 3 轮条件" not in texts[0]
    assert texts[1] == str(briefing_msg(3).get("content"))
    assert texts[2] == str(briefing_msg(4).get("content"))
    assert result[-1] == messages[-1]
    # 首条 system 提示词（前缀不是「已知条件」）不受影响。
    assert result[0] == messages[0]


def test_input_not_mutated() -> None:
    """输入列表不被修改（写路径替换由调用方负责）。"""
    messages = stream(5)
    before = copy.deepcopy(messages)
    condense_briefings(messages, CAP)
    assert messages == before


def test_merged_entry_participates_in_next_merge() -> None:
    """合并条目带「已知条件」前缀：下一次超限时继续参与合并。"""
    messages = stream(5)
    first = condense_briefings(messages, CAP)
    second = condense_briefings(first, CAP)
    assert len(briefings_in(second)) == CAP
    texts = briefings_in(second)
    # 合并条目现在是最旧的，被再次合并：历史正文仍按时间序保留。
    assert texts[0].startswith(MERGED_BRIEFING_HEADER)
    assert "第 0 轮条件" in texts[0]


def test_merged_entry_survives_projection() -> None:
    """组合不变量：合并条目是不可删 system，投影后仍在存活集合内。"""
    policy = ContextPolicy(char_budget=1000, tool_result_budget=1000)
    messages = condense_briefings(stream(5), CAP)
    projected = project_messages(messages, policy)
    assert len(briefings_in(projected)) == CAP


def test_revision_chain_keeps_time_order() -> None:
    """改口链：合并后旧条件正文在前、新条件在后，「改为」语义不乱序。"""
    messages: list[ChatCompletionMessageParam] = [
        system_msg("你是 FlowPay 技术支持助手。"),
        system_msg("已知条件（用户此前提供，直接使用，不要再次索要）：\n- payment_id = pay_old"),
        user_msg("改为 acct_bm_030"),
        system_msg(
            "已知条件（用户此前提供，直接使用，不要再次索要）：\n- account_id = acct_bm_030"
        ),
    ]
    result = condense_briefings(messages, 1)
    texts = briefings_in(result)
    assert len(texts) == 1
    assert texts[0].index("pay_old") < texts[0].index("acct_bm_030")


def test_memory_only_briefing_joins_cap() -> None:
    """记忆-only 简报（无「已知条件」前缀）同样计入封顶合并。"""
    messages: list[ChatCompletionMessageParam] = [
        system_msg("你是 FlowPay 技术支持助手。"),
        system_msg(
            "历史会话排查记忆（供参考；与本轮已知条件或工具返回冲突时，一律以本轮为准）：\n- 旧结论"
        ),
        system_msg(str(briefing_msg(1).get("content"))),
    ]
    result = condense_briefings(messages, 1)
    texts = briefings_in(result)
    assert len(texts) == 1
    assert "旧结论" in texts[0]
    assert "第 1 轮条件" in texts[0]
