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

# FR-1 确定性白名单（specs/009）：错误形态枚举，测试固化；不做宽泛状态码归类。
# 档位的职责是驱动工具链路禁用判定（FR-2）并进入 tool.outage 事件载荷——
# manager 的重试语义不读本档位（现状模型类 400 已归 other，不可重试）。
# ①嵌入/检索对查询文本的结构性拒绝（400/422 + 输入不合法语义）；
# ②请求参数被供应商确定性拒绝（如推理参数不兼容）。首例关键词锚定 R96B
# FP-BM-TV-0116 的真实嵌入 400 正文（SiliconFlow "The parameter is invalid."）。
DETERMINISTIC_STATUSES = {400, 422}
INPUT_INVALID_KEYWORDS = (
    "parameter is invalid",
    "invalid parameter",
    "invalid input",
    "输入不合法",
    "参数不合法",
    "参数无效",
)
PARAM_REJECTED_KEYWORDS = (
    "unsupported parameter",
    "unknown parameter",
    "unrecognized parameter",
    "not supported",
)


@dataclass(frozen=True)
class FailureInfo:
    """一次失败的分类、是否可重试、确定性档位与用户可见文案。

    deterministic 是 FR-1 确定性档位：只由工具包装层与 tool.outage 载荷消费，
    不改变既有重试语义；约束为 True 时 retryable 必为 False。
    """

    category: FailureCategory
    retryable: bool
    message: str
    deterministic: bool = False

    def __post_init__(self) -> None:
        if self.deterministic and self.retryable:
            raise ValueError("deterministic=True 时 retryable 必为 False")


FRIENDLY: dict[FailureCategory, str] = {
    "timeout": "模型服务响应超时，已自动重试后仍未成功；请稍后再试本轮。",
    "rate_limit": "模型服务当前限流，请稍后再试。",
    # 不声称压缩：该文案同时被嵌入/检索链路复用（直连检索无压缩步骤），
    # 只陈述事实与建议，不描述未发生的过程。
    "context_over_limit": "本轮上下文超出模型上限，无法继续本轮；请新建会话或精简问题。",
    "quota": "模型服务配额已用尽，请联系管理员补充额度。",
    "model_5xx": "模型服务暂时不可用，请稍后重试本轮。",
    "other": "本轮执行失败，记录已保留；可查看轨迹或继续本轮。",
}


def classify_failure(error: BaseException) -> FailureInfo:
    """把异常映射成类别与恢复策略；分类不抛异常，未知一律归 other。

    确定性档位按白名单叠加：只对 4xx 输入类/参数拒绝形态置位，类别与重试
    语义不变——4xx 白名单形态本就归 other（不可重试），档位只是显式标记。
    超限形态优先于白名单：400 正文可能同时携带输入拒绝与超限关键词，
    超限是单请求条件，不构成整轮熔断依据（审查 P2-1）。
    """
    category = _category(error)
    info = FailureInfo(
        category, category in {"timeout", "rate_limit", "model_5xx"}, FRIENDLY[category]
    )
    if (
        isinstance(error, ModelHTTPError)
        and category != "context_over_limit"
        and _is_deterministic(error)
    ):
        return FailureInfo(category, False, info.message, deterministic=True)
    return info


def _is_deterministic(error: ModelHTTPError) -> bool:
    """FR-1 白名单：4xx 输入类拒绝与参数确定性拒绝；上下文超限不在此列（硬失败）。

    调用方先按类别排除 context_over_limit：超限与输入拒绝关键词同现时
    以超限优先，不置确定性档；其余正文关键词小写比对，形态未命中
    白名单的 4xx 维持 other 档不做扩大。
    """
    if error.status_code not in DETERMINISTIC_STATUSES:
        return False
    lowered = error.body_excerpt.lower()
    return any(keyword in lowered for keyword in INPUT_INVALID_KEYWORDS) or any(
        keyword in lowered for keyword in PARAM_REJECTED_KEYWORDS
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
