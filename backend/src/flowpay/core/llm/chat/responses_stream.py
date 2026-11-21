"""聚合 Responses API 流；不负责网络、重试或日志写入。

事件白名单见 specs/008 data-model.md；未知事件原文由外层 wire 事件落库，不阻断聚合。
"""

from __future__ import annotations

from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage

from flowpay.core.json import JsonObject, require_int, require_object, require_text

_FINISH_REASONS = {
    "response.completed": "stop",
    "response.incomplete": "length",
}


class ResponsesStreamAccumulator:
    """把 Responses 流事件合并成 SDK ChatCompletion 同形状；原始帧由外层日志保留。"""

    def __init__(self) -> None:
        """初始化消息、工具槽、推理存储与用量；聚合纪律与 chat 侧一致。"""
        self.message: JsonObject = {"role": "assistant", "content": ""}
        self.tool_calls: dict[int, JsonObject] = {}
        self.reasoning_items: list[JsonObject] = []
        self.usage: JsonObject = {}
        self.finish_reason: str = ""

    def consume(self, frame: JsonObject) -> str:
        """合并一帧并返回公开文本增量；工具与推理字段不混入展示文本。"""
        kind = require_text(frame["type"])
        if kind == "response.output_text.delta":
            delta = frame.get("delta")
            if isinstance(delta, str) and delta:
                self.message["content"] = require_text(self.message.get("content", "")) + delta
                return delta
            return ""
        if kind == "response.output_item.added":
            item = require_object(frame.get("item") or {})
            self._open_item(item)
            return ""
        if kind == "response.function_call_arguments.delta":
            self._append_arguments(require_text(frame["item_id"]), frame.get("delta") or "")
            return ""
        if kind == "response.output_item.done":
            item = require_object(frame.get("item") or {})
            self._close_item(item)
            return ""
        if kind in _FINISH_REASONS:
            self.finish_reason = "tool_calls" if self.tool_calls else _FINISH_REASONS[kind]
            response = require_object(frame.get("response") or {})
            raw_usage = require_object(response.get("usage") or self.usage)
            # Responses 的字段名映射为 ChatCompletion 侧既有字段名（specs/008 data-model）。
            self.usage = {
                "prompt_tokens": require_int(raw_usage.get("input_tokens", 0)),
                "completion_tokens": require_int(raw_usage.get("output_tokens", 0)),
                "total_tokens": require_int(raw_usage.get("total_tokens", 0)),
            }
            return ""
        if kind == "response.failed":
            error = require_object(require_object(frame.get("response") or {}).get("error") or {})
            raise RuntimeError(f"Responses stream failed: {error}.")
        # 未知事件不阻断：原文已由外层 model.chunk 落库。
        return ""

    def _open_item(self, item: JsonObject) -> None:
        """按 item 类型开槽；function_call 与 reasoning 各走各的存储。"""
        item_type = require_text(item["type"])
        if item_type == "function_call":
            index = len(self.tool_calls)
            self.tool_calls[index] = {
                "id": require_text(item.get("call_id") or ""),
                "type": "function",
                "function": {"name": require_text(item.get("name") or ""), "arguments": ""},
                "_item_id": require_text(item.get("id") or ""),
            }
        elif item_type == "reasoning":
            return  # added 侧无内容；内容在 done 帧。

    def _append_arguments(self, item_id: str, delta: object) -> None:
        """按 item id 追加参数片段；流未结束时不解析 JSON。"""
        for tool_call in self.tool_calls.values():
            if tool_call.get("_item_id") == item_id:
                function = require_object(tool_call["function"])
                function["arguments"] = require_text(function.get("arguments", "")) + require_text(
                    delta
                )
                return

    def _close_item(self, item: JsonObject) -> None:
        """done 帧定稿：reasoning 原文入存储；function_call 用终值覆盖累积。"""
        item_type = require_text(item["type"])
        if item_type == "reasoning":
            self.reasoning_items.append(item)
        elif item_type == "function_call":
            item_id = require_text(item.get("id") or "")
            for tool_call in self.tool_calls.values():
                if tool_call.get("_item_id") == item_id:
                    function = require_object(tool_call["function"])
                    function["name"] = require_text(item.get("name") or function["name"])
                    final = require_text(item.get("arguments") or function["arguments"])
                    function["arguments"] = final

    def finish(self) -> ChatCompletion:
        """返回包含首个候选的 SDK ChatCompletion；缺少完成事件时拒绝返回成功。"""
        if not self.finish_reason:
            raise RuntimeError("Responses stream ended without finish_reason")
        if self.tool_calls:
            self.message["tool_calls"] = [
                {key: value for key, value in self.tool_calls[index].items() if key != "_item_id"}
                for index in sorted(self.tool_calls)
            ]
        return ChatCompletion.model_construct(
            object="chat.completion",
            choices=[
                Choice.model_validate(
                    {
                        "index": 0,
                        "finish_reason": self.finish_reason,
                        "message": self.message,
                    }
                )
            ],
            usage=CompletionUsage.model_construct(**self.usage, _fields_set=None)
            if self.usage
            else None,
        )
