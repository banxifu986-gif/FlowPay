"""脚本登录 helper 与账号种子脚本（specs/005 US1/US7）。"""

import sys
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from scripts.accounts import Arguments as SeedArguments
from scripts.accounts import main as seed_main
from scripts.auth import authenticate

from flowpay.core.auth import AccountPolicy
from flowpay.core.config import Settings
from flowpay.core.http import create_app
from tests.core import auth_fixture
from tests.core.fixtures import FixtureWorkflow


def http_client(test_client: TestClient) -> httpx.Client:
    """TestClient 与 httpx.Client 形状一致（本环境是两个包）；双击 cast 是项目通行做法。"""
    return cast(httpx.Client, cast(object, test_client))


def test_authenticate_skips_demo_backend_without_password(tmp_path: Path) -> None:
    """契约假后端不门禁：探测成功即不登录，口令留空也不报错。"""
    settings = Settings(tmp_path, tmp_path, mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings))) as client:
        assert authenticate(http_client(client), "anyone", "") is False


def test_authenticate_logs_in_when_gated(tmp_path: Path) -> None:
    """门禁后端：先换令牌再发请求；缺少口令时明确失败而不是继续跑空请求。"""
    auth = auth_fixture.authenticator(tmp_path)
    settings = Settings(tmp_path, tmp_path, mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        assert authenticate(http_client(client), "internal_demo", "internal-password") is True
        assert client.get("/api/meta").status_code == 200
        assert authenticate(http_client(client), "internal_demo", "internal-password") is False
    with TestClient(create_app(settings, FixtureWorkflow(settings), auth)) as client:
        with pytest.raises(SystemExit):
            authenticate(http_client(client), "internal_demo", "")


def test_seed_writes_accounts_that_policy_can_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """种子脚本生成的文件能被账号策略直接解析；已存在的文件不覆盖。"""
    path = tmp_path / "accounts.toml"
    argv = [
        "accounts.py",
        "seed",
        "--path",
        str(path),
        "--merchant-account",
        "acct_seed",
        "--merchant-password",
        "merchant-pw",
        "--internal-password",
        "internal-pw",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    seed_main()
    policy = AccountPolicy.from_file(path)
    account = policy.login("merchant_demo", "merchant-pw")
    assert account is not None and account.account_id == "acct_seed"
    assert policy.login("internal_demo", "merchant-pw") is None

    with pytest.raises(SystemExit):
        seed_main()
    assert AccountPolicy.from_file(path).login("merchant_demo", "merchant-pw") is not None
    assert SeedArguments.path  # 默认路径存在，调用方不必显式传 --path


def test_bootstrap_fails_fast_without_accounts_or_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部署缺账号文件或密钥时启动即失败，不会以无门禁形态运行（F2）。"""
    from flowpay.bootstrap import create_authenticator

    with pytest.raises(ValueError, match="accounts_path"):
        create_authenticator(Settings(tmp_path, tmp_path))
    missing = Settings(tmp_path, tmp_path, accounts_path=tmp_path / "accounts.toml")
    with pytest.raises(ValueError, match="accounts_path"):
        create_authenticator(missing)

    auth_fixture.accounts_file(tmp_path)
    monkeypatch.delenv("FLOWPAY_AUTH_TOKEN_SECRET", raising=False)
    configured = Settings(tmp_path, tmp_path, accounts_path=tmp_path / "accounts.toml")
    with pytest.raises(ValueError, match="Missing environment variable"):
        create_authenticator(configured)
    monkeypatch.setenv("FLOWPAY_AUTH_TOKEN_SECRET", "deploy-secret")
    authenticator = create_authenticator(configured)
    assert authenticator.login("merchant_demo", "merchant-password").principal.account_id
