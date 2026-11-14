"""usage_view 的提取与聚合契约：缺失容错，None 与 0 严格区分。"""

from openai.types.chat import ChatCompletion
from openai.types.completion_usage import CompletionUsage

from flowpay.core.chat.usage_view import UsageEntry, UsageSummary, from_completion, sum_entries


def chat_completion(usage: CompletionUsage | None) -> ChatCompletion:
    """构造最小响应对象，只带要考察的 usage。"""
    return ChatCompletion.model_construct(object="chat.completion", choices=[], usage=usage)


def test_full_usage_is_extracted() -> None:
    """三个字段齐全时按值提取。"""
    entry = from_completion(
        chat_completion(
            CompletionUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150)
        )
    )
    assert entry == UsageEntry(120, 30, 150)


def test_missing_usage_is_none_not_zero() -> None:
    """usage 为 None 时全部字段记 None，不伪装成 0。"""
    assert from_completion(chat_completion(None)) == UsageEntry(None, None, None)


def test_empty_usage_object_is_all_none() -> None:
    """流式聚合的宽松构造可能只带回空用量对象。"""
    assert from_completion(chat_completion(CompletionUsage.model_construct())) == UsageEntry(
        None, None, None
    )


def test_partial_usage_keeps_missing_fields_none() -> None:
    """部分字段缺失时其余照常提取，缺失位保持 None。"""
    entry = from_completion(chat_completion(CompletionUsage.model_construct(prompt_tokens=120)))
    assert entry == UsageEntry(120, None, None)


def test_sum_adds_fully_known_entries() -> None:
    """无缺失时逐位求和，model_calls 是调用次数。"""
    entries = [UsageEntry(10, 5, 15), UsageEntry(20, 10, 30)]
    assert sum_entries(entries) == UsageSummary(2, 30, 15, 45)


def test_sum_with_missing_field_is_none() -> None:
    """任一位有缺失则该位记 None，不按 0 补齐求和。"""
    entries = [UsageEntry(10, None, None), UsageEntry(20, 10, 30)]
    assert sum_entries(entries) == UsageSummary(2, 30, None, None)


def test_empty_entries_zero_calls_all_none() -> None:
    """零调用轮：model_calls 为 0，各用量位 None。"""
    assert sum_entries([]) == UsageSummary(0, None, None, None)
