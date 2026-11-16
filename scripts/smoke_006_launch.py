#!/usr/bin/env python3
"""006 内网全链路冒烟：登录 → 提问 → SSE → 反馈（specs/006 US6）。

对已在运行的后端执行；容器内：
`python scripts/smoke_006_launch.py --username internal_demo --password <口令>`。
不实现反向代理，只验证应用层全链路可用。
"""

from __future__ import annotations

import argparse
import json
import time

import httpx
from flowpay.core.chat.types import SessionView
from flowpay.core.event_log.types import Event
from pydantic import TypeAdapter

from scripts.auth import AuthenticatedClient, add_arguments

# 轻问题走能力介绍，不触发长链路排查；反馈仍针对真实轮次。
QUESTION = "你好，你能做什么？"


class Arguments(argparse.Namespace):
    """驱动参数：后端地址、登录凭据与等待上限。"""

    url: str = "http://127.0.0.1:8000"
    username: str = ""
    password: str = ""
    timeout: float = 300.0


def wait_terminal(client: httpx.Client, session_id: str, timeout: float) -> SessionView:
    """轮询会话直到终态；SSE 只验证事件流，不负责推断任务生命周期。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = TypeAdapter(SessionView).validate_json(
            client.get(f"/api/sessions/{session_id}").content
        )
        if view["status"] != "running":
            return view
        time.sleep(0.25)
    raise AssertionError("turn did not finish in time")


def read_stream(client: httpx.Client, session_id: str, timeout: float) -> list[Event]:
    """读取一次 SSE 直到本轮终态，返回看到的事件；超时按失败处理。"""
    events: list[Event] = []
    deadline = time.monotonic() + timeout
    with client.stream("GET", f"/api/sessions/{session_id}/stream?after=0") as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise AssertionError("SSE did not reach a terminal run event in time")
            if not line.startswith("data: "):
                continue
            event = TypeAdapter(Event).validate_json(line[6:])
            events.append(event)
            if event["type"] in {"run.finished", "run.failed", "run.canceled"}:
                break
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=Arguments.url)
    parser.add_argument("--timeout", type=float, default=Arguments.timeout)
    add_arguments(parser)
    args = parser.parse_args(namespace=Arguments())

    with AuthenticatedClient(args.url, args.username, args.password) as client:
        created = client.post("/api/sessions", json={})
        created.raise_for_status()
        session_id = TypeAdapter(SessionView).validate_json(created.content)["id"]

        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"request_id": "smoke006_turn", "content": QUESTION},
        )
        response.raise_for_status()
        events = read_stream(client, session_id, args.timeout)
        started = [event for event in events if event["type"] == "run.started"]
        assert started, "SSE never delivered run.started"
        run_id = started[-1]["run_id"]
        assert run_id
        view = wait_terminal(client, session_id, args.timeout)
        assert view["status"] in {"completed", "waiting_user"}, view["status"]

        feedback = client.post(
            f"/api/sessions/{session_id}/feedback",
            json={"run_id": run_id, "rating": "up", "comment": "冒烟反馈"},
        )
        feedback.raise_for_status()
        # 重复提交幂等：同一 (会话,轮次,主体) 不追加第二条事件（specs/006 F5）。
        duplicate = client.post(
            f"/api/sessions/{session_id}/feedback",
            json={"run_id": run_id, "rating": "down"},
        )
        duplicate.raise_for_status()
        stored = TypeAdapter(list[Event]).validate_json(
            client.get(f"/api/sessions/{session_id}/events").content
        )
        feedback_events = [
            event for event in stored if event["type"] == "feedback.received"
        ]
        assert len(feedback_events) == 1, feedback_events
        assert feedback_events[0]["payload"]["rating"] == "up"
        # 反馈只进事件日志：会话消息投影里不应出现反馈正文。
        assert all(
            "冒烟反馈" not in str(message.get("content"))
            for message in view["messages"]
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "session_id": session_id,
                    "run_id": run_id,
                    "checks": ["login", "session", "turn", "sse", "feedback"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
