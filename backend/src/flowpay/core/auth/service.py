"""认证能力的默认实现：账号文件提供主体，令牌用 stdlib 签名（specs/005 US1）。

不读环境变量：密钥与有效期由装配层（`flowpay/bootstrap.py`）传入，便于启动前校验
和测试注入。校验失败只抛 AuthError，具体原因不进入响应。
"""

from __future__ import annotations

from dataclasses import dataclass

from .accounts import AccountPolicy
from .contracts import AuthError, LoginResult, Principal
from .tokens import sign_token, verify_token


@dataclass(frozen=True)
class TokenAuthenticator:
    """账号文件 + HMAC 密钥的认证实现；无服务端会话表，重启后令牌仍有效。"""

    accounts: AccountPolicy
    secret: str

    def login(self, username: str, password: str) -> LoginResult:
        """校验口令后签发令牌；失败统一按 401 处理，不区分账号是否存在。"""
        account = self.accounts.login(username, password)
        if account is None:
            raise AuthError("Invalid username or password")
        principal = account.principal()
        token, expires_at = sign_token(principal, self.secret, self.accounts.token_ttl_hours * 3600)
        return LoginResult(token, principal, expires_at)

    def authenticate(self, token: str) -> Principal:
        """校验签名与有效期；错误由 verify_token 抛 AuthError。"""
        return verify_token(token, self.secret)
