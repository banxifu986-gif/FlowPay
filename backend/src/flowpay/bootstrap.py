"""装配运行需要的具体客户端；阶段选择交给 router，运行资源统一管理连接。"""

import os

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.config import Settings
from flowpay.core.execution.contracts import Workflow
from flowpay.core.llm.chat.openai_chat import OpenAiChatModel
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.router import create_stage, resolve_stage


def create_workflow(settings: Settings) -> Workflow:
    """核对当前阶段的模型和知识配置，注入客户端；缺少依赖时在服务启动前失败。"""
    stage = resolve_stage(settings.workflow)
    if settings.model.provider != "openai-compatible":
        raise ValueError("RAG requires an openai-compatible chat model")
    if not settings.knowledge_collection_id or settings.embedding is None:
        raise ValueError("RAG requires a ready knowledge collection and embedding configuration")
    if settings.embedding.name != "qwen3-embedding:0.6b":
        raise ValueError("Current query formatting expects qwen3-embedding:0.6b")
    # 用业务工具的阶段直接读 dataset；缺少访问策略时在服务启动前失败，而不是运行到一半。
    if stage in {"manual-loop", "multi-agent"} and settings.sources_config is None:
        raise ValueError(f"Stage {stage} requires sources_config pointing at the access policy")
    for key in (
        settings.model.api_key_env,
        settings.embedding.api_key_env,
        settings.knowledge_url_env,
    ):
        if not os.environ.get(key):
            raise ValueError(f"Missing environment variable: {key}")
    # 两项预算都 > 0 才启用请求前投影；缺省 0 = 关闭 = 现行为。
    context_policy = (
        ContextPolicy(
            char_budget=settings.context_char_budget,
            tool_result_budget=settings.context_tool_result_budget,
        )
        if settings.context_char_budget > 0 and settings.context_tool_result_budget > 0
        else None
    )
    return create_stage(stage, OpenAiChatModel(settings.model), retrieval_input, context_policy)
