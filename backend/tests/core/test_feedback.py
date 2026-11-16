# 回归测试：反馈 append-only 且不进入对话上下文（specs/006 US5）。
from pathlib import Path

import pytest

from flowpay.core.auth import Principal
from flowpay.core.chat.errors import SessionNotFound
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.errors import Conflict
from flowpay.core.execution.manager import SessionManager
from tests.core.fixtures import FixtureWorkflow
from tests.core.principals import INTERNAL

OTHER_INTERNAL = Principal(username="test_internal_two", role="internal")


async def settle(manager: SessionManager, session_id: str) -> None:
    task = manager.tasks.get(session_id)
    if task is not None:
        await task


async def test_feedback_is_logged_and_never_enters_messages(tmp_path: Path) -> None:
    """反馈事件可查询；下一轮消息与检查点不含反馈正文。"""
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), FixtureWorkflow(settings), settings
    )
    sid = (await manager.create_session(INTERNAL))["id"]
    await manager.submit_message(sid, "one", "question", INTERNAL)
    await settle(manager, sid)
    run_id = (await manager.get_session(sid, INTERNAL))["runs"][-1]["id"]

    await manager.record_feedback(sid, run_id, "down", "附言不应进上下文", INTERNAL)
    events = manager.store.read(sid)
    feedback = [e for e in events if e["type"] == "feedback.received"]
    assert feedback and feedback[0]["payload"]["comment"] == "附言不应进上下文"
    assert feedback[0]["payload"]["rating"] == "down"

    view = await manager.get_session(sid, INTERNAL)
    assert all("附言不应进上下文" not in message["content"] for message in view["messages"])
    await manager.submit_message(sid, "two", "question", INTERNAL)
    await settle(manager, sid)
    finished = [e for e in manager.store.read(sid) if e["type"] == "run.finished"]
    assert "附言不应进上下文" not in str(finished[-1]["payload"])


async def test_feedback_is_idempotent_per_subject_and_allows_other_subject(
    tmp_path: Path,
) -> None:
    """同一 (会话,轮次,主体) 只追加一次；另一主体仍可反馈（F5）。"""
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), FixtureWorkflow(settings), settings
    )
    sid = (await manager.create_session(INTERNAL))["id"]
    await manager.submit_message(sid, "one", "hello", INTERNAL)
    await settle(manager, sid)
    run_id = (await manager.get_session(sid, INTERNAL))["runs"][-1]["id"]

    await manager.record_feedback(sid, run_id, "up", None, INTERNAL)
    await manager.record_feedback(sid, run_id, "down", "重复不应追加", INTERNAL)
    await manager.record_feedback(sid, run_id, "up", None, OTHER_INTERNAL)
    feedback = [event for event in manager.store.read(sid) if event["type"] == "feedback.received"]
    assert len(feedback) == 2
    assert {event["payload"]["user"] for event in feedback} == {
        INTERNAL.username,
        OTHER_INTERNAL.username,
    }
    assert [event["payload"]["rating"] for event in feedback] == ["up", "up"]


async def test_feedback_rejects_running_turn_and_unknown_run(tmp_path: Path) -> None:
    """运行中的轮次不接受反馈；不存在的 run_id 按冲突拒绝。"""
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=500)
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), FixtureWorkflow(settings), settings
    )
    sid = (await manager.create_session(INTERNAL))["id"]
    await manager.submit_message(sid, "one", "hello", INTERNAL)
    run_id = (await manager.get_session(sid, INTERNAL))["runs"][-1]["id"]
    with pytest.raises(Conflict, match="after the turn finishes"):
        await manager.record_feedback(sid, run_id, "up", None, INTERNAL)
    await settle(manager, sid)
    with pytest.raises(Conflict, match="not part of this session"):
        await manager.record_feedback(sid, "run_missing", "up", None, INTERNAL)


async def test_feedback_cannot_target_other_merchants_session(tmp_path: Path) -> None:
    """商户只能评价自己的会话；他人会话按不存在处理，不泄漏存在性。"""
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    manager = SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), FixtureWorkflow(settings), settings
    )
    from tests.core.principals import MERCHANT

    owner = (await manager.create_session(INTERNAL))["id"]
    await manager.submit_message(owner, "one", "hello", INTERNAL)
    await settle(manager, owner)
    run_id = (await manager.get_session(owner, INTERNAL))["runs"][-1]["id"]
    with pytest.raises(SessionNotFound):
        await manager.record_feedback(owner, run_id, "up", None, MERCHANT)
