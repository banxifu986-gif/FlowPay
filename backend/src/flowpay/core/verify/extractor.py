"""断言抽取调用（specs/010 FR-2）：LLM 一次调用把待校验文本拆为原子断言清单。

抽取只产清单不改草稿；断言 text 取待校验文本的完整原句（处置按句级等值匹配，
research D5），refs/record_ids 从原句抄录。输出经既有解析工具（core/json）解析，
坏输出上抛由 fail-open 包装兜底（FR-6）；无断言返回空清单。断言文本逐字去重，
处置才不会对同一句执行两次替换。
"""

from __future__ import annotations

from dataclasses import dataclass

from openai.types.chat import ChatCompletion

from flowpay.core.chat.record_ids import BUSINESS_RECORD_ID
from flowpay.core.json import parse_json, require_list, require_object, require_text
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.llm.chat.messages import answer_text
from flowpay.core.verify.contracts import Assertion

EXTRACT_SYSTEM_PROMPT = (
    "你是 FlowPay 技术支持助手的断言抽取器。把待校验文本逐句拆为原子断言清单，"
    '只输出 JSON 对象：{"assertions": [{"text": "...", "refs": [], "record_ids": []}]}。\n'
    "规则：\n"
    "- text 必须是待校验文本中的完整原句（含句尾标点），不要改写、不要合并或拆碎；\n"
    "- refs 抄录该句里的片段引用（如 KB-TS-012#c001），原句没有就给空数组；\n"
    "- record_ids 只抄业务记录编号（如 pay_bg_001_0003）；字段名、错误码不算记录编号；\n"
    "- 只收事实陈述句；操作指引、客套话、疑问句不产出断言；\n"
    '- 没有断言时输出 {"assertions": []}；除 JSON 外不要输出任何其他文字。'
)


@dataclass(frozen=True)
class Extraction:
    """一次抽取调用的产物：断言清单与原始响应（响应供调用方入账用量）。"""

    assertions: tuple[Assertion, ...]
    completion: ChatCompletion


def decode_json(text: str) -> object:
    """从模型正文解码 JSON：容忍 markdown 代码栅栏包裹；解析失败原样上抛。

    既有解析工具（core/json）只认裸 JSON 文本，模型常见的 ```json 围栏在这里剥掉。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    return parse_json(stripped)


def _refs(raw: object) -> tuple[str, ...]:
    """规范化条目里的片段引用：去方括号与 doc: 前缀，按出现顺序去重。"""
    return tuple(
        dict.fromkeys(
            normalized
            for value in require_list(raw)
            if (normalized := require_text(value).strip("[]").removeprefix("doc:"))
        )
    )


def _record_ids(raw: object) -> tuple[str, ...]:
    """条目里的记录编号：只保留业务记录词形（specs/009 强词形备注）。

    api_v2 这类「字母段尾带数字」的词形不是记录编号，留下会因摘录缺失被误降级。
    """
    return tuple(
        dict.fromkeys(
            identifier
            for value in require_list(raw)
            if BUSINESS_RECORD_ID.search(identifier := require_text(value).strip())
        )
    )


def parse_assertions(completion: ChatCompletion) -> tuple[Assertion, ...]:
    """从抽取响应解析原子断言清单；结构或必需字段缺失上抛 ValueError。"""
    body = require_object(decode_json(answer_text(completion)))
    items = require_list(body.get("assertions"))
    assertions: list[Assertion] = []
    seen: set[str] = set()
    for item in items:
        record = require_object(item)
        text = require_text(record.get("text")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        assertions.append(
            Assertion(
                text=text,
                refs=_refs(record.get("refs", [])),
                record_ids=_record_ids(record.get("record_ids", [])),
            )
        )
    return tuple(assertions)


async def extract_assertions(chat: ChatClient, *, text: str) -> Extraction:
    """抽取一次调用：正文进 user 消息，internal 输出口径不进回答展示。"""
    completion, _ = await chat.call(
        [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": f"## 待校验文本\n{text}"},
        ],
        output="internal",
    )
    return Extraction(assertions=parse_assertions(completion), completion=completion)
