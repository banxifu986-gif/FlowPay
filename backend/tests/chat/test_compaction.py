"""压缩纯函数的六类用例（T002）：判定/触发切点/信封替换/迭代合并/畸形/组合不变量。

压缩把检查点消息流里切点前的历史整组替换为一条摘要 system 消息；record_ids 由
代码从被删消息确定性提取，不由模型填写（research R-3）。纯函数层不调模型、不发
事件，编排层（turn.py）单独测试。
"""

import copy
import json
from typing import cast

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from flowpay.core.chat.compaction import (
    compact_messages,
    is_summary_message,
    select_compaction_range,
    summary_user_prompt,
)
from flowpay.core.chat.projection import ContextPolicy, project_messages
from flowpay.core.json import JsonObject, parse_json, require_object

TRIGGER = 5000
KEEP = 2


def system_message(content: str) -> ChatCompletionSystemMessageParam:
    return {"role": "system", "content": content}


def user_message(content: str) -> ChatCompletionUserMessageParam:
    return {"role": "user", "content": content}


def summary_message(text: str) -> ChatCompletionSystemMessageParam:
    """一条合法的既有摘要信封（system 形态）。"""
    return system_message(
        json.dumps(
            {"kind": "session_summary", "text": text, "record_ids": [], "rounds": 1},
            ensure_ascii=False,
        )
    )


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


def big_result(rows: int = 4, record_id: str = "pay_bg_001_0003") -> str:
    """大工具结果：rows 撑起体积，同时携带可核查的记录编号。"""
    return json.dumps(
        {"rows": [{"id": record_id, "note": "x" * 150} for _ in range(rows)]},
        ensure_ascii=False,
    )


