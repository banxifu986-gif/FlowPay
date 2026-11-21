# 统一解析配置与环境覆盖；运行快照只保留配置值和密钥变量名，不保存密钥。
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from flowpay.core.json import JsonObject, require_object
from flowpay.core.llm.embedding import EmbeddingSettings


@dataclass(frozen=True)
class ModelSettings:
    """聊天模型连接配置。

    name 对应请求的 model，base_url 指向兼容服务；api_key_env 是密钥变量名，
    默认那个变量名不绑定供应商，换模型只改 .env，不改代码。
    request_options 保存供应商扩展参数，不得覆盖应用构造的消息和工具。
    protocol 选择聊天协议（specs/008）：chat_completions 是现状口径；responses
    是 OpenAI 推理模型的正式接口，由装配层据此选择适配器实现。
    """

    provider: str = "scripted"
    name: str = "foundation-demo-v1"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "FLOWPAY_MODEL_API_KEY"
    timeout_seconds: float = 60
    protocol: str = "chat_completions"
    request_options: JsonObject = field(default_factory=dict)

    def __post_init__(self):
        """拒绝非对象扩展参数以及对 messages、tools、model、stream 等保留字段的覆盖。"""
        require_object(self.request_options)
        if self.protocol not in ("chat_completions", "responses"):
            raise ValueError(f"Unknown model protocol: {self.protocol}")
        reserved = {"model", "messages", "tools", "stream", "stream_options"}
        if reserved & self.request_options.keys():
            raise ValueError("Model request_options cannot override reserved request fields")


@dataclass(frozen=True)
class Settings:
    """一次服务启动使用的项目配置，不是发给模型的请求参数。

    data_dir 用于本地运行产物，dataset_path 是业务数据根目录；model 和 embedding
    分别配置聊天与嵌入。路径由 loader 解析，凭据字段只保存环境变量名。
    """

    data_dir: Path
    dataset_path: Path
    workflow: str = "multi-agent"
    app_env: str = "development"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000
    mock_delay_ms: int = 70
    model: ModelSettings = ModelSettings()
    knowledge_url_env: str = "FLOWPAY_KNOWLEDGE_DSN"
    knowledge_builder_url_env: str = "FLOWPAY_KNOWLEDGE_BUILDER_DSN"
    embedding: EmbeddingSettings | None = None
    sources_config: Path | None = None
    knowledge_collection_id: str | None = None
    # 预置账号文件（specs/005）；路径由 loader 解析。缺省 None = 不装配认证，
    # 只有 scripts/demo.py 的契约假后端走这条路径，服务入口启动前会拒绝缺配置。
    accounts_path: Path | None = None
    # 令牌签名密钥只保存环境变量名，值不进配置快照。
    auth_token_secret_env: str = "FLOWPAY_AUTH_TOKEN_SECRET"
    # 请求前上下文投影：两项预算都 > 0 时启用；缺省 0 = 关闭 = 现行为。
    context_char_budget: int = 0
    context_tool_result_budget: int = 0
    # LLM 摘要式压缩随投影策略一并启用：触发线 > 0 时在 think() 入口压缩检查点
    # 消息流；摘要正文超出上限由代码截断（specs/003）。
    context_compaction_trigger_chars: int = 0
    context_summary_char_budget: int = 4000
    # 调查报告产出端/汇总读取端预算（specs/003 P1，0=不截断）。
    context_report_char_budget: int = 4096
    # manual-loop 简报 system 消息数封顶（specs/004 P2，0=现行为逐字不变）。
    context_briefing_cap: int = 0

    # 跨会话排查记忆（specs/004）：recall_char_budget > 0 启用整链路（抽取+召回+
    # 注入+封顶），值为注入简报的记忆段落字符预算；缺省 0 = 关闭 = 现行为。
    memory_recall_char_budget: int = 0
    # 语义兜底召回候选数；> 0 时键未命中才发生嵌入调用，且要求 embedding 配置。
    memory_semantic_limit: int = 0
    # 记忆总量封顶，超出按 created_at 最旧淘汰。
    memory_max_notes: int = 200

    # 用量守卫（specs/006）：按 UTC 日计 run.started；0 = 关闭。
    # 默认值实施时定，待 smoke/真机校准后回填；商户并发 1 表示第二请求直接 429。
    quota_merchant_daily_turns: int = 200
    quota_internal_daily_turns: int = 500
    quota_global_daily_turns: int = 5000
    quota_merchant_concurrency: int = 1
    # 可重试失败（超时/限流/模型 5xx）的额外尝试数与线性退避基数。
    failure_retry_attempts: int = 2
    failure_retry_delay_seconds: float = 1.0
    # 混合检索（specs/006 US4）：关键词路 + 向量路 RRF；测试直接构造
    # KnowledgeClient 时默认走纯向量，因此该开关只影响装配层。
    knowledge_hybrid: bool = True
    knowledge_keyword_limit: int = 20
    knowledge_vector_limit: int = 20
    knowledge_rrf_k: int = 60

    # 保留复现所需的运行设置；认证环境变量的值不属于快照内容。
    def snapshot(self) -> JsonObject:
        """返回可序列化的运行配置，供 run.started 固定本轮设置。

        Path 转成字符串；数据库与模型凭据保存的是环境变量名，不读取其值。
        调用者仍不得把密钥放进 request_options 等普通配置字段，这里不是通用脱敏器。
        """
        value = asdict(self)
        value["data_dir"] = str(self.data_dir)
        value["dataset_path"] = str(self.dataset_path)
        value["sources_config"] = str(self.sources_config) if self.sources_config else None
        value["accounts_path"] = str(self.accounts_path) if self.accounts_path else None
        return value
