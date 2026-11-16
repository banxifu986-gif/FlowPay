"""登录门禁回归：401/403 边界、令牌有效期与账号结构（specs/005 US1）。

固定输入只用于复现门禁行为，不构成业务答案来源。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpay.core.auth import (
    AccountPolicy,
    AuthError,
    Principal,
    hash_password,
    sign_token,
    verify_password,
    verify_token,
)
from flowpay.core.config import Settings
from flowpay.core.http import create_app
from flowpay.core.json import parse_json, require_object, require_text
from tests.core import auth_fixture
from tests.core.fixtures import FixtureWorkflow

PROTECTED: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("GET", "/api/health", {}),
    ("GET", "/api/meta", {}),
    ("GET", "/api/sessions", {}),
    ("POST", "/api/sessions", {}),
    ("POST", "/api/sessions/batch-delete", {"session_ids": ["session_x"]}),
    ("GET", "/api/sessions/session_x", {}),
    ("DELETE", "/api/sessions/session_x", {}),
    ("POST", "/api/sessions/session_x/messages", {"request_id": "r", "content": "q"}),
    ("POST", "/api/sessions/session_x/continue", {"request_id": "r"}),
    ("POST", "/api/sessions/session_x/cancel", {}),
    ("GET", "/api/sessions/session_x/events", {}),
    ("GET", "/api/sessions/session_x/export", {}),
    ("GET", "/api/sessions/session_x/stream", {}),
)


@pytest.mark.parametrize("method, path, body", PROTECTED)
def test_all_api_endpoints_require_token(
    tmp_path: Path, method: str, path: str, body: dict[str, object]
) -> None:
    """未登录请求从「可用」变为 401 是有意的行为变更（specs/005），SSE 同样门禁。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset")
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        response = client.request(method, path, json=body or None)
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_login_returns_identity_and_rejects_wrong_password(tmp_path: Path) -> None:
    """登录成功返回令牌与身份；口令错误、用户不存在都只有同一个 401。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset")
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "merchant_demo", "password": "merchant-password"},
        )
        assert response.status_code == 200
        body = require_object(parse_json(response.text))
        assert require_text(body["role"]) == "merchant"
        assert require_text(body["account_id"]) == auth_fixture.MERCHANT.account_id
        assert require_text(body["username"]) == "merchant_demo"
        token = require_text(body["token"])

        client.headers.update({"Authorization": f"Bearer {token}"})
        assert client.get("/api/meta").status_code == 200

        for credentials in (
            {"username": "merchant_demo", "password": "wrong-password"},
            {"username": "nobody", "password": "wrong-password"},
            {"username": "merchant_demo", "password": ""},
        ):
            failed = client.post("/api/auth/login", json=credentials)
            assert failed.status_code in {401, 422}
            if failed.status_code == 401:
                assert failed.json()["code"] == "unauthorized"


def test_expired_and_tampered_tokens_are_rejected(tmp_path: Path) -> None:
    """过期令牌与签名被改动的令牌都按 401 拒绝，不在端点内二次判断。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path / "dataset")
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        expired, _ = sign_token(
            auth_fixture.INTERNAL,
            auth_fixture.SECRET,
            300,
            now=datetime.now(UTC) - timedelta(seconds=600),
        )
        assert (
            client.get("/api/meta", headers={"Authorization": f"Bearer {expired}"}).status_code
            == 401
        )
        current = auth.login("internal_demo", "internal-password").token
        tampered = current[:-2] + ("AA" if not current.endswith("AA") else "BB")
        assert (
            client.get("/api/meta", headers={"Authorization": f"Bearer {tampered}"}).status_code
            == 401
        )
        assert client.get("/api/meta", headers={"Authorization": "Basic abc"}).status_code == 401


def test_demo_backend_stays_unauthenticated(tmp_path: Path) -> None:
    """契约假后端（authenticator=None）保持无门禁；登录接口明确不可用。"""
    settings = Settings(tmp_path, tmp_path / "dataset", mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings))) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/sessions").status_code == 200
        assert (
            client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code
            == 503
        )


def test_password_records_round_trip_and_reject_malformed_input() -> None:
    """口令记录含算法与迭代数；畸形记录只返回 False，不抛异常。"""
    encoded = hash_password("secret", iterations=1000)
    assert verify_password("secret", encoded)
    assert not verify_password("other", encoded)
    for broken in ("", "sha256$x$y$z", "pbkdf2_sha256$0$AA$AA", "pbkdf2_sha256$1000$AA$AA$BB"):
        assert not verify_password("secret", broken)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "accounts": [
                {"username": "m", "role": "merchant", "password_hash": "pbkdf2_sha256$x$y$z"}
            ]
        },
        {
            "accounts": [
                {
                    "username": "i",
                    "role": "internal",
                    "account_id": "acct_1",
                    "password_hash": "pbkdf2_sha256$x$y$z",
                }
            ]
        },
        {
            "accounts": [
                {
                    "username": "m",
                    "role": "merchant",
                    "account_id": "acct_1",
                    "password_hash": "pbkdf2_sha256$x$y$z",
                },
                {
                    "username": "m",
                    "role": "internal",
                    "password_hash": "pbkdf2_sha256$x$y$z",
                },
            ]
        },
        {"accounts": [], "typo": 1},
    ],
)
def test_account_policy_rejects_inconsistent_entries(payload: dict[str, object]) -> None:
    """商户缺账户、内部绑账户、用户名重复、未知字段都不能被静默接受。"""
    with pytest.raises(ValueError):
        AccountPolicy.from_object(payload)


def test_token_payload_carries_role_and_boundary() -> None:
    """令牌只需签名与有效期即可还原主体；场景化断言替代对内部的直接读取。"""
    token, expires_at = sign_token(
        Principal("merchant_demo", "merchant", "acct_demo"), auth_fixture.SECRET, 60
    )
    assert verify_token(token, auth_fixture.SECRET) == Principal(
        "merchant_demo", "merchant", "acct_demo"
    )
    assert expires_at > datetime.now(UTC)
    with pytest.raises(AuthError):
        verify_token(token, "another-secret")


def test_secret_name_is_recorded_but_value_is_not(tmp_path: Path) -> None:
    """配置快照只记账号文件路径与密钥变量名，避免把密钥写进事件日志。"""
    settings = Settings(tmp_path, tmp_path / "dataset", accounts_path=tmp_path / "accounts.toml")
    snapshot = settings.snapshot()
    assert snapshot["accounts_path"] == str(tmp_path / "accounts.toml")
    assert snapshot["auth_token_secret_env"] == "FLOWPAY_AUTH_TOKEN_SECRET"


def test_merchant_token_with_blank_account_is_rejected() -> None:
    """空串账户会让 scope 注入退化成无边界：令牌校验层直接拒绝（F5）。"""
    token, _ = sign_token(Principal("merchant_demo", "merchant", ""), auth_fixture.SECRET, 60)
    with pytest.raises(AuthError):
        verify_token(token, auth_fixture.SECRET)
    token, _ = sign_token(Principal("merchant_demo", "merchant", "   "), auth_fixture.SECRET, 60)
    with pytest.raises(AuthError):
        verify_token(token, auth_fixture.SECRET)
