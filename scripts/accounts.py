#!/usr/bin/env python3
"""预置账号种子脚本：生成被 git 忽略的账号文件与令牌密钥（specs/005 US1）。

口令以 argv 传入，脚本只把 pbkdf2 派生值写进文件；密钥打印到标准输出由使用者填进
`.env`。不覆盖已有文件（换账号等于换数据边界，必须显式 --force）。
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flowpay.core.auth import hash_password

ROOT = Path(__file__).resolve().parents[1]


class Arguments(argparse.Namespace):
    """种子参数；两个角色的口令都必须显式给出。"""

    path: Path = ROOT / "backend/configs/accounts.toml"
    ttl_hours: int = 24
    merchant_username: str = "merchant_demo"
    merchant_account: str = ""
    merchant_password: str = ""
    internal_username: str = "internal_demo"
    internal_password: str = ""
    force: bool = False


def account_block(
    username: str, role: str, account_id: str | None, password: str
) -> str:
    """拼一条 [[accounts]] 记录；字符串一律用 JSON 引号，避免 TOML 转义差异。"""
    lines = ["[[accounts]]", f"username = {json.dumps(username, ensure_ascii=False)}"]
    lines.append(f"role = {json.dumps(role)}")
    if account_id is not None:
        lines.append(f"account_id = {json.dumps(account_id, ensure_ascii=False)}")
    lines.append(f"password_hash = {json.dumps(hash_password(password))}")
    return "\n".join(lines)


def render(args: Arguments) -> str:
    """生成完整文件内容；头部注释说明它为什么被忽略、由谁维护。"""
    header = [
        "# FlowPay 预置账号（specs/005）。本地文件，故意被 git 忽略：只存口令派生值。",
        "# 由 scripts/accounts.py seed 生成；重新生成会改变口令，需要同时更新使用方。",
        f"token_ttl_hours = {args.ttl_hours}",
        "",
    ]
    blocks = [
        account_block(
            args.merchant_username,
            "merchant",
            args.merchant_account,
            args.merchant_password,
        ),
        account_block(args.internal_username, "internal", None, args.internal_password),
    ]
    return "\n".join(header) + "\n\n".join(blocks) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["seed"], nargs="?", default="seed")
    parser.add_argument("--path", type=Path, default=Arguments.path)
    parser.add_argument("--ttl-hours", type=int, default=24)
    parser.add_argument("--merchant-username", default="merchant_demo")
    parser.add_argument(
        "--merchant-account", required=True, help="商户绑定的 account_id"
    )
    parser.add_argument("--merchant-password", required=True)
    parser.add_argument("--internal-username", default="internal_demo")
    parser.add_argument("--internal-password", required=True)
    parser.add_argument("--force", action="store_true", help="覆盖已有账号文件")
    args = parser.parse_args(namespace=Arguments())
    if not 1 <= args.ttl_hours <= 24 * 30:
        parser.error("--ttl-hours must be in 1..720")
    if args.path.exists() and not args.force:
        parser.error(f"{args.path} exists; pass --force to regenerate it")
    args.path.parent.mkdir(parents=True, exist_ok=True)
    args.path.write_text(render(args), encoding="utf-8")
    secret = secrets.token_urlsafe(48)
    print(f"accounts written: {args.path}")
    print("add this to .env (do not commit):")
    print(f"FLOWPAY_AUTH_TOKEN_SECRET={secret}")


if __name__ == "__main__":
    main()
