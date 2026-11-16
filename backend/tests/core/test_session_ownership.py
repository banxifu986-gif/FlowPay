"""会话归属与可见性回归（specs/005 US5）。

商户只见自己的会话；读取别人的会话按不存在处理，不泄漏存在性；内部看全部。
"""

from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from flowpay.core.auth import AuthError, Principal
from flowpay.core.chat.types import SessionSummary, SessionView
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.event_log.types import Event
from flowpay.core.execution.manager import SessionManager
from flowpay.core.http import create_app
from flowpay.core.json import require_object, require_text
from tests.core import auth_fixture
from tests.core.fixtures import FixtureWorkflow


def created_id(client: TestClient) -> str:
    """新建会话并取 id；不直接读 Any，避免类型边界被抹平。"""
    response = client.post("/api/sessions", json={})
    return require_text(TypeAdapter(SessionView).validate_json(response.content)["id"])


def listed_ids(client: TestClient) -> list[str]:
    """会话列表按主体过滤后的 id 顺序。"""
    response = client.get("/api/sessions")
    return [
        item["id"] for item in TypeAdapter(list[SessionSummary]).validate_json(response.content)
    ]


def test_merchant_cannot_read_or_delete_other_session(tmp_path: Path) -> None:
    """B 读 A 的会话（详情/事件/导出/SSE/删除）全部 404；内部账号可见。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        client.headers.update(auth_fixture.headers(auth, auth_fixture.MERCHANT.username))
        sid = created_id(client)
        view = TypeAdapter(SessionView).validate_json(client.get(f"/api/sessions/{sid}").content)
        assert view["user_id"] == auth_fixture.MERCHANT.username

        client.headers.update(auth_fixture.headers(auth, auth_fixture.MERCHANT_B.username))
        for suffix in ("", "/events", "/export", "/stream"):
            assert client.get(f"/api/sessions/{sid}{suffix}").status_code == 404
        assert client.delete(f"/api/sessions/{sid}").status_code == 404
        assert (
            client.post(
                f"/api/sessions/{sid}/messages", json={"request_id": "x", "content": "q"}
            ).status_code
            == 404
        )

        client.headers.update(auth_fixture.headers(auth, auth_fixture.INTERNAL.username))
        assert client.get(f"/api/sessions/{sid}").status_code == 200


def test_session_list_is_filtered_by_owner(tmp_path: Path) -> None:
    """列表按 user_id 收敛：商户各看各的，内部看全部；旧会话（无 user_id）不向商户展示。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        client.headers.update(auth_fixture.headers(auth, auth_fixture.MERCHANT.username))
        own = created_id(client)
        manager = cast(SessionManager, cast(FastAPI, client.app).state.manager)
        # 005 之前的历史会话没有 user_id：只有内部账号能看到。
        manager.store.append(
            "session_legacy",
            [("session.created", {"workflow": {"id": "fixture", "version": "1"}})],
        )

        assert listed_ids(client) == [own]

        client.headers.update(auth_fixture.headers(auth, auth_fixture.MERCHANT_B.username))
        assert listed_ids(client) == []

        client.headers.update(auth_fixture.headers(auth, auth_fixture.INTERNAL.username))
        assert set(listed_ids(client)) == {own, "session_legacy"}


def test_session_created_event_records_user(tmp_path: Path) -> None:
    """事件日志记 user 归属：session.created 的 user_id 是登录主体用户名。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        client.headers.update(auth_fixture.headers(auth, auth_fixture.MERCHANT.username))
        sid = created_id(client)
        events = TypeAdapter(list[Event]).validate_json(
            client.get(f"/api/sessions/{sid}/events").content
        )
        created = next(event for event in events if event["type"] == "session.created")
        assert require_text(require_object(created["payload"])["user_id"]) == (
            auth_fixture.MERCHANT.username
        )


async def test_manager_rejects_unbound_merchant(tmp_path: Path) -> None:
    """管理器边界兜底：商户主体没有绑定账户时拒绝创建会话（F5）。"""
    settings = Settings(tmp_path, tmp_path, mock_delay_ms=0)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"),
        FixtureWorkflow(settings),
        settings,
    )
    try:
        with pytest.raises(AuthError):
            await manager.create_session(Principal("merchant_demo", "merchant", None))
    finally:
        await manager.shutdown()
