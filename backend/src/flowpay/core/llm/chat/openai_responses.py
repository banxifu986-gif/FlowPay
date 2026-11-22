"""OpenAI Responses API 适配器：实现 ChatModel Protocol，推理模型经 /responses 调用。

与 OpenAiChatModel 同纪律：prepare 只构造记录对象不发送请求；invoke 消费流并保留
原始 SSE 帧；连接失败一次重试；HTTP 错误转 ModelHTTPError。多轮上下文完整重放
（specs/008）：历史消息翻译成 input items，上一轮的推理项按原样回插。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Sequence
from contextlib import AsyncExitStack
from typing import cast

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam, ChatCompletionToolParam
from openai.types.responses import ResponseInputParam

from flowpay.core.config import ModelSettings
from flowpay.core.json import JsonObject, parse_json, require_list, require_object, require_text
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.chat.http_error import ModelHTTPError
from flowpay.core.llm.chat.openai_chat import ConnectionRetry
from flowpay.core.llm.chat.responses_stream import ResponsesStreamAccumulator

# Responses 端的保留字段：配置不得越权覆盖应用构造的部分。
_RESERVED_RESPONSE_KEYS = frozenset(
    {"model", "input", "tools", "stream", "previous_response_id", "reasoning", "include"}
)

# 推理档位白名单；非法值启动即败，避免运行中才发现。
_REASONING_EFFORTS = frozenset({"low", "medium", "high", "none"})


def _reasoning_of(request_options: JsonObject) -> JsonObject:
    """从 request_options 提取 reasoning 参数并校验档位；没有则不启用推理。

    "none" 是合法档位（无推理形态的基准口径），映射为不发 reasoning 字段——
    Responses 端不推理与 chat 端 reasoning_effort="none" 语义一致。
    """
    effort = request_options.get("reasoning_effort")
    if effort is None:
        return {}
    if effort == "none":
        return {}
    if effort not in _REASONING_EFFORTS:
        raise ValueError(f"Unknown reasoning_effort: {effort}")
    return {"effort": effort}


def _input_items(messages: Sequence[ChatCompletionMessageParam]) -> list[JsonObject]:
    """把 Chat Completions 消息历史翻译成 Responses input items（完整重放）。

    assistant.tool_calls → function_call item + 对应 tool 结果 → function_call_output；
    assistant 消息上的 reasoning_items 扩展字段（上一轮聚合器保存）按原样回插。
    """
    items: list[JsonObject] = []
    for raw_message in messages:
        # SDK 消息参数是 TypedDict 联合（各 role 形状不同），按 dict 收口后逐键读取。
        message = require_object(cast("object", raw_message))
        role = require_text(message["role"])
        if role in ("system", "user"):
            items.append({"role": role, "content": require_text(message["content"])})
            continue
        if role == "assistant":
            # SDK 校验会把 tool_calls 变成 ValidatorIterator 等验证器对象；
            # 推理项是纯扩展字段保持 list。翻译按"可枚举"读取，不假设具体容器。
            reasoning_raw: object = message.get("reasoning_items") or []
            if not isinstance(reasoning_raw, list):
                reasoning_items = list(cast("Iterable[object]", reasoning_raw))
            else:
                reasoning_items = cast("list[object]", reasoning_raw)
            for reasoning in reasoning_items:
                items.append(require_object(reasoning))
            calls_raw: object = message.get("tool_calls") or []
            if not isinstance(calls_raw, list):
                calls = list(cast("Iterable[object]", calls_raw))
            else:
                calls = cast("list[object]", calls_raw)
            content = message.get("content")
            if isinstance(content, str) and content:
                items.append({"role": "assistant", "content": content})
            for raw_call in calls:
                call = require_object(raw_call)
                function = require_object(call["function"])
                items.append(
                    {
                        "type": "function_call",
                        "call_id": require_text(call["id"]),
                        "name": require_text(function["name"]),
                        "arguments": require_text(function["arguments"]),
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": require_text(message["tool_call_id"]),
                    "output": require_text(message["content"]),
                }
            )
            continue
        raise ValueError(f"Unsupported message role for responses protocol: {role}")
    return items


class OpenAiResponsesModel:
    """基于官方 SDK Responses API 的推理模型适配器，保留原始 SSE 数据。"""

    capabilities: dict[str, bool] = {"tools": True, "streaming": True, "reasoning": True}

    def __init__(self, settings: ModelSettings, transport: httpx.AsyncBaseTransport | None = None):
        """保存模型配置和可选测试 transport；SDK 客户端在实际调用时创建并关闭。"""
        self.settings: ModelSettings = settings
        self.name: str = settings.name
        self.transport: httpx.AsyncBaseTransport | None = transport
        # 启动即校验推理档位：配置错误不该等到第一轮调用才暴露。
        self.reasoning: JsonObject = _reasoning_of(settings.request_options)
        for key in _RESERVED_RESPONSE_KEYS & settings.request_options.keys():
            if key != "reasoning_effort":
                raise ValueError(f"request_options cannot override reserved responses field: {key}")

    def prepare(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolParam],
    ) -> JsonObject:
        """按消息历史与工具类型构造实际请求体，返回 URL 与正文供追踪；不访问网络。"""
        options = {
            key: value
            for key, value in self.settings.request_options.items()
            if key != "reasoning_effort"
        }
        body: JsonObject = {
            **options,
            "model": self.name,
            "input": _input_items(messages),
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": require_text(tool["function"].get("name") or ""),
                    "parameters": require_object(tool["function"].get("parameters") or {}),
                    **(
                        {"description": require_text(description)}
                        if (description := tool["function"].get("description"))
                        else {}
                    ),
                }
                for tool in tools
            ]
        if self.reasoning:
            body["reasoning"] = dict(self.reasoning)
        return {
            "provider": "openai-compatible",
            "protocol": "responses",
            "url": self.settings.base_url.rstrip("/") + "/responses",
            "body": body,
        }

    async def invoke(self, request: JsonObject, emit: ChunkSink) -> ChatCompletion:
        """用官方 SDK 发送 prepare 得到的请求，返回 ChatCompletion 同形状聚合。

        流读取纪律与 chat 适配器一致：连接失败一次重试；收到响应头之后的中断
        不能重放；流结束但没有完成事件视为中断。原始 data 帧先落日志再解析。
        """
        key = os.environ.get(self.settings.api_key_env)
        if not key:
            raise ValueError(f"Missing environment variable: {self.settings.api_key_env}")
        accumulator = ResponsesStreamAccumulator()
        async with AsyncOpenAI(
            api_key=key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=self.transport),
        ) as client:
            body = require_object(request["body"])
            try:
                async with AsyncExitStack() as stack:
                    attempt = 1
                    while True:
                        try:
                            # SDK 的 input 参数要求 ResponseInputParam；这里按 dict 直传，
                            # extra_body 不含 input（它已是具名参数），类型收口用 cast。
                            response = await stack.enter_async_context(
                                client.responses.with_streaming_response.create(
                                    model=require_text(body["model"]),
                                    input=cast(
                                        "ResponseInputParam",
                                        require_list(body["input"]),
                                    ),
                                    stream=True,
                                    extra_body={
                                        k: v
                                        for k, v in body.items()
                                        if k not in {"model", "input", "stream"}
                                    },
                                )
                            )
                            break
                        except APIConnectionError as error:
                            cause = error.__cause__
                            if not isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout)):
                                raise
                            if attempt == 2:
                                raise RuntimeError(
                                    "Model connection failed after 2 attempts; "
                                    + "check provider reachability, proxy and TLS settings "
                                    + f"({type(cause).__name__})"
                                ) from None
                            retry = ConnectionRetry(error_type=type(cause).__name__)
                            await emit(to_json_object(retry))
                            await asyncio.sleep(retry.delay_ms / 1000)
                            attempt += 1
                    async for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        await emit({"kind": "wire", "data": data})
                        if data == "[DONE]":
                            break
                        frame = require_object(parse_json(data))
                        content = accumulator.consume(frame)
                        if content:
                            await emit({"kind": "content_delta", "text": content})
            except APIStatusError as error:
                text = (await error.response.aread()).decode(errors="replace")
                await emit({"kind": "http_error", "status": error.status_code, "body": text})
                raise ModelHTTPError(error.status_code, text) from None
        return accumulator.finish()


def to_json_object(value: object) -> JsonObject:
    """延迟导入避免与 core.json 循环依赖；保持与 openai_chat 同一序列化出口。"""
    from flowpay.core.json import to_json_object as _to

    return _to(value)


# 兼容旧导入路径的显式别名（tests 与未来调用方使用）。
__all__ = ["OpenAiResponsesModel"]
