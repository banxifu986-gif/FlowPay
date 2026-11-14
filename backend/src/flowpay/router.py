"""阶段入口；只选择阶段，不执行检索策略或 Agent Loop。"""

from collections.abc import Callable

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.execution.contracts import Workflow
from flowpay.core.llm.chat import ChatModel
from flowpay.workflows.manual_loop import ManualLoopWorkflow
from flowpay.workflows.multi_agent import MultiAgentWorkflow

StageFactory = Callable[[ChatModel, Callable[[str], str], ContextPolicy | None], Workflow]

# 阶段按名字指代编排方式；不设并列的变体入口。
STAGES: dict[str, StageFactory] = {
    "manual-loop": ManualLoopWorkflow,
    "multi-agent": MultiAgentWorkflow,
}


def resolve_stage(name: str) -> str:
    """检查阶段已实现；未知名称直接报错。"""
    if name in STAGES:
        return name
    raise ValueError(f"Unknown stage: {name}; available: {', '.join(STAGES)}")


def create_stage(
    name: str,
    model: ChatModel,
    prepare_query: Callable[[str], str],
    context_policy: ContextPolicy | None = None,
) -> Workflow:
    """将装配好的能力交给所选阶段；各阶段保持独立的 run 实现和会话身份。"""
    return STAGES[resolve_stage(name)](model, prepare_query, context_policy)
