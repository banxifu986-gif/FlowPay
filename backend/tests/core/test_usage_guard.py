# 回归测试：用量守卫的日预算、并发与结构化 429（specs/006 US1）。
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.errors import QuotaExceeded
from flowpay.core.execution.manager import SessionManager
from flowpay.core.http import create_app
from flowpay.core.json import parse_json, require_object, require_text
from tests.core import auth_fixture
from tests.core.fixtures import FixtureWorkflow
from tests.core.principals import INTERNAL, MERCHANT


def merchant_manager(
    tmp_path: Path,
    *,
    daily: int = 1,
    internal_daily: int = 0,
    concurrent: int = 0,
    global_daily: int = 0,
    mock_delay: int = 0,
) -> SessionManager:
    """商户主体的管理器；默认关闭并发，便于单独验证日预算。"""
    settings = Settings(
        tmp_path,
        tmp_path / "dataset",
        quota_merchant_daily_turns=daily,
        quota_internal_daily_turns=internal_daily,
        quota_global_daily_turns=global_daily,
        quota_merchant_concurrency=concurrent,
        mock_delay_ms=mock_delay,
    )
    return SessionManager(
        EventStore(tmp_path / "sessions.sqlite"), FixtureWorkflow(settings), settings
    )


async def settle(manager: SessionManager, session_id: str) -> None:
    """等待本轮后台任务结束，让 run.started 落库后再计入下一次预算。"""
    task = manager.tasks.get(session_id)
    if task is not None:
        await task


async def test_merchant_daily_budget_rejects_next_turn(tmp_path: Path) -> None:
    """第一轮落库后日预算用尽；下一轮在提交前被 429 语义拒绝。"""
    manager = merchant_manager(tmp_path)
    first = (await manager.create_session(MERCHANT))["id"]
    await manager.submit_message(first, "one", "question", MERCHANT)
    await settle(manager, first)

    second = (await manager.create_session(MERCHANT))["id"]
    with pytest.raises(QuotaExceeded, match="预算") as error:
        await manager.submit_message(second, "two", "question", MERCHANT)
    assert error.value.code == "quota_exceeded"
    assert error.value.retry_after > 0


async def test_zero_limits_disable_guard(tmp_path: Path) -> None:
    """配置 0 关闭对应守卫；既有行为逐字不变。"""
    manager = merchant_manager(tmp_path, daily=0, global_daily=0)
    first = (await manager.create_session(MERCHANT))["id"]
    await manager.submit_message(first, "one", "hello", MERCHANT)
    await settle(manager, first)
    second = (await manager.create_session(MERCHANT))["id"]
    await manager.submit_message(second, "two", "hello", MERCHANT)
    await settle(manager, second)
    assert (await manager.get_session(second, MERCHANT))["status"] == "completed"


def test_http_returns_structured_429_for_concurrent_merchant(tmp_path: Path) -> None:
    """并发第二请求由 HTTP 层转成 detail/code 结构，与 401/403 形状一致。"""
    settings = Settings(
        tmp_path,
        tmp_path / "dataset",
        quota_merchant_concurrency=1,
        quota_merchant_daily_turns=0,
        quota_global_daily_turns=0,
        mock_delay_ms=500,
    )
    auth = auth_fixture.authenticator(tmp_path)
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        headers = auth_fixture.headers(auth, "merchant_demo")
        first = client.post("/api/sessions", json={}, headers=headers)
        second = client.post("/api/sessions", json={}, headers=headers)
        assert first.status_code == second.status_code == 201
        first_id = require_text(require_object(parse_json(first.text))["id"])
        second_id = require_text(require_object(parse_json(second.text))["id"])
        accepted = client.post(
            f"/api/sessions/{first_id}/messages",
            json={"request_id": "one", "content": "question"},
            headers=headers,
        )
        assert accepted.status_code == 202
        rejected = client.post(
            f"/api/sessions/{second_id}/messages",
            json={"request_id": "two", "content": "question"},
            headers=headers,
        )
        assert rejected.status_code == 429
        body = require_object(parse_json(rejected.text))
        assert require_text(body["code"]) == "concurrency_limit"
        assert "等待本轮结束" in require_text(body["detail"])
        assert rejected.headers["Retry-After"]
        client.post(f"/api/sessions/{first_id}/cancel", json={}, headers=headers)


async def test_internal_account_daily_budget_rejects_next_turn(tmp_path: Path) -> None:
    """内部账号个体日上限防止单账号吃满全局额度、锁死商户（F4）。"""
    manager = merchant_manager(tmp_path, daily=0, internal_daily=1)
    first = (await manager.create_session(INTERNAL))["id"]
    await manager.submit_message(first, "one", "hello", INTERNAL)
    await settle(manager, first)
    second = (await manager.create_session(INTERNAL))["id"]
    with pytest.raises(QuotaExceeded, match="内部账号") as error:
        await manager.submit_message(second, "two", "hello", INTERNAL)
    assert error.value.code == "quota_exceeded"
    assert error.value.retry_after > 0


async def test_internal_daily_limit_does_not_block_merchant(tmp_path: Path) -> None:
    """内部个体上限只作用于内部主体；商户仍按自己的日预算。"""
    manager = merchant_manager(tmp_path, daily=2, internal_daily=1)
    first = (await manager.create_session(MERCHANT))["id"]
    await manager.submit_message(first, "one", "hello", MERCHANT)
    await settle(manager, first)
    second = (await manager.create_session(MERCHANT))["id"]
    await manager.submit_message(second, "two", "hello", MERCHANT)
    await settle(manager, second)
    assert (await manager.get_session(second, MERCHANT))["status"] == "completed"
