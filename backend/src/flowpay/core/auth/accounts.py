"""预置账号文件解析：账号、角色与绑定账户（specs/005）。

文件由 `scripts/accounts.py` 生成、被 git 忽略，只存盐与派生值。这里只解析与校验结构，
不决定账号从哪来，也不做注册流程——范围内就是平台方维护的少量预置账号。
"""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import cast

from flowpay.core.json import JsonObject, require_object, require_text

from .contracts import Principal, Role
from .passwords import hash_password, verify_password


@dataclass(frozen=True)
class Account:
    """一条预置账号；商户必须有 account_id，内部账号必须没有。"""

    username: str
    role: Role
    account_id: str | None
    password_hash: str

    def principal(self) -> Principal:
        """还原成门禁与业务边界使用的登录主体。"""
        return Principal(self.username, self.role, self.account_id)


@dataclass(frozen=True)
class AccountPolicy:
    """账号集合与其令牌有效期；构造时校验唯一性、角色与账户绑定。"""

    accounts: tuple[Account, ...]
    token_ttl_hours: int = 24

    @classmethod
    def from_file(cls, path: Path) -> AccountPolicy:
        """读取指定 TOML 构造策略；文件缺失或结构非法时直接报错。"""
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        return cls.from_object(raw)

    @classmethod
    def from_object(cls, raw: JsonObject) -> AccountPolicy:
        """从已解析对象构造策略；未知字段一律拒绝，避免拼错字段被静默忽略。"""
        values = require_object(raw)
        allowed = {"accounts", "token_ttl_hours"}
        if unknown := set(values) - allowed:
            raise ValueError(f"Unknown accounts configuration fields: {sorted(unknown)}")
        entries = values.get("accounts")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Accounts configuration needs a non-empty accounts list")
        accounts = tuple(_account(entry) for entry in cast(list[object], entries))
        usernames = {account.username for account in accounts}
        if len(usernames) != len(accounts):
            raise ValueError("Duplicate account username")
        ttl = values.get("token_ttl_hours", 24)
        if type(ttl) is not int or not 1 <= ttl <= 24 * 30:
            raise ValueError("token_ttl_hours must be an integer in 1..720")
        return cls(accounts, ttl)

    def find(self, username: str) -> Account | None:
        """按用户名精确查找；找不到返回 None，不区分「不存在」与「口令错误」。"""
        return next((account for account in self.accounts if account.username == username), None)

    def login(self, username: str, password: str) -> Account | None:
        """校验用户名与口令；任意一项不符返回 None，调用方统一转成 401。

        用户名不存在时也照常跑一次派生：失败路径不做短接，响应时间不暴露账号是否存在。
        """
        account = self.find(username)
        if account is None:
            verify_password(password, _dummy_hash())
            return None
        if not verify_password(password, account.password_hash):
            return None
        return account


@cache
def _dummy_hash() -> str:
    """失败路径使用的等代价哈希记录；只在首次失败时计算一次。"""
    return hash_password(secrets.token_urlsafe(16))


def _account(value: object) -> Account:
    """校验单条账号记录；角色取值与 account_id 的必填关系在这里就拒绝。"""
    entry = require_object(value)
    allowed = {"username", "role", "account_id", "password_hash"}
    if unknown := set(entry) - allowed:
        raise ValueError(f"Unknown account fields: {sorted(unknown)}")
    username = require_text(entry.get("username"))
    role = require_text(entry.get("role"))
    password_hash = require_text(entry.get("password_hash"))
    if not username.strip():
        raise ValueError("Account username must not be blank")
    if role not in ("merchant", "internal"):
        raise ValueError(f"Invalid account role: {role}")
    account_id = entry.get("account_id")
    if role == "merchant":
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError(f"Merchant account needs account_id: {username}")
    elif account_id is not None:
        raise ValueError(f"Internal account must not bind account_id: {username}")
    if not password_hash.startswith("pbkdf2_sha256$"):
        raise ValueError(f"Account password_hash is not pbkdf2: {username}")
    return Account(
        username=username,
        role="merchant" if role == "merchant" else "internal",
        account_id=account_id if isinstance(account_id, str) else None,
        password_hash=password_hash,
    )
