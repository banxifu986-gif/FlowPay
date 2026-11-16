"""reactive compact：API 超限 → 强制压缩 → 重试一次的各分支直测（specs/004 US5）。

与 test_agent_compaction 同口径：AgentTurn 直测绕开数据源依赖，工作流端到端
由真机冒烟覆盖。触发线设 0 让主动压缩不介入，隔离 reactive 路径。
"""

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from flowpay.core.agent.budget import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.agent.turn import AgentTurn
from flowpay.core.chat.compaction import is_summary_message
from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, parse_json, require_object
from flowpay.core.llm.chat.http_error import ModelHTTPError

from .fixtures import Model, completion

USAGE: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
# 触发线 0：主动压缩永不介入，压缩只可能来自 reactive 补救路径。
POLICY = ContextPolicy(char_budget=100000, tool_result_budget=100000, compaction_trigger_chars=0)
OVER_LIMIT = "This model's maximum context length is 4096 tokens, please reduce the length."
OTHER_400 = "invalid api key format"


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
        {"rows": [{"id": record_id, "note": "x" * 150} for _ in range(4)]}, ensure_ascii=False
    )


def history(rounds: int) -> list[ChatCompletionMessageParam]:
    """检查点历史：system + user + N 轮大结果 + 收尾 user。"""
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


def build_turn(
    tmp_path: Path,
    model: Model,
    policy: ContextPolicy | None,
    saved_state: JsonObject | None = None,
    *,
    remember: bool = True,
    model_calls: int = 10,
) -> AgentTurn:
    """直连 AgentTurn：真实事件库 + 受控模型，空工具箱跑 think。"""
    store = EventStore(tmp_path / "events.sqlite")
    runtime = AgentRuntime(Tracer(store, "session-1", "run-1"))
    turn = TurnInput("接着查 pay_bg_001_0003 的状态", [], saved_state or {})
    return AgentTurn.start(
        turn,
        runtime,
        "你是排查助手。",
        model,
        Budget(model_calls, 8),
        remember=remember,
        context_policy=policy,
    )


def as_objects(messages: object) -> list[JsonObject]:
    return cast("list[JsonObject]", messages)


def summaries_in(messages: object) -> list[JsonObject]:
    return [m for m in as_objects(messages) if is_summary_message(m)]


def events_of(tmp_path: Path) -> list[tuple[str, JsonObject]]:
    store = EventStore(tmp_path / "events.sqlite")
    return [(row["type"], row["payload"]) for row in store.read("session-1")]


async def test_over_limit_triggers_compaction_and_retry(tmp_path: Path) -> None:
    """超限 400：摘要请求 → 正式重试含信封，事件 trigger=over_limit。"""
    model = Model()
    model.responses = [
        ModelHTTPError(400, OVER_LIMIT),
        completion("历史：已排除……", usage=USAGE),
        completion("结论。", usage=USAGE),
    ]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    reply = await agent.think()
    assert reply.text == "结论。"
    assert len(model.requests) == 3
    retried = as_objects(model.requests[2]["messages"])
    assert len(summaries_in(retried)) == 1
    body = require_object(parse_json(str(summaries_in(retried)[0]["content"])))
    assert body["rounds"] == 6
    surviving = {str(m.get("tool_call_id")) for m in retried if m.get("role") == "tool"}
    assert surviving == {"c6", "c7"}
    reactive = [
        payload for kind, payload in events_of(tmp_path) if kind == "context.reactive_compacted"
    ]
    assert len(reactive) == 1
    payload = require_object(reactive[0])
    assert payload["trigger"] == "over_limit"
    assert payload["rounds"] == 6
    assert cast("int", payload["after_chars"]) < cast("int", payload["before_chars"])


