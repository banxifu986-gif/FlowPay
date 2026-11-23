# 回归测试：逐断言校验的配置面（specs/010 T013）。固定输入复现边界，不作为业务答案来源。
from pathlib import Path

import pytest

from flowpay.core.config.loader import apply_environment_overrides, build_settings, load_settings
from flowpay.core.config.settings import Settings
from flowpay.core.json import JsonObject

BACKEND_DIR = Path(__file__).resolve().parents[2]


def minimal_raw(**extra: object) -> JsonObject:
    """最小合法配置（路径字段必填），供 loader 级用例追加字段。"""
    return {
        "data_dir": "./state",
        "dataset_path": "./dataset",
        **extra,
    }


def test_defaults_off_and_8000() -> None:
    """默认关闭 + 默认预算 8000：开关关保证 B0-B2 期间生产路径零变化（SC-1）。"""
    settings = build_settings(minimal_raw())
    assert settings.assertion_audit_enabled is False
    assert settings.assertion_audit_evidence_budget == 8000


def test_toml_values_loaded() -> None:
    """local.toml 两个键可装载。"""
    settings = build_settings(
        minimal_raw(assertion_audit_enabled=True, assertion_audit_evidence_budget=6000)
    )
    assert settings.assertion_audit_enabled is True
    assert settings.assertion_audit_evidence_budget == 6000


def test_fields_excluded_from_environment_override_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密钥无关字段不入环境覆盖通道：FLOWPAY_ASSERTION_AUDIT_* 不生效。"""
    monkeypatch.setenv("FLOWPAY_ASSERTION_AUDIT_ENABLED", "true")
    monkeypatch.setenv("FLOWPAY_ASSERTION_AUDIT_EVIDENCE_BUDGET", "999")
    settings = build_settings(apply_environment_overrides(minimal_raw()))
    assert settings.assertion_audit_enabled is False
    assert settings.assertion_audit_evidence_budget == 8000


def test_unknown_assertion_field_rejected() -> None:
    """配置文件里的未知键依旧启动即败，不静默吞掉。"""
    with pytest.raises(ValueError, match="Unknown configuration fields"):
        build_settings(apply_environment_overrides(minimal_raw(assertion_audit_enable=True)))


def test_snapshot_carries_plain_values() -> None:
    """运行快照含两字段且为普通值（密钥无关，不涉脱敏）。"""
    snapshot = Settings(data_dir=Path("./s"), dataset_path=Path("./d")).snapshot()
    assert snapshot["assertion_audit_enabled"] is False
    assert snapshot["assertion_audit_evidence_budget"] == 8000


def test_local_toml_ships_the_two_keys() -> None:
    """仓库 local.toml 实际携带两键且默认关闭、预算 8000。"""
    settings = load_settings(BACKEND_DIR / "configs" / "local.toml")
    assert settings.assertion_audit_enabled is False
    assert settings.assertion_audit_evidence_budget == 8000
