"""004 冒烟驱动：真实模型跑跨会话记忆链路（specs/004 US3/US8）。

用法：compose 起栈（backend 带记忆键启动，见 specs/004 quickstart.md），容器内
执行 `python scripts/smoke_004_memory.py`。阶段：

- A 写入会话：完成一轮带编号的排查，断言 memory.extracted（结论落 memory.sqlite）；
- B 召回会话：新会话提交含同一支付编号的消息，断言 model.request 出现记忆话术
  （「一律以本轮为准」）且 memory.recalled matched=key；
- C 语义兜底：键未命中的同症状问法——semantic_limit>0 时断言 matched=semantic；
- D reactive compact：小预算长会话诱导 400 后核对 context.reactive_compacted 载荷。

退出码非 0 表示契约被破坏；阶段 D 未触发只打印提示（需要专门的小预算配置，不阻塞）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx
from flowpay.core.chat.types import SessionView
from flowpay.core.event_log.store import EventStore
from flowpay.core.event_log.types import Event
from flowpay.core.json import require_list, require_object, require_text
from pydantic import TypeAdapter


class Arguments(argparse.Namespace):
    """驱动参数：后端地址、单轮等待上限与事件库路径。"""

    url: str = "http://127.0.0.1:8000"
    timeout: float = 600.0
    store: str = "/app/var/live/sessions.sqlite"


# 阶段 A：一轮完整的排查（规则短路直达 sql 调查，产生可抽取的结论）。
TURN_A = "生产环境 acct_bg_001，支付 pay_bg_001_0003 成功了但一直没收到通知，帮我看看"
# 阶段 B：新会话，同一支付编号——结构化键应精确命中阶段 A 的记忆。
TURN_B = "pay_bg_001_0003 这笔支付之前查到过什么结论？"
# 阶段 C：同症状但无编号——键必然未命中，semantic_limit>0 时走语义兜底。
TURN_C = "有笔支付成功了但一直没收到通知，之前遇到过类似的问题吗？"


def wait_terminal(client: httpx.Client, sid: str, timeout: float) -> SessionView:
    """轮询会话直到本轮终态；超过上限按超时失败处理。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = TypeAdapter(SessionView).validate_json(
            client.get(f"/api/sessions/{sid}").content
        )
        if view["status"] != "running":
            return view
        time.sleep(1.0)
    raise AssertionError("turn did not finish in time")


def run_turn(
    client: httpx.Client, sid: str, request_id: str, text: str, timeout: float
) -> SessionView:
    """提交一轮消息并等待终态。"""
    response = client.post(
        f"/api/sessions/{sid}/messages",
        json={"request_id": request_id, "content": text},
    )
    response.raise_for_status()
    return wait_terminal(client, sid, timeout)


def events_of(store: EventStore, sid: str) -> list[Event]:
    """按会话读全部事件。"""
    return store.read(sid)


TERMINAL_MEMORY_EVENTS = (
    "memory.extracted",
    "memory.extract_skipped",
    "memory.extract_failed",
)