async def test_retry_failure_propagates(tmp_path: Path) -> None:
    """重试仍超限：上抛重试自身的异常，不再第二次补救。"""
    model = Model()
    model.responses = [
        ModelHTTPError(400, OVER_LIMIT),
        completion("历史：已排除……", usage=USAGE),
        ModelHTTPError(400, OVER_LIMIT),
    ]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    with pytest.raises(ModelHTTPError):
        await agent.think()
    assert len(model.requests) == 3
    reactive = [
        payload for kind, payload in events_of(tmp_path) if kind == "context.reactive_compacted"
    ]
    assert len(reactive) == 1


async def test_summary_failure_skips_remedy_and_raises_original(tmp_path: Path) -> None:
    """压缩调用失败：跳过补救，原 ModelHTTPError 上抛（003 约定不破）。"""
    model = Model()
    model.responses = [ModelHTTPError(400, OVER_LIMIT), RuntimeError("summary offline")]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    with pytest.raises(ModelHTTPError):
        await agent.think()
    failed = [
        payload for kind, payload in events_of(tmp_path) if kind == "context.compaction_failed"
    ]
    assert require_object(failed[0])["reason"] == "RuntimeError"
    assert not any(kind == "context.reactive_compacted" for kind, _ in events_of(tmp_path))


async def test_other_400_is_not_a_remedy_trigger(tmp_path: Path) -> None:
    """非超限形态的 400（如鉴权失败）：不补救，异常直接上抛。"""
    model = Model()
    model.responses = [ModelHTTPError(400, OTHER_400)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    with pytest.raises(ModelHTTPError):
        await agent.think()
    assert len(model.requests) == 1
    assert not any(kind.startswith("context.") for kind, _ in events_of(tmp_path))


async def test_no_policy_or_worker_never_remedies(tmp_path: Path) -> None:
    """无策略或 remember=False：超限异常原样上抛，零压缩请求。"""
    for policy, remember in ((None, True), (POLICY, False)):
        model = Model()
        model.responses = [ModelHTTPError(400, OVER_LIMIT)]
        agent = build_turn(tmp_path, model, policy, checkpoint_of(history(8)), remember=remember)
        with pytest.raises(ModelHTTPError):
            await agent.think()
        assert len(model.requests) == 1


async def test_already_minimal_raises_original(tmp_path: Path) -> None:
    """只剩保留轮、压不动：不重试，原异常上抛。"""
    model = Model()
    model.responses = [ModelHTTPError(400, OVER_LIMIT)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(2)))
    with pytest.raises(ModelHTTPError):
        await agent.think()
    assert len(model.requests) == 1
    assert not any(kind == "context.reactive_compacted" for kind, _ in events_of(tmp_path))


async def test_last_budget_call_over_limit_raises_original(tmp_path: Path) -> None:
    """超限发生在最后一次预算调用上：无重试额度，不压缩、上抛原异常。

    大上下文最容易在收尾拍（forced=True）超限；若此时仍执行补救，
    spend_model_call 会以预算错误掩盖真实的超限原因。
    """
    model = Model()
    model.responses = [ModelHTTPError(400, OVER_LIMIT)]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)), model_calls=1)
    with pytest.raises(ModelHTTPError):
        await agent.think()
    assert len(model.requests) == 1
    assert not any(
        kind in ("context.reactive_compacted", "context.compaction_failed")
        for kind, _ in events_of(tmp_path)
    )
    # 消息流未被强制压缩改写（无信封）。
    assert summaries_in(as_objects(model.requests[0]["messages"])) == []


async def test_reactive_retry_spends_budget_and_usage(tmp_path: Path) -> None:
    """补救重试计模型调用预算；摘要与重试的 usage 都入账（002 先例）。"""
    model = Model()
    model.responses = [
        ModelHTTPError(400, OVER_LIMIT),
        completion("历史：已排除……", usage=USAGE),
        completion("结论。", usage=USAGE),
    ]
    agent = build_turn(tmp_path, model, POLICY, checkpoint_of(history(8)))
    await agent.think()
    # 正式调用 1 + 摘要 0（不占预算）+ 重试 1 = 2 次预算消耗。
    assert agent.budget.spent_model_calls == 2
    assert len(agent.usage_entries) == 2
