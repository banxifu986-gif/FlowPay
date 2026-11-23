# 校验器测试共用的模型替身与事件记录；固定输入复现边界，不作为业务答案来源。
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam, ChatCompletionToolParam

from flowpay.core.event_log.contracts import EventSink
from flowpay.core.json import JsonObject
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.chat.client import ChatClient


def passage(identifier: str, text: str, score: float) -> Passage:
    """构造一个最小片段；id 用真实形态 doc:KB-XXX#cNNN。"""
    return Passage(
        id=identifier,
        title=f"{identifier} 片段",
        text=text,
        score=score,
        source=PassageSource(
            collection_id="fixture",
            doc_id=identifier.split("#")[0].removeprefix("doc:"),
            chunk_no=1,
            path="knowledge/fixture.md",
            sha256="fixture",
            locator={"start_char": 0},
            metadata={},
        ),
    )


# 本轮片段集：绑定主证据 + 反证候选，供装配/校验/处置用例复用。
ROUNDS = (
    passage("doc:KB-TS-012#c001", "企业版工单通常 5 个工作日响应。", 0.9),
    passage("doc:KB-TS-012#c002", "免费版不提供专属工单通道。", 0.7),
    passage("doc:KB-BILL-003#c001", "退款在 3 个工作日内原路退回。", 0.5),
)


def completion(content: str | None) -> ChatCompletion:
    """构造单选、stop 结束的最小模型响应。"""
    return ChatCompletion.model_validate(
        {
            "id": "fixture",
            "object": "chat.completion",
            "created": 0,
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        }
    )


def json_payload(payload: object) -> str:
    """把结构化断言/判定载荷变成模型正文（不包栅栏）。"""
    return json.dumps(payload, ensure_ascii=False)


class ScriptedModel:
    """逐次弹出预设响应或异常的模型替身；请求完整保存供断言。"""

    name: str = "scripted"
    capabilities: dict[str, bool] = {"tools": True, "streaming": False}

    def __init__(self, *responses: ChatCompletion | BaseException) -> None:
        self.responses: list[ChatCompletion | BaseException] = list(responses)
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
        if isinstance(result, BaseException):  # 取消（BaseException）也能注入
            raise result
        content = result.choices[0].message.content
        if content:
            await emit({"kind": "content_delta", "text": content})
        return result


class RecordingSink:
    """按序记录事件的事件出口；供 ChatClient 装配与事件断言。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, JsonObject]] = []

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> None:
        """按事件出口协议记录一次调用，忽略调用关联编号。"""
        del call_id, parent_call_id
        self.events.append((kind, payload))


def chat_client(model: ScriptedModel, sink: RecordingSink) -> ChatClient:
    """按 EventSink 协议装配校验用客户端（协议返回 Event 的差异以既有先例绕过）。"""
    return ChatClient(model, cast("EventSink", cast(object, sink)))


def copy_messages(
    messages: Sequence[ChatCompletionMessageParam],
) -> list[ChatCompletionMessageParam]:
    """深拷贝消息序列，避免调用方追加内容污染已记录请求。"""
    return cast(
        "list[ChatCompletionMessageParam]",
        json.loads(json.dumps(list(messages), ensure_ascii=False)),
    )
