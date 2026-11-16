"""写入会话事件的安全异常摘要。

会话事件对商户可读，异常正文常带 DSN、主机、SQL 语句或文件路径；事件载荷只放
异常类别，完整异常由调用点在写事件前用 logger.exception 记到服务端日志。
"""

from flowpay.core.json import JsonObject


def error_summary(error: BaseException) -> JsonObject:
    """把异常降级为稳定摘要；调用方不得把 str(error) 直接写进事件载荷。"""
    return {"error_type": type(error).__name__}
