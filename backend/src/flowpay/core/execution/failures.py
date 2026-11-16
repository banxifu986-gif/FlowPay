"""失败分类与恢复策略映射（specs/006 US2）。

分类只看异常的类型与供应商状态码，不读工作流内部状态；分类结果决定是否重试和
写进事件载荷的用户文案。完整异常仍由管理器记服务端日志（沿用 005 F3 脱敏口径）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flowpay.core.llm.chat.http_error import ModelHTTPError, is_context_over_limit

FailureCategory = Literal[
    "timeout", "rate_limit", "context_over_limit", "quota", "model_5xx", "other"
]

# 429/402 正文里的配额线索：命中即按「额度用尽」处理，不再重试。
QUOTA_KEYWORDS = ("quota", "billing", "insufficient", "额度", "余额")


@dataclass(frozen=True)
class FailureInfo:
    """一次失败的分类、是否可重试与用户可见文案。"""

    category: FailureCategory
    retryable: bool
    message: str


FRIENDLY: dict[FailureCategory, str] = {
    "timeout": "模型服务响应超时，已自动重试后仍未成功；请稍后再试本轮。",
    "rate_limit": "模型服务当前限流，请稍后再试。",
    "context_over_limit": "本轮上下文超出模型上限，压缩后仍无法继续；请新建会话或精简问题。",
    "quota": "模型服务配额已用尽，请联系管理员补充额度。",
    "model_5xx": "模型服务暂时不可用，请稍后重试本轮。",
    "other": "本轮执行失败，记录已保留；可查看轨迹或继续本轮。",
}


def classify_failure(error: BaseException) -> FailureInfo:
    """把异常映射成类别与恢复策略；分类不抛异常，未知一律归 other。"""
    category = _category(error)
    return FailureInfo(
        category, category in {"timeout", "rate_limit", "model_5xx"}, FRIENDLY[category]
    )


def _category(error: BaseException) -> FailureCategory:
    """按状态码优先、再按异常类型判定；关键词只用于区分限流与额度用尽。"""
    if isinstance(error, ModelHTTPError):
        return _http_category(error)
    name = type(error).__name__.lower()
    if isinstance(error, TimeoutError) or "timeout" in name:
        return "timeout"
    # 连接建立失败与超时同属可重试；流中断后的连接错误仍由上层判断是否已产出。
    if "connect" in name:
        return "timeout"
    return "other"


def _http_category(error: ModelHTTPError) -> FailureCategory:
    """供应商 HTTP 错误的分类：超限 400、额度 402/429、限流 429、5xx。"""
    if is_context_over_limit(error):
        return "context_over_limit"
    lowered = error.body_excerpt.lower()
    if error.status_code in {402, 429} and any(word in lowered for word in QUOTA_KEYWORDS):
        return "quota"
    if error.status_code == 429:
        return "rate_limit"
    if error.status_code >= 500:
        return "model_5xx"
    return "other"
