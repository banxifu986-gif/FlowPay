"""当前阶段入口；不启动服务或调用真实模型。"""

from dataclasses import replace
from pathlib import Path

import pytest

from flowpay.bootstrap import create_workflow
from flowpay.cli import Arguments, configured_runtime, main
from flowpay.core.config import load_settings
from flowpay.router import resolve_stage
from flowpay.workflows.manual_loop import ManualLoopWorkflow
from flowpay.workflows.multi_agent import MultiAgentWorkflow

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "backend/configs/local.toml"


def test_stage_overrides_environment_and_keeps_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式阶段优先于环境，保留模型和向量配置。"""
    monkeypatch.setenv("FLOWPAY_WORKFLOW", "manual-loop")
    settings = configured_runtime(Arguments(config=CONFIG, stage="manual-loop"))
    original = load_settings(CONFIG)
    assert settings.workflow == "manual-loop"
    assert settings.embedding == original.embedding
    assert settings.model == original.model
    assert settings.knowledge_collection_id == original.knowledge_collection_id
    for key in (settings.model.api_key_env, settings.knowledge_url_env):
        monkeypatch.setenv(key, "fixture")
    assert settings.embedding is not None
    monkeypatch.setenv(settings.embedding.api_key_env, "fixture")
    workflow = create_workflow(settings)
    assert isinstance(workflow, ManualLoopWorkflow)
    assert (workflow.id, workflow.version) == ("flowpay-multi-turn-rag", "1")


@pytest.mark.parametrize("selection", ["manual-loop", "multi-agent"])
def test_environment_stage_selection(monkeypatch: pytest.MonkeyPatch, selection: str) -> None:
    """环境变量使用当前注册阶段名。"""
    monkeypatch.setenv("FLOWPAY_WORKFLOW", selection)
    assert configured_runtime(Arguments(config=CONFIG)).workflow == selection


def test_multi_agent_is_the_default_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """multi-agent 是默认阶段；环境变量可配置回 manual-loop（FR-13）。"""
    monkeypatch.delenv("FLOWPAY_WORKFLOW", raising=False)
    assert configured_runtime(Arguments(config=CONFIG)).workflow == "multi-agent"


@pytest.mark.parametrize("stage", ["manual-loop", "multi-agent"])
def test_business_stages_require_an_access_policy_before_starting(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """用业务数据源的阶段缺少访问策略时在启动前失败，不跑到一半才发现。"""
    settings = configured_runtime(Arguments(config=CONFIG, stage=stage))
    assert settings.embedding is not None
    for key in (
        settings.model.api_key_env,
        settings.embedding.api_key_env,
        settings.knowledge_url_env,
    ):
        monkeypatch.setenv(key, "fixture")
    with pytest.raises(ValueError, match="sources_config"):
        create_workflow(replace(settings, sources_config=None))


@pytest.mark.parametrize(
    "stage, message",
    [
        ("unknown", "Unknown stage"),
        ("demo", "Unknown stage"),
        ("live", "Unknown stage"),
        ("rag", "Unknown stage"),
    ],
)
def test_invalid_or_retired_entry_is_explicit(stage: str, message: str) -> None:
    """拼写错误和旧演示模式提供明确错误，不伪装成正常运行。"""
    with pytest.raises(ValueError, match=message):
        resolve_stage(stage)


def test_removed_workflow_argument_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令行只提供 --stage，旧参数在装配外部依赖之前拒绝。"""
    monkeypatch.setattr("sys.argv", ["flowpay", "--workflow", "rag"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "stage, expected",
    [("manual-loop", ManualLoopWorkflow), ("multi-agent", MultiAgentWorkflow)],
)
def test_stage_is_assembled_with_shared_connections(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected: type[ManualLoopWorkflow | MultiAgentWorkflow],
) -> None:
    """按阶段名装配，复用现有模型、向量与知识配置。"""
    settings = configured_runtime(Arguments(config=CONFIG, stage=stage))
    assert settings.embedding is not None
    for key in (
        settings.model.api_key_env,
        settings.embedding.api_key_env,
        settings.knowledge_url_env,
    ):
        monkeypatch.setenv(key, "fixture")
    assert isinstance(create_workflow(settings), expected)
