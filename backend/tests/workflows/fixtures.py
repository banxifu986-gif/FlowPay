"""阶段回归共用的受控模型与检索；不调用外部服务，也不替代阶段决策。"""

import asyncio
import copy
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from flowpay.core.event_log.contracts import EventSink
from flowpay.core.json import JsonObject, to_json_object
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.chat.stream import ChatStreamAccumulator

ANSWER = "HTTP 410 后禁用端点。\n\n[doc:RULE-1#c001]"


def completion(
    content: str | None = None,
    calls: list[JsonObject] | None = None,
    finish: str | None = None,
    usage: JsonObject | None = None,
) -> ChatCompletion:
    """构造原生模型响应；附加供应商字段用于检查回填时没有丢失。

    usage 按协议形状（prompt/completion/total_tokens）放进响应；缺省 None
    保持既有用例的响应形状，响应里不出现 usage 键。
    """
    return ChatCompletion.model_validate(
        {
            "id": "fixture",
            "object": "chat.completion",
            "created": 0,
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish or ("tool_calls" if calls else "stop"),
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": calls,
                        "reasoning_content": "公开的测试推理字段",
                    },
                }
            ],
            **({"usage": usage} if usage else {}),
        }
    )


def read_call(identifier: str, doc_id: str) -> JsonObject:
    """创建一次 read_document 调用，用于检验模型在两个工具之间的选择。"""
    import json

    return {
        "id": identifier,
        "type": "function",
        "function": {"name": "read_document", "arguments": json.dumps({"doc_id": doc_id})},
    }


def search_call(identifier: str, query: str, limit: int = 10) -> JsonObject:
    """创建实际 SDK function 调用，arguments 仍是协议要求的 JSON 字符串。"""
    import json

    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "search_documents",
            "arguments": json.dumps({"query": query, "limit": limit}),
        },
    }


def intent_call(identifier: str, intent: str) -> JsonObject:
    """创建一次 set_intent 调用，驱动意图路由的 LLM 分类走到指定分支。"""
    import json

    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "set_intent",
            "arguments": json.dumps({"intent": intent, "reason": "受控测试：按预定意图 " + intent}),
        },
    }


def tasks_call(identifier: str, tasks: list[JsonObject]) -> JsonObject:
    """创建一次 set_tasks 调用，驱动协调者拆解出指定的调查任务。"""
    import json

    return {
        "id": identifier,
        "type": "function",
        "function": {"name": "set_tasks", "arguments": json.dumps({"tasks": tasks})},
    }


def hit() -> Passage:
    """返回可被前端回查的片段，非 business_rule 类型也允许被检索。"""
    return Passage(
        id="doc:RULE-1#c001",
        title="端点排查",
        text="HTTP 410 后禁用端点。",
        score=0.8,
        source=PassageSource(
            collection_id="fixture",
            doc_id="RULE-1",
            chunk_no=1,
            path="knowledge/endpoint.md",
            sha256="fixture",
            locator={"start_char": 0},
            metadata={"doc_type": "troubleshooting"},
        ),
    )


def other_hit(identifier: str, doc_id: str) -> Passage:
    """另一个片段，用于检验去重、引用选择与跨片段反馈。"""
    base = hit()
    return replace(base, id=identifier, source=replace(base.source, doc_id=doc_id))


class Search:
    """记录模型实际查询；可以返回空结果、故障或暂停以测试取消。"""

    def __init__(self) -> None:
        """每个测试持有独立的调用记录与阻塞信号。"""
        self.queries: list[tuple[str, int]] = []
        self.reads: list[str] = []
        self.fail: bool = False
        self.block: bool = False
        self.started: asyncio.Event = asyncio.Event()
        self.tracer: EventSink | None = None
        self.documents: dict[str, list[Passage]] = {"RULE-1": [hit()]}

    def for_run(self, tracer: EventSink) -> "Search":
        """按轮绑定追踪，与真实检索客户端同样的装配方式；调用记录跨轮累积。"""
        self.tracer = tracer
        return self

    async def search(
        self,
        collection_id: str,
        text: str,
        limit: int = 5,
        filters: JsonObject | None = None,
    ) -> list[Passage]:
        """核对集合和无文档类型过滤；包含 missing 的查询模拟零命中。"""
        assert collection_id == "fixture" and filters is None and self.tracer is not None
        self.queries.append((text, limit))
        await self.tracer.emit("knowledge.request", {"query": text, "limit": limit})
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        if self.fail:
            raise RuntimeError("search offline")
        results = [] if "missing" in text else [hit()]
        await self.tracer.emit(
            "knowledge.response", {"results": [to_json_object(r) for r in results]}
        )
        return results

    async def read_document(self, collection_id: str, doc_id: str) -> list[Passage]:
        """按编号整篇读回；未登记的编号返回空列表，与真实存储一致。"""
        assert collection_id == "fixture" and self.tracer is not None
        self.reads.append(doc_id)
        passages = self.documents.get(doc_id, [])
        await self.tracer.emit(
            "document.response",
            {"doc_id": doc_id, "passages": [to_json_object(p) for p in passages]},
        )
        return passages


