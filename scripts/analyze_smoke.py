#!/usr/bin/env python3
"""冒烟事件分析：按轮聚合 model.request 体积与 usage，供投影阈值校准。

用法：容器内 `python scripts/analyze_smoke.py <事件库目录> [session id]`。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from flowpay.core.json import JsonObject, parse_json, require_object

DIR = sys.argv[1] if len(sys.argv) > 1 else "/app/var/smoke-002-off"
SID = sys.argv[2] if len(sys.argv) > 2 else None


@dataclass
class RunStats:
    """单轮（run）的请求与用量统计。"""

    requests: int = 0
    max_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def request_chars(payload: JsonObject) -> int:
    """单次 model.request 的消息序列近似体积。"""
    request = payload.get("request")
    if not isinstance(request, dict):
        return 0
    body = cast("JsonObject", request).get("body")
    if not isinstance(body, dict):
        return 0
    return len(
        json.dumps(cast("JsonObject", body).get("messages", []), ensure_ascii=False)
    )


def response_tokens(payload: JsonObject) -> tuple[int, int]:
    """单次 model.response 的 prompt/completion tokens；缺失按 0 计（对账只求和真实值）。"""
    completion = payload.get("completion")
    if not isinstance(completion, dict):
        return 0, 0
    usage = cast("JsonObject", completion).get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    usage = cast("JsonObject", usage)
    prompt = usage.get("prompt_tokens")
    answer = usage.get("completion_tokens")
    return (
        prompt if isinstance(prompt, int) else 0,
        answer if isinstance(answer, int) else 0,
    )


def main() -> None:
    """扫描事件库，输出每轮请求数、体积峰值与 usage 对账。"""
    db_files = sorted(Path(DIR).glob("*.sqlite"))
    if not db_files:
        raise SystemExit(f"no sqlite under {DIR}")
    conn = sqlite3.connect(db_files[0])
    tables = [
        cast("str", row[0])
        for row in cast(
            "list[tuple[object]]",
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall(),
        )
    ]
    table = "events" if "events" in tables else tables[0]
    sessions: dict[str, dict[str, RunStats]] = {}
    rows = cast(
        "list[tuple[object]]", conn.execute(f"SELECT data FROM {table}").fetchall()
    )
    for row in rows:
        data = cast("str", row[0])
        event = require_object(parse_json(data))
        sid = event.get("session_id")
        if SID and sid != SID:
            continue
        run_id = str(event.get("run_id") or "?")
        bucket = sessions.setdefault(str(sid), {})
        stats = bucket.setdefault(run_id, RunStats())
        payload = cast("JsonObject", event.get("payload") or {})
        kind = event.get("type")
        if kind == "model.request":
            stats.requests += 1
            stats.max_chars = max(stats.max_chars, request_chars(payload))
        elif kind == "model.response":
            prompt, answer = response_tokens(payload)
            stats.input_tokens += prompt
            stats.output_tokens += answer
    for sid, bucket in sessions.items():
        print(f"session: {sid}")
        for run_id, stats in bucket.items():
            print(
                f"  run {run_id[:12]} requests={stats.requests:>2} max_single={stats.max_chars:>6} "
                + f"| events_usage in={stats.input_tokens:>6} out={stats.output_tokens:>5}"
            )


if __name__ == "__main__":
    main()
