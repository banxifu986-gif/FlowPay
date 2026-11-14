"""think() 的投影开关对照（T024）：关闭时请求与原生消息逐条一致，开启时占位替换。"""

import json
from typing import cast

from flowpay.core.agent import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.agent.turn import AgentTurn
from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime

from .fixtures import Model, completion, search_call
from .test_seen_restore import NoopTracer

BIG_RESULT = {"rows": [{"id": "pay_bg_001_0003", "note": "x" * 200} for _ in range(4)]}


def agent_with_tool_exchange(model: Model, policy: ContextPolicy | None) -> AgentTurn:
    """构造一轮：大工具结果交换之后再有一轮 assistant 文本，使其不属最后一段交换。

    最后一段交换的存活保护优先于占位替换（contracts §4），因此对照场景必须让
    大工具结果落在非存活位置，"开启 vs 关闭"才会表现出差异。
    """
    runtime = AgentRuntime(cast(Tracer, NoopTracer()), collection_id="fixture")
    agent = AgentTurn.start(
        TurnInput("排查这笔支付", [], {}),
        runtime,
        "系统提示词",
        model,
        Budget(4, 4),
        context_policy=policy,
    )
    assistant = completion(calls=[search_call("c1", "HTTP 410")]).choices[0].message
    agent.messages.add_assistant_message(assistant)
    agent.messages.add_tool_result("c1", BIG_RESULT)
    agent.messages.add_assistant_message(completion("先记录一下这些结果。").choices[0].message)
    model.responses = [completion("直接回答。")]
    return agent


async def test_projection_off_sends_raw_messages() -> None:
    """缺省（无策略）：请求与原生消息逐条一致，工具结果原样全量发送（SC-1）。"""
    model = Model()
    agent = agent_with_tool_exchange(model, None)
    await agent.think()
    request_messages = model.requests[0]["messages"]
    # think() 在请求之后才回填本次回答，比较请求时刻的前五条。
    assert request_messages == agent.messages.messages[:5]
    assert request_messages[3]["content"] == json.dumps(BIG_RESULT, ensure_ascii=False)


async def test_projection_on_replaces_oversized_tool_result() -> None:
    """开启策略：同一场景的请求中非存活工具结果变为占位摘要，其余消息原样。"""
    model = Model()
    agent = agent_with_tool_exchange(
        # 整体预算放宽到替换后的总体积之上，只让单条工具预算触发占位路径。
        model, ContextPolicy(char_budget=2000, tool_result_budget=100)
    )
    await agent.think()
    request_messages = model.requests[0]["messages"]
    assert request_messages[4] == agent.messages.messages[4]  # 最后一段 assistant 原样
    body = json.loads(str(request_messages[3]["content"]))
    assert body["placeholder"] is True
    assert body["target"] == "search_documents"
    assert body["rows"] == 4
    assert body["record_ids"] == ["pay_bg_001_0003"]
