"""认证与授权内核；HTTP 门禁、会话归属与数据边界共用同一套主体类型。"""

from .accounts import Account, AccountPolicy
from .contracts import (
    FORBIDDEN,
    UNAUTHORIZED,
    Authenticator,
    AuthError,
    LoginResult,
    Principal,
    Role,
)
from .passwords import DEFAULT_ITERATIONS, hash_password, verify_password
from .service import TokenAuthenticator
from .tokens import sign_token, verify_token

__all__ = [
    "DEFAULT_ITERATIONS",
    "FORBIDDEN",
    "UNAUTHORIZED",
    "Account",
    "AccountPolicy",
    "AuthError",
    "Authenticator",
    "LoginResult",
    "Principal",
    "Role",
    "TokenAuthenticator",
    "hash_password",
    "sign_token",
    "verify_password",
    "verify_token",
]
