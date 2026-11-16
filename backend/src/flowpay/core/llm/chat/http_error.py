"""供应商 HTTP 错误的类型化封装与上下文超限识别（specs/004）。

openai_chat 把 SDK 的 APIStatusError 转成本类型再上抛：调用方（turn 的
reactive 补救路径）据此识别「上下文超限」形态，其他错误形态不受影响。
本模块不导入任何 SDK，避免把供应商实现拖进通用层。
"""

from __future__ import annotations

# 正文摘要长度：够覆盖主流供应商的超限报错关键词即可，不拖长异常消息。
BODY_EXCERPT_CHARS = 400

# 上下文超限的宽松关键词（小写比对）：漏判=不做补救（维持现状无害），
# 误判=一次多余压缩加重试（同样无害，specs/004 spec.md）。
OVER_LIMIT_KEYWORDS = ("context_length", "context length", "maximum context", "上下文长度")


class ModelHTTPError(RuntimeError):
    """供应商返回的 HTTP 错误；携带状态码与正文摘要。

    消息保持「Model HTTP {status}」前缀：日志与旧口径的区分只看类型与属性，
    正文摘要附在后面供人工排查。
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code: int = status_code
        self.body_excerpt: str = body[:BODY_EXCERPT_CHARS]
        super().__init__(f"Model HTTP {status_code} ({self.body_excerpt})")


def is_context_over_limit(error: Exception) -> bool:
    """判断一次模型调用失败是否为上下文超限形态：400 且正文命中关键词。"""
    if not isinstance(error, ModelHTTPError) or error.status_code != 400:
        return False
    lowered = error.body_excerpt.lower()
    return any(keyword in lowered for keyword in OVER_LIMIT_KEYWORDS)
