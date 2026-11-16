"""登录主体的公开类型、错误与认证能力接口（specs/005）。

core 只认「用户名 + 角色 + 绑定账户」这一层：账号来自本地配置文件，令牌用 stdlib
签名，不含服务端会话表。业务层（HTTP 门禁、会话、工具边界）只读取主体，不自己解析令牌。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

Role = Literal["merchant", "internal"]

# 未认证（401）与已认证但越权（403）共用一种错误：响应体只有 detail 和 code，
# 不区分「账号不存在」与「口令错误」，避免把账号枚举成可观测差异。
UNAUTHORIZED = 401
FORBIDDEN = 403


class AuthError(Exception):
    """认证或授权失败；HTTP 层按 status_code 与 code 转成结构化错误体。"""

    def __init__(self, detail: str, *, status_code: int = UNAUTHORIZED, code: str = "unauthorized"):
        super().__init__(detail)
        self.detail: str = detail
        self.status_code: int = status_code
        self.code: str = code


@dataclass(frozen=True)
class Principal:
    """一次已认证的登录主体。

    商户绑死 account_id，数据边界由服务端据此注入；内部账号不绑账户，看全部会话与
    文档。username 同时作为会话归属的 user_id。
    """

    username: str
    role: Role
    account_id: str | None = None

    @property
    def is_merchant(self) -> bool:
        """商户主体需要服务端强制数据边界；内部主体照旧使用对话条件。"""
        return self.role == "merchant"


@dataclass(frozen=True)
class LoginResult:
    """登录成功的令牌、过期时间与主体；前端据此展示身份与角色。"""

    token: str
    principal: Principal
    expires_at: datetime


class Authenticator(Protocol):
    """门禁需要的两项能力：换取令牌与校验令牌。实现方负责口令与签名细节。"""

    def login(self, username: str, password: str) -> LoginResult:
        """校验用户名与口令并签发令牌；失败抛 AuthError，不区分具体原因。"""
        ...

    def authenticate(self, token: str) -> Principal:
        """校验令牌签名与有效期并返回主体；任何问题抛 AuthError。"""
        ...
