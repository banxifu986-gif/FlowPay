"""一次性统计：事件库 tool.started 按运行分组统计单任务调用分布（specs/009 T008）。

R96B 等历史事件库没有任务级归属，脚本用两个口径近似：
- 单轮计数：session+run 的 tool.started 总数（上界代理）；
- 单任务均值：从 plan 的 model.response 解析 set_tasks 任务数 n，单轮计数 ÷ n
  （各任务用量不均时是近似值；无 plan 调用的轮按 1 个任务计）。
输出分位数供 investigation_tool_budget 定档，结果记入 settings 注释。

用法：python scripts/stats_tool_calls.py <sessions.sqlite> [...]
"""

import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from flowpay.core.json import (
    parse_json,
    require_list,
    require_object,
    require_text,
)


def percentiles(values: list[float], marks: tuple[float, ...]) -> dict[float, float]:
    """按最近秩法取分位数；空列表返回全零，避免除零。"""
    if not values:
        return {mark: 0 for mark in marks}
    ordered = sorted(values)
    return {mark: ordered[min(len(ordered) - 1, int(mark / 100 * len(ordered)))] for mark in marks}


def plan_task_count(payload: object) -> int | None:
    """从 model.response 载荷解析 plan 调用的任务数；无 set_tasks 返回 None。"""
    try:
        completion = require_object(require_object(payload)["completion"])
        choices = require_list(completion["choices"])
        message = require_object(require_object(choices[0])["message"])
        calls = require_list(message["tool_calls"])
    except (KeyError, ValueError):
        return None
    for raw in calls:
        call = require_object(raw)
        function = require_object(call["function"])
        if require_text(function["name"]) != "set_tasks":
            continue
        try:
            arguments = require_object(parse_json(require_text(function["arguments"])))
            return max(1, len(require_list(arguments.get("tasks") or [])))
        except (KeyError, ValueError):
            return None
    return None


def collect(paths: Sequence[str]) -> tuple[list[int], list[float]]:
    """按事件库逐个统计：返回（单轮计数列表、单任务均值列表）。"""
    per_run: list[int] = []
    per_task: list[float] = []
    for raw in paths:
        db = sqlite3.connect(f"file:{Path(raw)}?mode=ro", uri=True)
        rows: list[tuple[object, ...]] = list(
            db.execute(
                "SELECT type, data, session_id, run_id FROM events ORDER BY session_id, seq"
            )
        )
        db.close()
        runs: dict[tuple[str, str], dict[str, int]] = {}
        for kind, data, session_id, run_id in rows:
            if run_id is None:
                continue
            key = (require_text(session_id), require_text(run_id))
            stats = runs.setdefault(key, {"tools": 0, "tasks": 0})
            if require_text(kind) == "tool.started":
                stats["tools"] += 1
            elif require_text(kind) == "model.response":
                count = plan_task_count(require_object(parse_json(require_text(data)))["payload"])
                if count is not None:
                    stats["tasks"] = count
        for stats in runs.values():
            if stats["tools"]:
                per_run.append(stats["tools"])
                per_task.append(stats["tools"] / (stats["tasks"] or 1))
    return per_run, per_task


def main(paths: Sequence[str]) -> None:
    per_run, per_task = collect(paths)
    marks = (50, 90, 95, 99, 100)
    print(f"runs_with_tools={len(per_run)} total_calls={sum(per_run)}")
    per_run_view = {f"p{m:g}": v for m, v in percentiles([float(v) for v in per_run], marks).items()}
    per_task_view = {f"p{m:g}": round(v, 1) for m, v in percentiles(per_task, marks).items()}
    print("单轮口径：", per_run_view)
    print("单任务均值口径：", per_task_view)
    print(f"单任务 max={max(per_task) if per_task else 0}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
