"""口令哈希：只用 stdlib 的 pbkdf2_hmac（specs/005 US1，不新增依赖）。

编码格式 ``pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>``：迭代数写进记录，
日后调高默认值不会让既有账号失效。比较用 hmac.compare_digest，避免长度/前缀时间差。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 210000
SALT_BYTES = 16


def _decode(value: str) -> bytes:
    """按 base64url 解码，缺失填充补回；畸形输入按 ValueError 上抛。"""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(
    password: str, *, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None
) -> str:
    """把明文口令派生为可存盘的哈希记录；口令为空或迭代数非法时拒绝。"""
    if not password:
        raise ValueError("Password must not be empty")
    if iterations <= 0:
        raise ValueError("Iterations must be positive")
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """用记录里的参数重算派生值并做常量时间比较；记录畸形返回 False。"""
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != SCHEME:
        return False
    try:
        iterations = int(parts[1])
        salt = _decode(parts[2])
        expected = _decode(parts[3])
    except ValueError:
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)
