"""无服务端会话表的签名令牌：stdlib HMAC-SHA256（specs/005 US1）。

令牌形如 ``base64url(payload).base64url(HMAC(payload))``，payload 是紧凑 JSON，含
主体、角色、绑定账户与过期时间。校验只看签名与 exp，不需要查询任何存储：
预置账号量级本来就不需要会话表，重启后令牌仍然有效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from flowpay.core.json import JsonObject, parse_json, require_int, require_object, require_text

from .contracts import AuthError, Principal


def _encode(raw: bytes) -> str:
    """无填充 base64url；URL 与请求头里都不需要转义。"""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: str) -> bytes:
    """补回填充后解码；非法字符按 ValueError 上抛，由调用方转成 AuthError。"""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _payload(principal: Principal, expires_at: datetime) -> JsonObject:
    """令牌载荷：用户名是会话归属的 user_id，内部账号的 account_id 为 null。"""
    return {
        "sub": principal.username,
        "role": principal.role,
        "account_id": principal.account_id,
        "exp": int(expires_at.timestamp()),
    }


def sign_token(
    principal: Principal,
    secret: str,
    ttl_seconds: int,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """签发令牌并返回它与过期时间；密钥为空、ttl 非正时拒绝签发。"""
    if not secret:
        raise ValueError("Token secret must not be empty")
    if ttl_seconds <= 0:
        raise ValueError("Token lifetime must be positive")
    issued = now or datetime.now(UTC)
    expires_at = issued + timedelta(seconds=ttl_seconds)
    payload = json.dumps(_payload(principal, expires_at), sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_encode(payload.encode())}.{_encode(signature)}", expires_at


def verify_token(token: str, secret: str, *, now: datetime | None = None) -> Principal:
    """校验签名与有效期并还原主体；任何问题都抛同一个 AuthError，不细分原因。"""
    if not secret:
        raise AuthError("Invalid token")
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
    except ValueError as error:
        raise AuthError("Invalid token") from error
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("Invalid token")
    try:
        record = require_object(parse_json(payload.decode()))
        username = require_text(record.get("sub"))
        role = require_text(record.get("role"))
        account_id = record.get("account_id")
        expires_at = require_int(record.get("exp"))
    except ValueError as error:
        raise AuthError("Invalid token") from error
    current = (now or datetime.now(UTC)).timestamp()
    if current >= expires_at:
        raise AuthError("Token expired", code="token_expired")
    if role not in ("merchant", "internal"):
        raise AuthError("Invalid token")
    # 商户必须绑定一个非空账户：空串会让 scope 注入退化成「没有边界」。
    if role == "merchant":
        if not isinstance(account_id, str) or not account_id.strip():
            raise AuthError("Invalid token")
    elif account_id is not None:
        raise AuthError("Invalid token")
    return Principal(
        username=username,
        role="merchant" if role == "merchant" else "internal",
        account_id=account_id if isinstance(account_id, str) else None,
    )
