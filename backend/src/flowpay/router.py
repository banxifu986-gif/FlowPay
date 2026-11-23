"""阶段入口；只选择阶段，不执行检索策略或 Agent Loop。"""

from collections.abc import Callable

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.execution.contracts import Workflow
from flowpay.core.llm.chat import ChatModel
from flowpay.core.memory.contracts import MemoryPolicy
from flowpay.workflows.manual_loop import ManualLoopWorkflow
from flowpay.workflows.multi_agent import MultiAgentWorkflow
from flowpay.workflows.multi_agent.assertion_gate import AssertionGate

# 各阶段构造签名不完全一致（multi-agent 独有 max_concurrency 与
# investigation_tool_budget），别名用可变参数承载：阶段特定参数只在
# create_stage 的转发分支出现，不进统一契约。
StageFactory = Callable[..., Workflow]

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
    memory_policy: MemoryPolicy | None = None,
    investigation_tool_budget: int = 16,
    assertion_gate: AssertionGate | None = None,
) -> Workflow:
    """将装配好的能力交给所选阶段；各阶段保持独立的 run 实现和会话身份。

    memory_policy 为 None 表示跨会话记忆关闭，工作流不发生任何召回调用。
    investigation_tool_budget 是单任务工具调用上限（specs/009 FR-9），仅
    multi-agent 阶段消费，其余阶段不接收。assertion_gate 是 010 断言校验门
    （specs/010 审查 P1-2），仅 multi-agent 阶段消费。
    """
    stage = resolve_stage(name)
    factory = STAGES[stage]
    if factory is MultiAgentWorkflow:
        return factory(
            model,
            prepare_query,
            context_policy,
            memory_policy,
            investigation_tool_budget=investigation_tool_budget,
            assertion_gate=assertion_gate,
        )
    return factory(model, prepare_query, context_policy, memory_policy)
