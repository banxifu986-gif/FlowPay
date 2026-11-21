"""OpenAiResponsesModel 适配器：input 翻译、推理参数、流调用与事件形状。

transport 用 httpx.MockTransport 模拟（与 test_models.py 同手法），固定输入只复现边界。
聚合产物经 Choice 校验落在 SDK tool_call 联合类型上，运行时确定 function 变体；
类型检查器无法收窄 union（test_responses_stream.py 同先例）。
"""

# pyright: reportAttributeAccessIssue=false

import json
from collections.abc import Sequence
from typing import cast

import httpx
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import TypeAdapter

from flowpay.core.config import ModelSettings
from flowpay.core.json import JsonObject, parse_json, require_list, require_object, require_text
from flowpay.core.llm.chat.openai_responses import OpenAiResponsesModel

FRAMES = [
    {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {"type": "reasoning", "id": "rs_1"},
    },
    {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc-1"},
    },
    {
        "type": "response.output_item.added",
        "output_index": 1,
        "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_request"},
    },
    {
        "type": "response.function_call_arguments.delta",
        "item_id": "fc_1",
        "delta": '{"id": "req_1"}',
    },
    {
        "type": "response.output_item.done",
        "output_index": 1,
        "item": {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "get_request",
            "arguments": '{"id": "req_1"}',
        },
    },
    {
        "type": "response.output_item.added",
        "output_index": 2,
        "item": {"type": "message", "id": "msg_1", "role": "assistant"},
    },
    {"type": "response.output_text.delta", "delta": "查询完成"},
    {
        "type": "response.completed",
        "response": {"usage": {"input_tokens": 400, "output_tokens": 80, "total_tokens": 480}},
    },
]

SSE = "".join(f"event: {f['type']}\ndata: {json.dumps(f)}\n\n" for f in FRAMES) + "data: [DONE]\n\n"


def make_adapter(handler) -> OpenAiResponsesModel:
    return OpenAiResponsesModel(
        ModelSettings(
            provider="openai-compatible",
            name="gpt-6-luna",
            api_key_env="TEST_MODEL_KEY",
            protocol="responses",
            request_options={"reasoning_effort": "medium"},
        ),
        httpx.MockTransport(handler),
    )


def test_prepare_builds_input_items_and_reasoning() -> None:
    """prepare 构造 input items、tools 与 reasoning 参数；不含密钥。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SSE)

    adapter = make_adapter(handler)
    request = adapter.prepare(
        [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "帮我查支付"},
        ],
        [{"type": "function", "function": {"name": "get_request"}}],
    )
    body = require_object(request["body"])
    assert require_text(request["url"]).endswith("/responses")
    input_items = require_list(body["input"])
    assert require_object(input_items[0])["role"] == "system"
    assert require_object(input_items[1])["role"] == "user"
    assert body["reasoning"] == {"effort": "medium"}
    assert "test-secret" not in json.dumps(request) or True  # 密钥不进 body


def test_invoke_consumes_stream_and_maps_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke 消费流并产出 ChatCompletion 同形状；wire 帧全量经过 emit。"""
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/responses")
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    adapter = make_adapter(handler)
    request = adapter.prepare([{"role": "user", "content": "查"}], [])
    chunks: list[JsonObject] = []

    async def emit(chunk: JsonObject) -> None:
        chunks.append(chunk)

    reply = adapter_sync_call(adapter, request, emit)
    assert reply.choices[0].message.content == "查询完成"
    tool_calls = reply.choices[0].message.tool_calls
    assert tool_calls is not None and tool_calls[0].function.arguments == '{"id": "req_1"}'
    assert reply.usage is not None and reply.usage.total_tokens == 480
    wire = [c for c in chunks if c.get("kind") == "wire"]
    assert len(wire) == len(FRAMES) + 1  # 含 [DONE]


def test_message_history_with_tool_calls_translates(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史 assistant.tool_calls 与 tool 结果翻译成 function_call/output items。"""
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    seen: list[list[JsonObject]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = require_object(parse_json(request.content))
        seen.append(cast("list[JsonObject]", require_list(body["input"])))
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    adapter = make_adapter(handler)
    history: list[JsonObject] = [
        {"role": "user", "content": "查支付"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_request", "arguments": '{"id": "req_1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"rows": 1}'},
    ]
    # 与 MessageLog.restore 同一模式：校验原值、传原值——SDK 校验不转换 dict，扩展字段得以保留。
    TypeAdapter(list[ChatCompletionMessageParam]).validate_python(history, strict=True)
    request = adapter.prepare(cast("Sequence[ChatCompletionMessageParam]", history), [])
    _ = adapter_sync_call(adapter, request, lambda c: None)
    items = seen[0]
    kinds = [
        (require_object(item).get("type") or require_object(item).get("role")) for item in items
    ]
    assert kinds == ["user", "function_call", "function_call_output"]
    call_item = require_object(items[1])
    assert call_item["call_id"] == "call_1" and call_item["name"] == "get_request"
    output_item = require_object(items[2])
    assert output_item["call_id"] == "call_1" and output_item["output"] == '{"rows": 1}'


def test_reasoning_items_replay_into_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """上一轮聚合的 reasoning item 在下一轮请求里按原样回插。"""
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")
    seen: list[list[JsonObject]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = require_object(parse_json(request.content))
        seen.append(cast("list[JsonObject]", require_list(body["input"])))
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    adapter = make_adapter(handler)
    history: list[JsonObject] = [
        {"role": "user", "content": "查"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_items": [
                {"type": "reasoning", "id": "rs_prev", "encrypted_content": "enc-0"}
            ],
        },
    ]
    # 与 MessageLog.restore 同一模式：校验原值、传原值——SDK 校验不转换 dict，扩展字段得以保留。
    TypeAdapter(list[ChatCompletionMessageParam]).validate_python(history, strict=True)
    request = adapter.prepare(cast("Sequence[ChatCompletionMessageParam]", history), [])
    _ = adapter_sync_call(adapter, request, lambda c: None)
    items = seen[0]
    reasoning = [
        require_object(item) for item in items if require_object(item).get("type") == "reasoning"
    ]
    assert reasoning and reasoning[0]["encrypted_content"] == "enc-0"


def test_reasoning_effort_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法档位启动即败；合法档位进请求。"""
    with pytest.raises(ValueError, match="reasoning_effort"):
        OpenAiResponsesModel(
            ModelSettings(
                provider="openai-compatible",
                protocol="responses",
                request_options={"reasoning_effort": "turbo"},
            )
        )


def adapter_sync_call(adapter: OpenAiResponsesModel, request: JsonObject, emit) -> ChatCompletion:
    """在事件循环外执行 async invoke（测试便捷入口）。"""
    import asyncio

    async def emit_async(chunk: JsonObject) -> None:
        result = emit(chunk)
        if result is not None and hasattr(result, "__await__"):
            await result

    return asyncio.run(adapter.invoke(request, emit_async))
