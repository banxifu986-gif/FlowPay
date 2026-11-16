"""HTTP 用例的登录夹具：固定账号、固定密钥、固定口令，避免每个用例各写一份。

口令只存在于测试代码里；账号文件写在 tmp_path，用例结束后随目录清理。
"""

from __future__ import annotations

import json
from pathlib import Path

from flowpay.core.auth import AccountPolicy, Principal, TokenAuthenticator, hash_password

SECRET = "test-token-secret"
MERCHANT = Principal(username="merchant_demo", role="merchant", account_id="acct_merchant_demo")
MERCHANT_B = Principal(username="merchant_two", role="merchant", account_id="acct_merchant_two")
INTERNAL = Principal(username="internal_demo", role="internal")
PASSWORDS = {
    "merchant_demo": "merchant-password",
    "merchant_two": "merchant-two-password",
    "internal_demo": "internal-password",
}


def accounts_file(directory: Path) -> Path:
    """按固定口令写出账号文件；内容与 scripts/accounts.py 生成的结构一致。"""
    blocks: list[str] = []
    for principal in (MERCHANT, MERCHANT_B, INTERNAL):
        lines = [
            "[[accounts]]",
            f"username = {json.dumps(principal.username)}",
            f"role = {json.dumps(principal.role)}",
        ]
        if principal.account_id is not None:
            lines.append(f"account_id = {json.dumps(principal.account_id)}")
        lines.append(f"password_hash = {json.dumps(hash_password(PASSWORDS[principal.username]))}")
        blocks.append("\n".join(lines))
    path = directory / "accounts.toml"
    path.write_text("\n\n".join(("token_ttl_hours = 24", *blocks)) + "\n", encoding="utf-8")
    return path


def authenticator(directory: Path) -> TokenAuthenticator:
    """装配测试用认证实现；密钥固定，因此用例可以自己签发过期令牌。"""
    return TokenAuthenticator(AccountPolicy.from_file(accounts_file(directory)), SECRET)


def headers(auth: TokenAuthenticator, username: str = INTERNAL.username) -> dict[str, str]:
    """登录后返回请求头；默认内部账号（可见全部、不注入 scope、记忆关闭）。"""
    token = auth.login(username, PASSWORDS[username]).token
    return {"Authorization": f"Bearer {token}"}
