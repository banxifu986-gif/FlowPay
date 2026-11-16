"""执行请求的状态、版本、用量守卫与幂等冲突。"""

from __future__ import annotations


class Conflict(Exception):
    """请求与当前会话状态、版本或幂等标识冲突。"""


class QuotaExceeded(Exception):
    """用量守卫拒绝新回合；HTTP 层映射为结构化 429。"""

    def __init__(self, detail: str, *, code: str = "quota_exceeded", retry_after: int = 0) -> None:
        """保存所有超限共用的响应字段；retry_after 秒为 0 时不下发响应头。"""
        self.detail: str = detail
        self.code: str = code
        self.retry_after: int = retry_after
        super().__init__(detail)