def wait_for_memory_events(
    store: EventStore, sid: str, timeout: float = 180.0
) -> list[Event]:
    """等待会话出现抽取收尾事件后返回全部事件。

    memory.recalled 在轮次开始就会落库，不能作为等待目标；抽取是 run.finished
    之后的收尾步骤，嵌入调用在 ollama CPU 上实测 20-35 秒（specs/004 quickstart
    校准数据），必须轮询到 extracted/skipped/failed 之一再断言。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = store.read(sid)
        if any(event["type"] in TERMINAL_MEMORY_EVENTS for event in events):
            return events
        time.sleep(2.0)
    return store.read(sid)


def new_session(client: httpx.Client) -> str:
    """创建会话并返回 id。"""
    created = client.post("/api/sessions", json={})
    created.raise_for_status()
    return require_text(TypeAdapter(SessionView).validate_json(created.content)["id"])


def request_bodies(events: list[Event]) -> list[str]:
    """收集全部 model.request 的请求正文 JSON，供注入断言。"""
    bodies: list[str] = []
    for event in events:
        if event["type"] == "model.request":
            bodies.append(
                json.dumps(require_object(event["payload"]), ensure_ascii=False)
            )
    return bodies


def payload_of(events: list[Event], kind: str) -> list[dict[str, object]]:
    """取全部指定类型事件的载荷。"""
    return [
        require_object(event["payload"]) for event in events if event["type"] == kind
    ]


def stage_a(client: httpx.Client, store: EventStore, timeout: float) -> str:
    """写入会话：完成排查后必须出现 memory.extracted；返回会话 id。"""
    print("\n=== A 写入会话")
    sid = new_session(client)
    view = run_turn(client, sid, "smoke004_a1", TURN_A, timeout)
    print(f"status={view['status']}")
    events = wait_for_memory_events(store, sid)
    extracted = payload_of(events, "memory.extracted")
    skipped = payload_of(events, "memory.extract_skipped")
    failed = payload_of(events, "memory.extract_failed")
    if failed:
        raise AssertionError(f"memory.extract_failed: {failed}")
    if not extracted:
        raise AssertionError(
            f"未抽取到记忆（skipped={skipped}）——检查存储条件或 data_dir 挂载"
        )
    print(f"memory.extracted: {extracted[0]}")
    return sid


def stage_b(client: httpx.Client, store: EventStore, timeout: float) -> str:
    """召回会话：同编号新会话必须键命中并注入记忆话术；返回会话 id。"""
    print("\n=== B 召回会话（结构化键）")
    sid = new_session(client)
    view = run_turn(client, sid, "smoke004_b1", TURN_B, timeout)
    print(f"status={view['status']}")
    events = wait_for_memory_events(store, sid)
    recalled = payload_of(events, "memory.recalled")
    if not recalled:
        raise AssertionError("没有 memory.recalled 事件——记忆键未启用或召回未执行")
    matched = require_text(recalled[0]["matched"])
    print(
        f"memory.recalled matched={matched} note_ids={require_list(recalled[0]['note_ids'])}"
    )
    if matched != "key":
        raise AssertionError(
            f"键未命中（matched={matched}）——阶段 A 的编号没有进记忆键集"
        )
    bodies = request_bodies(events)
    hit = [body for body in bodies if "一律以本轮为准" in body]
    if not hit:
        raise AssertionError("model.request 里没有记忆话术——注入段落缺失")
    print(f"注入话术出现在 {len(hit)}/{len(bodies)} 个请求中 ✓")
    return sid


def stage_c(client: httpx.Client, store: EventStore, timeout: float) -> str:
    """语义兜底：键未命中的同症状问法，按配置核对 matched；返回会话 id。"""
    print("\n=== C 语义兜底（semantic_limit>0 时）")
    sid = new_session(client)
    view = run_turn(client, sid, "smoke004_c1", TURN_C, timeout)
    print(f"status={view['status']}")
    recalled = payload_of(wait_for_memory_events(store, sid), "memory.recalled")
    if not recalled:
        raise AssertionError("没有 memory.recalled 事件")
    matched = require_text(recalled[0]["matched"])
    print(f"memory.recalled matched={matched}")
    limit = int(os.environ.get("FLOWPAY_MEMORY_SEMANTIC_LIMIT", "0") or 0)
    if limit > 0 and matched != "semantic":
        raise AssertionError(
            f"semantic_limit={limit} 但 matched={matched}——阈值或嵌入链路需校准"
        )
    if limit == 0:
        print("semantic_limit=0：键路径未命中即止，符合缺省口径 ✓")
    return sid


def stage_d(store: EventStore, sessions: list[str]) -> None:
    """reactive compact：核对事件载荷（需按 quickstart 配小预算诱导 400）。"""
    print("\n=== D reactive compact")
    reactive = [
        payload
        for sid in sessions
        for payload in payload_of(events_of(store, sid), "context.reactive_compacted")
    ]
    if not reactive:
        hint = (
            "未触发：需要按 quickstart.md 配置很小的 FLOWPAY_CONTEXT_CHAR_BUDGET 长会话诱导 400，"
            "该场景单测（test_reactive_compact）已覆盖各分支，不阻塞本冒烟。"
        )
        print(hint)
        return
    for payload in reactive:
        print(f"context.reactive_compacted: {payload}")


def main() -> None:
    """四阶段冒烟：写入 → 键召回 → 语义兜底 → reactive 核对。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=Arguments.url)
    parser.add_argument("--timeout", type=float, default=Arguments.timeout)
    parser.add_argument("--store", default=Arguments.store)
    args = parser.parse_args(namespace=Arguments())
    with httpx.Client(base_url=args.url, timeout=30) as client:
        client.get("/api/health").raise_for_status()
        store = EventStore(Path(args.store))
        sessions = [
            stage_a(client, store, args.timeout),
            stage_b(client, store, args.timeout),
            stage_c(client, store, args.timeout),
        ]
        stage_d(store, sessions)
    print("\n004 memory contract satisfied ✓")


if __name__ == "__main__":
    main()
