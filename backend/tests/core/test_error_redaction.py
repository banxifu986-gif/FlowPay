"""事件日志脱敏：会话事件只放异常类别，完整异常进服务端日志（specs/005 F3）。"""

import logging
from pathlib import Path

import pytest

from flowpay.core.agent.trace import Tracer
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.stage import StageCard
from flowpay.core.json import JsonObject, require_text
from flowpay.core.tools import Tool, Toolbox
from tests.core.principals import INTERNAL

# 模拟驱动/数据源层抛出的错误文本：含账号、主机与库名，商户可读的事件里不能出现。
SENSITIVE = "postgresql://flowpay:secret@db.internal/flowpay_knowledge"


async def test_tool_error_event_keeps_only_error_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """工具内部故障：事件只有类别，完整异常（含连接串）只进服务端日志。"""
    store = EventStore(tmp_path / "trace.sqlite")
    store.append("s", [("session.created", {"workflow": {"id": "w", "version": "1"}})])
    tracer = Tracer(store, "s", "run")

    async def explode(_: JsonObject) -> JsonObject:
        raise RuntimeError(SENSITIVE)

    toolbox = Toolbox([Tool("broken", "", {"type": "object", "properties": {}}, explode)], tracer)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        await toolbox.execute("broken", {}, "call_1")
    events = store.read("s")
    error = next(event for event in events if event["type"] == "tool.error")
    assert error["payload"] == {"name": "broken", "error_type": "RuntimeError"}
    assert "postgresql://" not in str(events)
    assert "db.internal" not in str(events)
    # 完整异常仍然可查：服务端日志（含 traceback）保留原文。
    assert SENSITIVE in caplog.text


class ExplodingWorkflow:
    """直接抛异常的一轮：用于 run.failed 载荷。"""

    id: str = "exploding"
    version: str = "1"
    card: StageCard = StageCard(label="exploding", description="脱敏回归")

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        del turn, runtime
        raise RuntimeError(SENSITIVE)


async def test_run_failed_event_hides_internal_detail(tmp_path: Path) -> None:
    """运行失败：run.failed 只记类别；商户读会话事件看不到 DSN。"""
    settings = Settings(tmp_path, tmp_path)
    manager = SessionManager(EventStore(tmp_path / "events.sqlite"), ExplodingWorkflow(), settings)
    try:
        sid = require_text((await manager.create_session(INTERNAL))["id"])
        await manager.submit_message(sid, "one", "question", INTERNAL)
        await manager.tasks[sid]
        events = manager.store.read(sid)
    finally:
        await manager.shutdown()
    failed = next(event for event in events if event["type"] == "run.failed")
    # specs/006：终态增加分类与友好文案；异常类别仍是唯一来自异常本身的信息。
    assert failed["payload"]["error_type"] == "RuntimeError"
    assert failed["payload"]["category"] == "other"
    assert failed["payload"]["message"]
    assert "postgresql://" not in str(events)
    assert "db.internal" not in str(events)
