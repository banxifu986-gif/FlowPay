"""模型用量的提取与聚合；纯函数、缺失容错，不推测未提供的字段。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from openai.types.chat import ChatCompletion


@dataclass(frozen=True)
class UsageEntry:
    """一次模型响应的用量；供应商未提供的字段记 None，不按 0 补齐。

    None 与 0 语义不同：0 表示供应商明确报了 0，None 表示没有返回该字段。
    """

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class UsageSummary:
    """一轮内全部模型调用的用量汇总。"""

    model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def from_completion(completion: ChatCompletion) -> UsageEntry:
    """提取单次用量；usage 缺失、为 None 或空对象时各字段记 None。

    流式聚合可能只带回部分用量字段（stream.py 的宽松构造），按属性逐个读取，
    只接受 int 值，其余一律记 None，不让部分缺失伪装成 0。
    """
    usage = completion.usage
    return UsageEntry(
        _tokens(usage, "prompt_tokens"),
        _tokens(usage, "completion_tokens"),
        _tokens(usage, "total_tokens"),
    )


def sum_entries(entries: Iterable[UsageEntry]) -> UsageSummary:
    """按轮聚合；model_calls 是调用次数，任一位有缺失则该位记 None（不推测总数）。"""
    items = list(entries)
    return UsageSummary(
        model_calls=len(items),
        input_tokens=_total(entry.input_tokens for entry in items),
        output_tokens=_total(entry.output_tokens for entry in items),
        total_tokens=_total(entry.total_tokens for entry in items),
    )


def _tokens(usage: object, name: str) -> int | None:
    """读取用量对象的一个字段；缺失或非 int 记 None。"""
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _total(values: Iterable[int | None]) -> int | None:
    """逐位求和；没有值或遇缺失都返回 None——没有任何测量与缺失都不参与推测。"""
    result = 0
    seen = False
    for value in values:
        seen = True
        if value is None:
            return None
        result += value
    return result if seen else None
