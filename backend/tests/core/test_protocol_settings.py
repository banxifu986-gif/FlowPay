"""ModelSettings.protocol 配置：值域校验、保留字分流、环境覆盖。

环境覆盖测试走完整链路（apply_environment_overrides → build_settings），
与 load_config 的实际执行顺序一致——build_settings 本身不做环境覆盖。
"""

from typing import cast

import pytest

from flowpay.core.config import ModelSettings
from flowpay.core.config.loader import apply_environment_overrides, build_settings
from flowpay.core.json import JsonObject


def test_protocol_defaults_to_chat_completions() -> None:
    """未配置协议时按现状走 chat/completions，回滚零改动。"""
    assert ModelSettings().protocol == "chat_completions"


def test_protocol_accepts_responses_value() -> None:
    """responses 是合法协议值。"""
    assert ModelSettings(provider="openai-compatible", protocol="responses").protocol == (
        "responses"
    )


def test_protocol_rejects_unknown_value() -> None:
    """未知协议启动即败，不静默降级。"""
    with pytest.raises(ValueError, match="protocol"):
        ModelSettings(provider="openai-compatible", protocol="grpc")


def test_loader_overrides_protocol_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLOWPAY_MODEL_PROTOCOL 环境变量覆盖配置文件值。"""
    monkeypatch.setenv("FLOWPAY_MODEL_PROTOCOL", "responses")
    raw = {
        "data_dir": "./state",
        "dataset_path": "./dataset",
        "model": {"provider": "openai-compatible", "name": "fixture"},
    }
    config = cast("JsonObject", raw)
    assert build_settings(apply_environment_overrides(config)).model.protocol == "responses"


def test_loader_rejects_invalid_protocol_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量给了非法协议值时启动即败。"""
    monkeypatch.setenv("FLOWPAY_MODEL_PROTOCOL", "websocket")
    raw = {
        "data_dir": "./state",
        "dataset_path": "./dataset",
        "model": {"provider": "openai-compatible", "name": "fixture"},
    }
    with pytest.raises(ValueError):
        build_settings(apply_environment_overrides(cast("JsonObject", raw)))
