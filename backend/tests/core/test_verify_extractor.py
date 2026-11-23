# 回归测试：断言抽取调用（specs/010 FR-2）。固定输入复现边界，不作为业务答案来源。
from typing import cast

import pytest

from flowpay.core.json import JsonObject
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.verify.extractor import extract_assertions, parse_assertions
from tests.core.verify_fixtures import (
    RecordingSink,
    ScriptedModel,
    chat_client,
    completion,
    json_payload,
)

DRAFT = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。退款已提交 pay_bg_001_0003。"

EXTRACTIONS: list[JsonObject] = [
    {
        "text": "企业版所有用户最多等 5 天 [KB-TS-012#c001]。",
        "refs": ["doc:KB-TS-012#c001"],
        "record_ids": ["pay_bg_001_0003", "api_v2"],
    },
    {"text": "退款已提交 pay_bg_001_0003。", "refs": [], "record_ids": []},
]


def extractor_with(content: str | None) -> tuple[ChatClient, ScriptedModel, RecordingSink]:
    """装配脚本化抽取模型；返回客户端、模型与事件出口。"""
    model = ScriptedModel(completion(content))
    sink = RecordingSink()
    return chat_client(model, sink), model, sink


async def test_extract_returns_normalized_atomic_assertions() -> None:
    """清单解析：refs 规范化去前缀，record_ids 只保留业务记录词形。"""
    chat, _, _ = extractor_with(json_payload({"assertions": EXTRACTIONS}))
    extraction = await extract_assertions(chat, text=DRAFT)
    assert [a.text for a in extraction.assertions] == [
        "企业版所有用户最多等 5 天 [KB-TS-012#c001]。",
        "退款已提交 pay_bg_001_0003。",
    ]
    assert extraction.assertions[0].refs == ("KB-TS-012#c001",)
    assert extraction.assertions[0].record_ids == ("pay_bg_001_0003",)  # api_v2 非业务词形被滤掉
    assert extraction.assertions[1].refs == () and extraction.assertions[1].record_ids == ()
    assert extraction.completion is not None


async def test_extract_prompts_model_once_with_internal_output() -> None:
    """一次调用：正文进 user 消息；internal 输出口径落 model.request 事件。"""
    chat, model, sink = extractor_with(json_payload({"assertions": EXTRACTIONS}))
    await extract_assertions(chat, text=DRAFT)
    assert len(model.requests) == 1
    messages = cast("list[JsonObject]", model.requests[0]["messages"])
    assert any(DRAFT in str(m.get("content")) for m in messages)
    assert [kind for kind, _ in sink.events] == ["model.request", "model.delta", "model.response"]
    request_event = sink.events[0][1]
    assert request_event["output"] == "internal"


async def test_extract_empty_assertions_returns_empty_list() -> None:
    """无断言返回空清单（纯操作指引/客套话回答）。"""
    chat, _, _ = extractor_with(json_payload({"assertions": []}))
    extraction = await extract_assertions(chat, text="请按以下步骤操作：")
    assert extraction.assertions == ()


async def test_extract_tolerates_markdown_fences() -> None:
    """模型用代码栅栏包 JSON 时照常解析。"""
    chat, _, _ = extractor_with(f"```json\n{json_payload({'assertions': EXTRACTIONS})}\n```")
    extraction = await extract_assertions(chat, text=DRAFT)
    assert len(extraction.assertions) == 2


async def test_extract_dedupes_identical_assertion_texts() -> None:
    """同句重复产出只保留第一条，处置才不会对同一句替换两次。"""
    chat, _, _ = extractor_with(
        json_payload({"assertions": [EXTRACTIONS[0], EXTRACTIONS[0]]})
    )
    extraction = await extract_assertions(chat, text=DRAFT)
    assert len(extraction.assertions) == 1


@pytest.mark.parametrize(
    "content",
    [
        "不是 JSON 的正文",
        "{\"kind\": \"other\"}",  # 缺 assertions 字段
        "{\"assertions\": \"no-list\"}",  # 清单不是列表
        "{\"assertions\": [{}]}",  # 条目缺 text
        "{\"assertions\": [{\"text\": \"  \"}]}",  # 空白句视同无断言
        "{\"assertions\": [{\"text\": 42}]}",
    ],
)
async def test_extract_bad_output_raises_for_fail_open(content: str) -> None:
    """坏输出上抛由 fail-open 包装兜底；空白句视同无断言不抛。"""
    chat, _, _ = extractor_with(content)
    if content == "{\"assertions\": [{\"text\": \"  \"}]}":
        assert (await extract_assertions(chat, text=DRAFT)).assertions == ()
        return
    with pytest.raises(ValueError):
        await extract_assertions(chat, text=DRAFT)


async def test_extract_model_failure_propagates() -> None:
    """调用失败上抛（取消与故障由包装层决定 fail-open）。"""
    model = ScriptedModel(RuntimeError("model offline"))
    chat = chat_client(model, RecordingSink())
    with pytest.raises(RuntimeError):
        await extract_assertions(chat, text=DRAFT)


def test_parse_assertions_is_pure() -> None:
    """解析纯函数可直接对响应对象复用（回放侧同构）。"""
    parsed = parse_assertions(completion(json_payload({"assertions": EXTRACTIONS})))
    assert parsed[0].refs == ("KB-TS-012#c001",)