def long_conversation(rounds: int) -> list[ChatCompletionMessageParam]:
    """system + user + N 轮（assistant+大工具结果，奇偶轮携带不同编号）+ 收尾 user。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
    ]
    for index in range(rounds):
        record = f"pay_bg_001_{index:04d}"
        messages.append(assistant_call(f"c{index}"))
        messages.append(tool_message(f"c{index}", big_result(record_id=record)))
    messages.append(user_message("结合上面的结果回答"))
    return messages


def as_objects(messages: list[ChatCompletionMessageParam]) -> list[JsonObject]:
    return cast("list[JsonObject]", messages)


def test_is_summary_message_recognition() -> None:
    """判定合法信封；畸形 content、缺 kind、非摘要 kind、非 system 角色一律 False。"""
    assert is_summary_message(summary_message("进度"))
    for malformed in [
        system_message("普通系统消息"),
        system_message("不是 JSON"),
        system_message(json.dumps({"kind": "other"}, ensure_ascii=False)),
        system_message(json.dumps({"text": "缺 kind"}, ensure_ascii=False)),
        user_message(json.dumps({"kind": "session_summary"}, ensure_ascii=False)),
    ]:
        assert not is_summary_message(malformed)


def test_select_returns_none_when_under_trigger() -> None:
    """原生流体积未超触发线：不压缩，返回 None（编排层不花摘要调用）。"""
    messages = long_conversation(2)
    assert select_compaction_range(as_objects(messages), TRIGGER, KEEP) is None


def test_select_returns_none_when_not_enough_rounds() -> None:
    """可压缩组数不多于保留数：返回 None——压缩无收益。"""
    messages = long_conversation(2)
    assert select_compaction_range(as_objects(messages), 10, KEEP) is None


def test_select_cut_discipline() -> None:
    """切点纪律：只选中可删整组（assistant+尾随 tool），保留最近 KEEP 组与其后的消息。"""
    messages = long_conversation(5)
    groups = select_compaction_range(as_objects(messages), 10, KEEP)
    assert groups is not None
    selected = [index for group in groups for index in group]
    dropped_ids = {
        str(as_objects(messages)[index].get("tool_call_id"))
        for index in selected
        if as_objects(messages)[index].get("role") == "tool"
    }
    # 最近 KEEP 组（c3/c4）与其后的收尾 user 不在压缩区间。
    assert dropped_ids.isdisjoint({"c3", "c4"})
    # 选中的下标在消息流里构成 assistant+尾随 tool 的完整组，不拆配对。
    roles = [as_objects(messages)[index].get("role") for index in selected]
    assert roles == ["assistant", "tool", "assistant", "tool", "assistant", "tool"]
    # 前三个完整交换（c0/c1/c2）全部被选中。
    assert dropped_ids == {"c0", "c1", "c2"}


def test_compact_replaces_with_envelope() -> None:
    """压缩产出摘要信封：替换而非追加、编号由代码提取、输入不被修改。"""
    messages = long_conversation(5)
    original = copy.deepcopy(messages)
    groups = select_compaction_range(as_objects(messages), 10, KEEP)
    assert groups is not None
    result, _, rounds = compact_messages(as_objects(messages), groups, "历史摘要正文", 4000)
    assert rounds == 3
    # 输入原样——检查点语义由编排层决定，纯函数不偷改。
    assert messages == original
    # 恰好一条摘要，且首条 system/保留轮次/收尾 user 原样存活。
    summaries = [m for m in result if is_summary_message(m)]
    assert len(summaries) == 1
    body = require_object(parse_json(str(summaries[0].get("content"))))
    assert body["text"] == "历史摘要正文"
    assert body["rounds"] == 3
    # 被删轮次的编号全部由代码提取（奇偶轮不同编号），排序去重。
    assert body["record_ids"] == [f"pay_bg_001_{i:04d}" for i in range(3)]
    objects = as_objects(result)
    surviving_ids = {
        str(m.get("tool_call_id")) for m in objects if m.get("role") == "tool"
    }
    assert surviving_ids == {"c3", "c4"}
    assert result[0] == messages[0] and result[-1] == messages[-1]
    assert all(m.get("role") == "user" for m in result if m.get("role") == "user")


def test_compact_truncates_oversized_summary() -> None:
    """摘要正文超上限：代码截断到上限后落盘（clarify 决议 B），编号信封不受影响。"""
    messages = long_conversation(5)
    groups = select_compaction_range(as_objects(messages), 10, KEEP)
    assert groups is not None
    result, _, _ = compact_messages(as_objects(messages), groups, "长" * 9000, 100)
    summaries = [m for m in result if is_summary_message(m)]
    body = require_object(parse_json(str(summaries[0].get("content"))))
    text = str(body["text"])
    assert len(text) <= 100
    assert body["record_ids"] == [f"pay_bg_001_{i:04d}" for i in range(3)]


def test_compact_keeps_at_most_one_summary() -> None:
    """二次压缩：新摘要替换旧摘要位置（取更靠前），流内至多一条，编号跨压缩链累积。"""
    # 旧信封携带更早轮次的编号（其原始消息已被首次压缩删除，不在当前流里）。
    legacy = system_message(
        json.dumps(
            {
                "kind": "session_summary",
                "text": "上一段进度",
                "record_ids": ["pay_bg_009_9999"],
                "rounds": 3,
            },
            ensure_ascii=False,
        )
    )
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("开始排查"),
        legacy,
        assistant_call("c0"),
        tool_message("c0", big_result()),
        assistant_call("c1"),
        tool_message("c1", big_result(record_id="pay_bg_001_0001")),
        assistant_call("c2"),
        tool_message("c2", big_result(record_id="pay_bg_001_0002")),
        user_message("继续"),
    ]
    groups = select_compaction_range(as_objects(messages), 10, 1)
    assert groups is not None
    result, _, rounds = compact_messages(as_objects(messages), groups, "新进度", 4000)
    assert rounds == 2  # 三个完整交换保留最近 1 个，压掉最老两个
    summaries = [m for m in result if is_summary_message(m)]
    assert len(summaries) == 1
    body = require_object(parse_json(str(summaries[0].get("content"))))
    assert body["text"] == "新进度"
    # 编号 = 本次被删消息提取 ∪ 旧信封继承——历史编号跨压缩链不断裂（R-8 对策）。
    assert body["record_ids"] == ["pay_bg_001_0001", "pay_bg_001_0003", "pay_bg_009_9999"]
    # 摘要插在旧摘要位置（更靠前），旧摘要正文不再出现在任何消息里。
    assert all("上一段进度" not in str(m.get("content")) for m in as_objects(result))


def test_summary_prompt_includes_previous_summary_and_budget() -> None:
    """二次压缩的摘要请求：既有摘要以 previous-summary 标签传入，并声明字符上限。"""
    dropped = as_objects(long_conversation(2))[:4]
    prompt = summary_user_prompt(dropped, "上一段进度", 4000)
    assert "<previous-summary>" in prompt
    assert "上一段进度" in prompt
    assert "4000" in prompt
    first = summary_user_prompt(dropped, None, 4000)
    assert "<previous-summary>" not in first


def test_summary_prompt_clips_tool_results() -> None:
    """摘要输入里的大工具结果被截断到短预算，防止摘要请求比被摘要上下文还大。"""
    huge = "y" * 50000
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("排查 pay_bg_001_0003"),
        assistant_call("c0"),
        tool_message("c0", json.dumps({"rows": [{"note": huge}]}, ensure_ascii=False)),
    ]
    prompt = summary_user_prompt(as_objects(messages), None, 4000)
    assert "排查 pay_bg_001_0003" in prompt  # user 内容保留
    assert huge not in prompt  # 大工具结果不整段进入摘要输入


def test_malformed_inputs_are_safe() -> None:
    """全函数契约：畸形输入不抛异常——select 返回 None，compact 空组原样带回。"""
    garbage = cast("list[JsonObject]", ["garbage", {"role": "tool", "content": "缺配对"}])
    assert select_compaction_range(garbage, 10, KEEP) is None
    messages = as_objects(long_conversation(5))
    result, record_ids, rounds = compact_messages(messages, [], "任何摘要", 4000)
    assert result == messages and record_ids == [] and rounds == 0


def test_projection_after_compaction_holds_invariants() -> None:
    """组合不变量：压缩后的消息流再过投影，配对完整、摘要存活、保护集合原样。"""
    messages = long_conversation(6)
    groups = select_compaction_range(as_objects(messages), 10, KEEP)
    assert groups is not None
    compacted, _, _ = compact_messages(as_objects(messages), groups, "进度摘要", 4000)
    policy = ContextPolicy(char_budget=3000, tool_result_budget=400)
    projected = as_objects(project_messages(compacted, policy))
    # 摘要（中段 system）永不被投影删除。
    assert any(is_summary_message(m) for m in projected)
    # 配对完整（与 test_projection.py 同款：只收集到下一个非 tool 消息为止）。
    for index, message in enumerate(projected):
        if message.get("role") != "assistant":
            continue
        calls = cast("list[JsonObject]", message.get("tool_calls") or [])
        identifiers = [str(call.get("id")) for call in calls]
        following: list[str] = []
        for later in projected[index + 1 :]:
            if later.get("role") != "tool":
                break
            following.append(str(later.get("tool_call_id")))
        assert identifiers == following
    # 首条 system、任何 user、最后一段交换原样。
    assert projected[0] == compacted[0]
    assert [m for m in projected if m.get("role") == "user"] == [
        m for m in compacted if m.get("role") == "user"
    ]


def test_orphan_tool_follows_dropped_assistant_on_compact() -> None:
    """病态相邻性：属主 assistant 被删时孤儿 tool 一并删除，与投影清扫纪律对齐。"""
    messages: list[ChatCompletionMessageParam] = [
        system_message("你是排查助手。"),
        user_message("开始排查"),
        assistant_call("c0"),
        user_message("补充一句"),
        tool_message("c0", big_result()),
        assistant_call("c1"),
        tool_message("c1", big_result(record_id="pay_bg_001_0001")),
        assistant_call("c2"),
        tool_message("c2", big_result(record_id="pay_bg_001_0002")),
        user_message("继续"),
    ]
    groups = select_compaction_range(as_objects(messages), 10, 1)
    assert groups is not None
    result, _, _ = compact_messages(as_objects(messages), groups, "摘要", 4000)
    surviving = {str(m.get("tool_call_id")) for m in as_objects(result) if m.get("role") == "tool"}
    assert surviving == {"c2"}  # c0 的孤儿 tool 随属主删除
