"""Responses SSE 流聚合器：事件白名单、文本增量、tool_calls 累积、reasoning 保留、usage 映射。

事件形态按 specs/008 data-model.md 的处理表逐行覆盖；固定输入用于复现边界，
不作为业务答案来源。
聚合产物经 Choice.model_validate 落在 SDK 的 tool_call 联合类型上，运行时确定
function 变体；类型检查器无法收窄 union，这两条断言关闭该检查（graph.py 同先例）。
"""

# pyright: reportAttributeAccessIssue=false

from typing import cast

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from flowpay.core.json import JsonObject
from flowpay.core.llm.chat.responses_stream import ResponsesStreamAccumulator


def feed(accumulator: ResponsesStreamAccumulator, frames: list[JsonObject]) -> str:
    """逐帧喂入并拼接公开文本增量。"""
    return "".join(accumulator.consume(frame) for frame in frames)


def test_output_text_delta_becomes_public_increment() -> None:
    """output_text.delta 是公开正文，聚合后经 finish 进 message.content。"""
    acc = ResponsesStreamAccumulator()
    text = feed(
        acc,
        [
            {"type": "response.output_text.delta", "delta": "排查结论："},
            {"type": "response.output_text.delta", "delta": "API 版本不匹配。"},
        ],
    )
    assert text == "排查结论：API 版本不匹配。"


def test_function_call_fragments_accumulate_by_item() -> None:
    """function_call_arguments.delta 按 item id 累积参数，不混入展示文本。

    added 帧的 item.id 是 Responses 侧 item 标识（测试用 fc_1），delta 帧按它路由；
    call_id 是工具调用回执的成对编号，两者不同。
    """
    acc = ResponsesStreamAccumulator()
    text = feed(
        acc,
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "get_request",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"id":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '"req_1"}',
            },
            {"type": "response.completed", "response": {"usage": {}}},
        ],
    )
    assert text == ""
    finished = acc.finish()
    tool_calls = finished.choices[0].message.tool_calls
    first = cast("ChatCompletionMessageToolCall", tool_calls[0]) if tool_calls else None
    assert first is not None
    assert first.function.name == "get_request"
    assert first.function.arguments == '{"id":"req_1"}'
    assert finished.choices[0].finish_reason == "tool_calls"


def test_reasoning_items_are_kept_not_displayed() -> None:
    """reasoning item 原文进扩展存储，供下一轮重放；不进入展示文本。"""
    acc = ResponsesStreamAccumulator()
    text = feed(
        acc,
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "rs_1", "summary": []},
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc-abc"},
            },
            {"type": "response.output_text.delta", "delta": "答案"},
        ],
    )
    assert text == "答案"
    assert [item["encrypted_content"] for item in acc.reasoning_items] == ["enc-abc"]


def test_reasoning_items_survive_finish_into_message() -> None:
    """finish 后 reasoning_items 随 message 扩展字段返回，供下一轮 _input_items 回插。

    审查发现的断裂点：聚合器收进了 reasoning_items 但 finish 组装 message 时漏挂，
    导致加密推理每步全丢（静默，不报错）。本用例守住"聚合 → message → 历史写入"
    的完整链路。
    """
    acc = ResponsesStreamAccumulator()
    feed(
        acc,
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "rs_1", "summary": []},
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc-abc"},
            },
            {"type": "response.output_text.delta", "delta": "答案"},
            {"type": "response.completed", "response": {"usage": {}}},
        ],
    )
    finished = acc.finish()
    message = finished.choices[0].message
    extra = message.model_extra or {}
    assert extra.get("reasoning_items") == [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc-abc"}
    ]


def test_usage_maps_input_output_to_prompt_completion() -> None:
    """completed 帧的 usage 按 input/output/total 映射为既有字段名。"""
    acc = ResponsesStreamAccumulator()
    feed(
        acc,
        [
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 90,
                        "total_tokens": 590,
                    }
                },
            },
        ],
    )
    finished = acc.finish()
    assert finished.usage is not None
    assert finished.usage.prompt_tokens == 500
    assert finished.usage.completion_tokens == 90
    assert finished.usage.total_tokens == 590


def test_completed_sets_stop_finish_reason() -> None:
    """completed 终态在无工具调用时 finish_reason=stop。"""
    acc = ResponsesStreamAccumulator()
    feed(acc, [{"type": "response.completed", "response": {"usage": {}}}])
    assert acc.finish().choices[0].finish_reason == "stop"


def test_unknown_events_are_tolerated() -> None:
    """未知事件类型不阻断聚合（原文由外层 wire 事件落库）。"""
    acc = ResponsesStreamAccumulator()
    text = feed(
        acc,
        [
            {"type": "response.some_future_event", "data": 1},
            {"type": "response.output_text.delta", "delta": "继续"},
        ],
    )
    assert text == "继续"


def test_finish_without_completed_is_rejected() -> None:
    """流结束但没有完成事件时拒绝返回成功（与 chat 口径一致）。"""
    acc = ResponsesStreamAccumulator()
    feed(acc, [{"type": "response.output_text.delta", "delta": "半截"}])
    with pytest.raises(RuntimeError, match="finish_reason"):
        acc.finish()


def test_incomplete_counts_as_finished_with_reason() -> None:
    """incomplete 也是终态（记录原因），不算中断。"""
    acc = ResponsesStreamAccumulator()
    feed(
        acc,
        [
            {"type": "response.output_text.delta", "delta": "部分"},
            {
                "type": "response.incomplete",
                "response": {
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            },
        ],
    )
    finished = acc.finish()
    assert finished.choices[0].finish_reason == "length"


def test_failed_event_raises() -> None:
    """response.failed 上抛异常，走既有失败分类。"""
    acc = ResponsesStreamAccumulator()
    with pytest.raises(RuntimeError, match="failed"):
        acc.consume(
            {
                "type": "response.failed",
                "response": {"error": {"code": "server_error", "message": "boom"}},
            }
        )
