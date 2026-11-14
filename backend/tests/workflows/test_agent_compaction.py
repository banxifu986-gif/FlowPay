"""压缩编排的 AgentTurn 直测（T005）：触发、计费口径、跨轮兼容、失败跳过。

manual-loop 工作流级测试需要数据源策略（容器基线的既有失败原因），这里直接
构造 AgentTurn——空工具箱即可跑 think()，绕开数据源依赖；工作流端到端由真机
冒烟（scripts/smoke_003_context.py）覆盖。失败路径下 prepare 仍会记录摘要请求
（请求先落库后调用），断言按此口径展开。
"""

import copy
import json
from pathlib import Path
from typing import cast

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from flowpay.core.agent.budget import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.agent.turn import AgentTurn
from flowpay.core.chat.compaction import (
    compact_messages,
    is_summary_message,
    select_compaction_range,
)
from flowpay.core.chat.projection import ContextPolicy, project_messages
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, parse_json, require_object

from .fixtures import Model, completion

USAGE: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
TRIGGER = 5000
POLICY = ContextPolicy(
    char_budget=100000,
    tool_result_budget=100000,
    compaction_trigger_chars=TRIGGER,
    summary_char_budget=4000,
)
# 投影开启但压缩触发线为 0：投影照常工作，压缩不介入。
PROJECTION_ONLY = ContextPolicy(char_budget=100000, tool_result_budget=100000)


def system_message(content: str) -> ChatCompletionSystemMessageParam:
    return {"role": "system", "content": content}


def user_message(content: str) -> ChatCompletionUserMessageParam:
    return {"role": "user", "content": content}


def assistant_call(identifier: str) -> ChatCompletionMessageParam:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {"name": "query_sql", "arguments": "{}"},
            }
        ],
    }


def tool_message(identifier: str, content: str) -> ChatCompletionToolMessageParam:
    return {"role": "tool", "tool_call_id": identifier, "content": content}


def big_result(record_id: str) -> str:
    return json.dumps(
        {"rows": [{"id": record_id, "note": "x" * 150} for _ in range(4)]},
        ensure_ascii=False,
    )


