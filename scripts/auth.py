#!/usr/bin/env python3
"""脚本侧登录 helper：HTTP 验收用内部账号登录，进程内基准用固定内部主体（specs/005 US7）。

demo 契约假后端不门禁，探测 health 后无令牌即跳过登录；真实服务则先换令牌再发请求。
"""

from __future__ import annotations

import argparse
import os

import httpx
from flowpay.core.auth import Principal
from flowpay.core.json import parse_json, require_object, require_text

# 进程内跑基准与知识准备时使用的主体：内部账号看全部数据，记忆关闭，与 300 题基线同口径。
INTERNAL = Principal(username="internal_scripts", role="internal")

DEFAULT_USERNAME = "internal_demo"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """给已有脚本补上登录参数；口令也可以从环境变量给，避免进命令行历史。"""
    parser.add_argument(
        "--username", default=os.environ.get("FLOWPAY_USER", DEFAULT_USERNAME)
    )
    parser.add_argument("--password", default=os.environ.get("FLOWPAY_PASSWORD", ""))


def login(client: httpx.Client, username: str, password: str) -> str:
    """用账号口令换取 Bearer 令牌，并写入客户端默认请求头。"""
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    token = require_text(require_object(parse_json(response.text))["token"])
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def authenticate(client: httpx.Client, username: str, password: str) -> bool:
    """需要时登录：未门禁的假后端直接返回 False，门禁后端返回 True。"""
    probe = client.get("/api/health")
    if probe.status_code != 401:
        probe.raise_for_status()
        return False
    if not password:
        raise SystemExit(
            "This backend requires authentication; pass --password or FLOWPAY_PASSWORD"
        )
    login(client, username, password)
    return True


class AuthenticatedClient:
    """with 语句里拿到已按需登录的客户端；退出时关闭连接。

    用类而不是 @contextmanager：返回类型显式，静态检查不需要理解装饰器的协程包装。
    """

    def __init__(self, url: str, username: str, password: str) -> None:
        self._url: str = url
        self._username: str = username
        self._password: str = password
        self._client: httpx.Client | None = None

    def __enter__(self) -> httpx.Client:
        self._client = httpx.Client(base_url=self._url, timeout=30)
        authenticate(self._client, self._username, self._password)
        return self._client

    def __exit__(self, *_exc: object) -> None:
        if self._client is not None:
            self._client.close()
