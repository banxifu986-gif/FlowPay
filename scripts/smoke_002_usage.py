#!/usr/bin/env python3
"""002 冒烟驱动：真实模型跑六轮，逐轮核对用量契约（message.assistant usage）。

用法：容器内 `python scripts/smoke_002_usage.py [--url http://127.0.0.1:8000]`，
后端经 compose 起栈（见 specs/002 quickstart 手工冒烟记录）。
"""

from __future__ import annotations

import argparse
import time

import httpx
from flowpay.core.chat.types import ChatMessage, SessionView
from pydantic import TypeAdapter

from scripts.auth import AuthenticatedClient, add_arguments


class Arguments(argparse.Namespace):
    """驱动参数：后端地址与单轮等待上限。"""

    url: str = "http://127.0.0.1:8000"
    username: str = ""
    password: str = ""
    timeout: float = 300.0


MESSAGES = [
    "你好，你能做什么？",
    "Webhook 重试策略是什么？",
    "有笔支付成功了但一直没收到通知，帮我看看",
    "生产环境 acct_bg_001，支付编号 pay_bg_001_0003",
    "结合刚才的条件，帮我看看这笔支付为什么没收到通知",
    "谢谢",
]


def wait_terminal(client: httpx.Client, sid: str, timeout: float) -> SessionView:
    """轮询会话直到本轮终态；超过上限按超时失败处理。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = TypeAdapter(SessionView).validate_json(
            client.get(f"/api/sessions/{sid}").content
        )
        if view["status"] != "running":
            return view
        time.sleep(0.5)
    raise AssertionError("turn did not finish in time")


def main() -> None:
    """六轮消息逐一提交，断言每轮回答都携带用量契约。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=Arguments.url)
    parser.add_argument("--timeout", type=float, default=Arguments.timeout)
    add_arguments(parser)
    args = parser.parse_args(namespace=Arguments())
    with AuthenticatedClient(args.url, args.username, args.password) as client:
        created = client.post("/api/sessions", json={})
        created.raise_for_status()
        sid = TypeAdapter(SessionView).validate_json(created.content)["id"]
        print(f"session: {sid}")
        for index, text in enumerate(MESSAGES, start=1):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"request_id": f"smoke_{index}", "content": text},
            )
            response.raise_for_status()
            view = wait_terminal(client, sid, args.timeout)
            last: ChatMessage = view["messages"][-1]
            usage = last["usage"]
            print(f"\n=== [{index}] status={view['status']} usage={usage}")
            print("content head:", last["content"][:80].replace("\n", " "))
            if usage is None:
                raise AssertionError(f"轮 {index} 的回答缺 usage 契约")
            model_calls = usage.get("model_calls")
            if not isinstance(model_calls, int) or model_calls < 1:
                raise AssertionError(f"轮 {index} 用量 model_calls 异常: {usage}")
    print("\nall turns carry usage contract ✓")


if __name__ == "__main__":
    main()
