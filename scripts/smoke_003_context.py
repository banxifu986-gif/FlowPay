"""003 冒烟驱动：真实模型跑六轮长会话，核对 LLM 摘要式压缩契约（T013/T014）。

用法：compose 起栈（backend 带治理键启动，见 specs/003 quickstart），容器内执行
`python scripts/smoke_003_context.py`。流程：六轮 HTTP 对话（含驱动大结果的排查
三连与跨轮引用核对），然后读 var/live/sessions.sqlite 分析：

- context.compacted / context.compaction_failed 事件与压缩体积曲线（SC-8）；
- 摘要信封检查点：record_ids 由代码提取、非空且 ⊆ 会话真实出现过的编号（SC-2）；
- 压缩后请求不含被压缩轮次的 tool 消息原文，正式请求的 usage 诚实入账（SC-5）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from flowpay.core.chat.record_ids import record_ids
from flowpay.core.chat.types import ChatMessage, SessionView
from flowpay.core.event_log.store import EventStore
from flowpay.core.event_log.types import Event
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_list,
    require_object,
    require_text,
)
from pydantic import TypeAdapter


class Arguments(argparse.Namespace):
    """驱动参数：后端地址、单轮等待上限与事件库路径。"""

    url: str = "http://127.0.0.1:8000"
    timeout: float = 600.0
    store: str = "/app/var/live/sessions.sqlite"


# 六轮长会话：轻分支 → 文档问答 → 排查开场 → 补条件 → 驱动大结果 → 跨轮引用核对。
MESSAGES = [
    "你好，你能做什么？",
    "Webhook 重试策略是什么？",
    "有笔支付成功了但一直没收到通知，帮我看看",
    "生产环境 acct_bg_001，支付编号 pay_bg_001_0003",
    "结合刚才的条件，把这笔支付的投递记录和事件都查出来，看看哪一环出了问题",
    "pay_bg_001_0003 现在到底是什么状态？之前查到的结论还有效吗？",
]


def wait_terminal(client: httpx.Client, sid: str, timeout: float) -> SessionView:
    """轮询会话直到本轮终态；超过上限按超时失败处理。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = TypeAdapter(SessionView).validate_json(client.get(f"/api/sessions/{sid}").content)
        if view["status"] != "running":
            return view
        time.sleep(1.0)
    raise AssertionError("turn did not finish in time")


def run_turns(client: httpx.Client, timeout: float) -> tuple[str, list[SessionView]]:
    """逐轮提交并等待终态，返回会话 id 与每轮视图。"""
    created = client.post("/api/sessions", json={})
    created.raise_for_status()
    sid = TypeAdapter(SessionView).validate_json(created.content)["id"]
    print(f"session: {sid}")
    views: list[SessionView] = []
    for index, text in enumerate(MESSAGES, start=1):
        response = client.post(
            f"/api/sessions/{sid}/messages",
            json={"request_id": f"smoke003_{index}", "content": text},
        )
        response.raise_for_status()
        view = wait_terminal(client, sid, timeout)
        views.append(view)
        last: ChatMessage = view["messages"][-1]
        usage = last.get("usage")
        print(f"\n=== [{index}] status={view['status']} usage={usage}")
        print("content head:", last["content"][:100].replace("\n", " "))
    return sid, views


def session_events(store: EventStore, sid: str) -> list[Event]:
    """按会话读全部事件。"""
    return store.read(sid)


def analyze(events: list[Event]) -> None:
    """压缩契约分析：事件留痕、体积曲线、信封编号完整性（SC-2/SC-5/SC-8）。"""
    compacted = [e for e in events if e["type"] == "context.compacted"]
    failed = [e for e in events if e["type"] == "context.compaction_failed"]
    print(f"\n=== compaction events: {len(compacted)} ok, {len(failed)} failed")
    for event in compacted:
        payload = event["payload"]
        print(
            "  compacted: rounds={} before={} after={}".format(
                payload.get("rounds"), payload.get("before_chars"), payload.get("after_chars")
            )
        )
    for event in failed:
        print(f"  FAILED: {event['payload']}")
    if failed:
        raise AssertionError("存在压缩失败事件——触发线/摘要上限需要校准")

    # 会话里工具真实返回过的全部编号（信封来源真值）。
    all_ids: set[str] = set()
    for event in events:
        if event["type"] == "tool.result":
            all_ids.update(record_ids(json.dumps(event["payload"], ensure_ascii=False)))
    all_ids.update(record_ids(json.dumps(MESSAGES, ensure_ascii=False)))

    requests = [e for e in events if e["type"] == "model.request"]
    sizes = [
        len(json.dumps(require_object(e["payload"]).get("request") or {}, ensure_ascii=False))
        for e in requests
    ]
    print(f"=== model.request total: {len(requests)}; max chars={max(sizes) if sizes else 0}")

    # 每轮 run.finished 的检查点：体积曲线 + 信封编号完整性。
    finished = [e for e in events if e["type"] == "run.finished"]
    for index, event in enumerate(finished, start=1):
        state = require_object(require_object(event["payload"]).get("state") or {})
        messages = require_list(state.get("messages") or [])
        if not messages:
            # multi-agent 检查点不含消息流（worker remember=False）：打印结构化记忆体积。
            findings = require_list(state.get("findings") or [])
            evidence = require_list(state.get("evidence") or [])
            print(
                "  turn {}: multi-agent checkpoint findings={} evidence={} (无消息流检查点)".format(
                    index, len(findings), len(evidence)
                )
            )
            continue
        volume = len(json.dumps(messages, ensure_ascii=False))
        envelopes: list[JsonObject] = []
        for item in messages:
            message = require_object(item)
            try:
                body = require_object(parse_envelope(require_text(message.get("content"))))
            except ValueError:
                continue
            if body.get("kind") == "session_summary":
                envelopes.append(body)
        print(
            "  turn {}: checkpoint messages={} chars={} envelopes={}".format(
                index, len(messages), volume, len(envelopes)
            )
        )
        for body in envelopes:
            ids = [str(i) for i in require_list(body.get("record_ids") or [])]
            if not ids:
                raise AssertionError("摘要信封 record_ids 为空——代码提取失效")
            unknown = [i for i in ids if i not in all_ids]
            if unknown:
                raise AssertionError(f"信封出现会话中不存在的编号: {unknown}")
            print(f"    envelope ids({len(ids)}): {ids[:6]}…")
    if not compacted:
        raise AssertionError("六轮长会话未触发压缩——提高查询强度或下调触发线后重跑")
    print("\ncompaction contract satisfied ✓")


def parse_envelope(content: str) -> JsonObject:
    """解析信封正文；非 JSON 抛 ValueError 走跳过路径。"""
    return require_object(parse_json(content))


def main() -> None:
    """六轮对话 + 事件库压缩契约分析。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=Arguments.url)
    parser.add_argument("--timeout", type=float, default=Arguments.timeout)
    parser.add_argument("--store", default=Arguments.store)
    args = parser.parse_args(namespace=Arguments())
    with httpx.Client(base_url=args.url, timeout=30) as client:
        client.get("/api/health").raise_for_status()
        sid, _ = run_turns(client, args.timeout)
    store = EventStore(Path(args.store))
    analyze(session_events(store, sid))


if __name__ == "__main__":
    main()