class Model:
    """逐次返回预设动作，完整保存请求；不调用网络，不评价真实模型能力。"""

    name: str = "fixture"
    capabilities: dict[str, bool] = {"tools": True, "streaming": True}

    def __init__(self, chunked_content: int = 1, chunked_tool_args: bool = False) -> None:
        """测试显式安排每次调用结果，不用固定业务结论代替真实运行实现。

        chunked_content > 1 或 chunked_tool_args 时，invoke 把预设响应拆成原生
        流式帧经 ChatStreamAccumulator 重组返回，驱动分片路径；缺省保持原行为。
        """
        self.chunked_content: int = max(1, chunked_content)
        self.chunked_tool_args: bool = chunked_tool_args
        self.responses: list[ChatCompletion | Exception] = []
        self.requests: list[JsonObject] = []

    def prepare(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolParam],
    ) -> JsonObject:
        """保存深拷贝，避免工作流追加消息时污染已记录的请求。"""
        request: JsonObject = {"messages": copy.deepcopy(list(messages)), "tools": list(tools)}
        self.requests.append(request)
        return request

    async def invoke(self, request: JsonObject, emit: ChunkSink) -> ChatCompletion:
        """发出正文片段，让事件日志同时覆盖流式正文和最终协议响应。"""
        _ = request
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if self.chunked_content > 1 or self.chunked_tool_args:
            return await self._invoke_chunked(result, emit)
        if result.choices[0].message.content:
            await emit({"kind": "content_delta", "text": result.choices[0].message.content})
        return result

    async def _invoke_chunked(self, result: ChatCompletion, emit: ChunkSink) -> ChatCompletion:
        """按原生分片帧发射预设响应，由 ChatStreamAccumulator 重组返回。"""
        accumulator = ChatStreamAccumulator()
        for frame_chunk in self.wire_frames(result):
            text = accumulator.consume(frame_chunk)
            if text:
                await emit({"kind": "content_delta", "text": text})
        return accumulator.finish()

    def wire_frames(self, result: ChatCompletion) -> list[JsonObject]:
        """把预设响应拆成原生分片帧：正文按 chunked_content 分片，工具参数再拆片发射。"""
        choice = result.choices[0]
        message = choice.message

        def frame(delta: JsonObject, finish: str | None = None) -> JsonObject:
            return {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fixture",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        frames: list[JsonObject] = [frame({"role": "assistant"})]
        fields = cast(
            "JsonObject",
            message.model_dump(mode="json", exclude_unset=True, exclude_none=True),
        )
        for field, value in fields.items():
            if field not in {"role", "content", "tool_calls"}:
                # reasoning_content 等公开扩展字段按原样作为一次增量发射。
                frames.append(frame({field: value}))
        for piece in fragments(message.content or "", self.chunked_content):
            frames.append(frame({"content": piece}))
        # tool_calls 的 SDK 类型是 function/custom 联合；本夹具只构造 function 调用。
        calls = cast("list[ChatCompletionMessageFunctionToolCall]", message.tool_calls or [])
        for position, call in enumerate(calls):
            frames.append(
                frame(
                    {
                        "tool_calls": [
                            {
                                "index": position,
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.function.name},
                            }
                        ]
                    }
                )
            )
            pieces = (
                fragments(call.function.arguments, 3)
                if self.chunked_tool_args
                else [call.function.arguments]
            )
            for piece in pieces:
                frames.append(
                    frame({"tool_calls": [{"index": position, "function": {"arguments": piece}}]})
                )
        frames.append(frame({}, choice.finish_reason))
        if result.usage is not None:
            frames.append(
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture",
                    "choices": [],
                    "usage": result.usage.model_dump(mode="json", exclude_unset=True),
                }
            )
        return frames


def fragments(text: str, count: int) -> list[str]:
    """把文本拆成至多 count 个非空片段；文本过短或 count<=1 时返回单片段。"""
    if not text or count <= 1:
        return [text] if text else []
    count = min(count, len(text))
    size, extra = divmod(len(text), count)
    offsets = [i * size + min(i, extra) for i in range(count + 1)]
    return [text[offsets[i] : offsets[i + 1]] for i in range(count)]


def grounded(
    text: str = "HTTP 410 后禁用端点。", sources: list[str] | None = None
) -> ChatCompletion:
    """模拟原生 Markdown 回答，引用工具返回的真实文档 ID。"""
    identifiers = ["doc:RULE-1#c001"] if sources is None else sources
    return completion(text + "\n\n" + "".join(f"[{source}]" for source in identifiers))


def conversation(text: str) -> ChatCompletion:
    """模拟无需引用的原生文本回复。"""
    return completion(text)


def insufficient(missing: str = "端点恢复的适用条件") -> ChatCompletion:
    """依据不足时模型直接解释限制，不再切换回答协议。"""
    return completion("当前资料无法确认：" + missing)
