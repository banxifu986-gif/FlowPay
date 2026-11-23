"""装配运行需要的具体客户端；阶段选择交给 router，运行资源统一管理连接。"""

import os

from flowpay.core.auth import AccountPolicy, TokenAuthenticator
from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.config import Settings
from flowpay.core.execution.contracts import Workflow
from flowpay.core.llm.chat.contracts import ChatModel
from flowpay.core.llm.chat.openai_chat import OpenAiChatModel
from flowpay.core.llm.chat.openai_responses import OpenAiResponsesModel
from flowpay.core.llm.embedding.ollama import query_input_for
from flowpay.core.memory.contracts import MemoryPolicy
from flowpay.router import create_stage, resolve_stage


def create_authenticator(settings: Settings) -> TokenAuthenticator:
    """装配登录门禁：账号文件与令牌密钥缺一不可，在服务启动前失败而不是运行到一半。

    密钥只从环境变量读取，值既不进配置快照也不落日志；有效期由账号文件给出。
    """
    if settings.accounts_path is None or not settings.accounts_path.is_file():
        raise ValueError("Authentication requires accounts_path pointing at the accounts file")
    secret = os.environ.get(settings.auth_token_secret_env)
    if not secret:
        raise ValueError(f"Missing environment variable: {settings.auth_token_secret_env}")
    return TokenAuthenticator(AccountPolicy.from_file(settings.accounts_path), secret)


def create_workflow(settings: Settings) -> Workflow:
    """核对当前阶段的模型和知识配置，注入客户端；缺少依赖时在服务启动前失败。"""
    stage = resolve_stage(settings.workflow)
    if settings.model.provider != "openai-compatible":
        raise ValueError("RAG requires an openai-compatible chat model")
    if not settings.knowledge_collection_id or settings.embedding is None:
        raise ValueError("RAG requires a ready knowledge collection and embedding configuration")
    # 查询侧输入约定随嵌入模型走，未知模型在这里失败，不猜格式。
    query_input = query_input_for(settings.embedding)
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
    # 两项预算都 > 0 才启用请求前投影；缺省 0 = 关闭 = 现行为。压缩与直连护栏
    # 随同一份策略一并启用（触发线 > 0 才实际生效），避免投影关闭时预算 0 的病态路径。
    context_policy = (
        ContextPolicy(
            char_budget=settings.context_char_budget,
            tool_result_budget=settings.context_tool_result_budget,
            compaction_trigger_chars=settings.context_compaction_trigger_chars,
            summary_char_budget=settings.context_summary_char_budget,
            report_char_budget=settings.context_report_char_budget,
            briefing_cap=settings.context_briefing_cap,
        )
        if settings.context_char_budget > 0 and settings.context_tool_result_budget > 0
        else None
    )
    # 跨会话记忆缺省关闭：recall_char_budget > 0 才装配策略（specs/004）。
    # 语义兜底依赖嵌入配置，而上方 RAG 前置校验已保证 embedding 存在；
    # recall 层（resources.memory_recall）仍有独立兜底，缺配置时退化为键匹配。
    memory_policy = None
    if settings.memory_recall_char_budget > 0:
        memory_policy = MemoryPolicy(
            recall_char_budget=settings.memory_recall_char_budget,
            semantic_limit=settings.memory_semantic_limit,
            max_notes=settings.memory_max_notes,
        )
    # 协议分流（specs/008）：responses 走推理适配器，chat_completions 维持现状。
    chat_model: ChatModel = (
        OpenAiResponsesModel(settings.model)
        if settings.model.protocol == "responses"
        else OpenAiChatModel(settings.model)
    )
    return create_stage(
        stage,
        chat_model,
        query_input,
        context_policy,
        memory_policy,
        investigation_tool_budget=settings.investigation_tool_budget,
    )
