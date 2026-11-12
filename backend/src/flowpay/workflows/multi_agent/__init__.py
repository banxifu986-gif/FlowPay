"""multi-agent 阶段：意图路由 + 并行协作排查的轮内编排。

LangGraph 只负责一轮内部的节点编排（路由、调度、汇总）；跨轮持久化继续走
saved_state 通道，图状态在轮末组装为检查点，轮内不引入 checkpointer。
"""

from .graph import MultiAgentWorkflow

__all__ = ["MultiAgentWorkflow"]
