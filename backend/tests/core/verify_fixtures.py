# 校验器测试共用的模型替身与事件记录；固定输入复现边界，不作为业务答案来源。
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam, ChatCompletionToolParam

from flowpay.core.json import JsonObject
from flowpay.core.llm.chat import ChunkSink


def completion(content: str | None) -> ChatCompletion:
    """构造单选、stop 结束的最小模型响应。"""
    return ChatCompletion.model_validate(
        {
            "id": "fixture",
            "object": "chat.completion",
            "created": 0,
            "model": "fixture",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
            ],
        }
    )


def json_payload(payload: object) -> str:
    """把结构化断言/判定载荷变成模型正文（不包栅栏）。"""
    return json.dumps(payload, ensure_ascii=False)


class ScriptedModel:
    """逐次弹出预设响应或异常的模型替身；请求完整保存供断言。"""

    name = "scripted"
    capabilities: dict[str, bool] = {"tools": True, "streaming": False}

    def __init__(self, *responses: ChatCompletion | Exception) -> None:
        self.responses: list[ChatCompletion | Exception] = list(responses)
        self.requests: list[JsonObject] = []

    def prepare(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolParam],
    ) -> JsonObject:
        self.requests.append({"messages": copy_messages(messages), "tools": list(tools)})
        return self.requests[-1]

    async def invoke(self, request: JsonObject, emit: ChunkSink) -> ChatCompletion:
        _ = request
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        content = result.choices[0].message.content
        if content:
            await emit({"kind": "content_delta", "text": content})
        return result


class RecordingSink:
    """按序记录事件的事件出口；供 ChatClient 装配与事件断言。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, JsonObject]] = []

    async def emit(self, kind: str, payload: JsonObject, call_id: str | None = None) -> None:
        _ = call_id
        self.events.append((kind, payload))


def copy_messages(messages: Sequence[ChatCompletionMessageParam]) -> list[ChatCompletionMessageParam]:
    """深拷贝消息序列，避免调用方追加内容污染已记录请求。"""
    return cast(
        "list[ChatCompletionMessageParam]",
        json.loads(json.dumps(list(messages), ensure_ascii=False)),
    )