def history(rounds: int) -> list[ChatCompletionMessageParam]:
    """跨轮检查点里的历史消息：system + user + N 轮大结果 + 收尾 user。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
    ]
    for index in range(rounds):
        messages.append(assistant_call(f"c{index}"))
        messages.append(tool_message(f"c{index}", big_result(f"pay_bg_001_{index:04d}")))
    messages.append(user_message("结合上面的结果继续"))
    return messages


def checkpoint_of(messages: list[ChatCompletionMessageParam]) -> JsonObject:
    return {"messages": copy.deepcopy(messages), "evidence": []}


def expected_input(
    messages: list[ChatCompletionMessageParam], question: str
) -> list[ChatCompletionMessageParam]:
    """start() 恢复检查点后追加本轮 user 消息的实际输入。"""
    return [*messages, user_message(question)]


def build_turn(
    tmp_path: Path,
    model: Model,
    policy: ContextPolicy | None,
    saved_state: JsonObject | None = None,
    question: str = "接着查 pay_bg_001_0003 的状态",
) -> AgentTurn:
    """直连 AgentTurn：真实事件库 + 受控模型，空工具箱跑 think。"""
    store = EventStore(tmp_path / "events.sqlite")
    runtime = AgentRuntime(Tracer(store, "session-1", "run-1"))
    turn = TurnInput(question, [], saved_state or {})
    return AgentTurn.start(
        turn,
        runtime,
        "你是排查助手。",
        model,
        Budget(10, 8),
        remember=True,
        context_policy=policy,
    )


def as_objects(messages: object) -> list[JsonObject]:
    return cast("list[JsonObject]", messages)


def summaries_in(messages: object) -> list[JsonObject]:
    return [
        m for m in as_objects(messages) if is_summary_message(m)
    ]


async def test_without_compaction_requests_are_untouched(tmp_path: Path) -> None:
    """触发线为 0：压缩不介入，投影单独工作——请求与纯投影输出逐字一致。"""
    messages = history(8)
    model = Model()
    model.responses = [completion("直接回答。", usage=USAGE)]
    agent = build_turn(tmp_path, model, PROJECTION_ONLY, checkpoint_of(messages))
    await agent.think()
    request = as_objects(model.requests[0]["messages"])
    expected = expected_input(messages, "接着查 pay_bg_001_0003 的状态")
    assert request == as_objects(project_messages(expected, PROJECTION_ONLY))
    assert summaries_in(request) == []


async def test_compaction_runs_before_first_request(tmp_path: Path) -> None:
    """开启且超线：摘要请求先发，正式请求含信封、切点前历史消失、保留集原样。"""
    messages = history(8)
    model = Model()
    model.responses = [completion("历史：已排除……", usage=USAGE), completion("结论。", usage=USAGE)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(messages))
    await agent.think()
    assert len(model.requests) == 2
    # 第一次调用是摘要请求：五段模板与序列化对话在里面，但没有信封。
    summary_request = json.dumps(model.requests[0]["messages"], ensure_ascii=False)
    assert "### 已确认事实" in summary_request
    assert summaries_in(model.requests[0]["messages"]) == []
    # 正式请求：恰好一条信封；被压缩轮次的编号消失，保留轮次的编号还在。
    request = as_objects(model.requests[1]["messages"])
    assert len(summaries_in(request)) == 1
    body = require_object(parse_json(str(summaries_in(request)[0]["content"])))
    assert body["rounds"] == 6  # 8 组保留最近 2 组
    # 被压缩轮次的 tool 消息消失（编号只留在信封 record_ids 里），保留轮次原样。
    surviving_ids = {str(m.get("tool_call_id")) for m in request if m.get("role") == "tool"}
    assert surviving_ids == {"c6", "c7"}
    assert "pay_bg_001_0006" in json.dumps(request, ensure_ascii=False)
    # 编号 0000 只存在于摘要信封的 record_ids（0000-0005），不在消息原文里。
    assert request[0] == messages[0]
    assert request[-1] == user_message("接着查 pay_bg_001_0003 的状态")
    assert request[-2] == messages[-1]  # 保留的收尾 user（切点后）


async def test_compaction_call_is_free_but_charged(tmp_path: Path) -> None:
    """摘要调用不占模型调用预算，但 usage 诚实入账（R-7）。"""
    model = Model()
    model.responses = [completion("摘要。", usage=USAGE), completion("结论。", usage=USAGE)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    await agent.think()
    assert agent.budget.spent_model_calls == 1  # 只有一次正式 think
    assert len(agent.usage_entries) == 2  # 摘要 + think 都计费
    checkpoint = agent.checkpoint()
    assert checkpoint["model_calls"] == 1  # 检查点记账沿用预算口径


async def test_compacted_checkpoint_survives_restore(tmp_path: Path) -> None:
    """压缩后检查点经 restore 起下一轮：seen 从信封结构字段重建，disputed 放行。"""
    messages = history(8)
    groups = select_compaction_range(as_objects(messages), TRIGGER, 2)
    assert groups is not None
    compacted, _, _ = compact_messages(as_objects(messages), groups, "进度摘要", 4000)
    model = Model()
    model.responses = [completion("pay_bg_001_0003 已恢复。", usage=USAGE)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(compacted))
    # 历史轮工具返回过的编号经信封结构化字段进入 seen——防线材料来源扩展（FR-5）。
    assert "pay_bg_001_0000" in agent.seen
    reply = await agent.think()
    assert not await agent.disputed(reply)  # 引用压缩掉的编号不触发编造改写
    assert len(summaries_in(agent.checkpoint()["messages"])) == 1


async def test_compaction_failure_skips_silently(tmp_path: Path) -> None:
    """摘要调用失败：压缩前消息流不动、正式请求无信封、本轮照常作答。

    失败的摘要请求仍会经 prepare 记录（请求先落库），故 requests 有两条；
    think 成功后本轮回答照常追加进消息流，比对以压缩前的输入为准。
    """
    messages = history(8)
    question = "接着查 pay_bg_001_0003 的状态"
    for failure in [RuntimeError("offline"), completion(content=None)]:
        model = Model()
        model.responses = [failure, completion("结论。", usage=USAGE)]
        agent = build_turn(tmp_path, model, POLICY, checkpoint_of(messages), question=question)
        reply = await agent.think()
        assert len(model.requests) == 2
        assert "### 已确认事实" in json.dumps(model.requests[0]["messages"], ensure_ascii=False)
        assert summaries_in(model.requests[1]["messages"]) == []  # 正式请求无信封
        assert agent.messages.messages[: -1] == expected_input(messages, question)
        assert agent.messages.messages[-1]["role"] == "assistant"  # 本轮回答正常追加
        assert reply.text == "结论。"
