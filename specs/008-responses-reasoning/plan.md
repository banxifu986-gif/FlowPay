# 实施计划：008 推理能力验证与检索收尾

需求唯一来源见 spec.md；本文件记录落地路径、分层接线、批次与验收口径。

## Technical Context

- 协议选择：`ModelSettings` 新增 `protocol` 字段（`"chat_completions"` 默认 / `"responses"`），装配层（bootstrap）按该值选择适配器；两套实现并存，回滚 = 配置改一行。
- 影响层：
  - `core/llm/chat/openai_responses.py`（新增）：Responses 适配器，实现 ChatModel Protocol（prepare/invoke/capabilities）。
  - `core/llm/chat/responses_stream.py`（新增）：Responses SSE 帧聚合器，产物与 `ChatCompletion` 同形状。
  - `core/config/settings.py`：ModelSettings 增 `protocol` 字段；request_options 校验的保留字集合补充 Responses 端的保留键。
  - `bootstrap.py`：按 protocol 分流构造适配器，workflows 层零改动。
  - `local.toml`：默认 protocol=chat_completions 不变；推理臂经环境变量覆盖 protocol + reasoning_effort。
  - `scripts/benchmark/retrieval.py` 与 `docs/`：收尾包两处。
- 多轮重放：`invoke` 把 messages 翻译成 Responses `input` items；助手历史消息（含 tool_calls）映射为 function_call / function_call_output item 序列；模型返回的 reasoning item 以 `encrypted_content` 原样回插到该助手轮的开头，服务端可解密续链。
- 流帧与追踪：原始 SSE 帧全量经既有 `model.chunk` 事件落库；聚合产物构造 `ChatCompletion` 形状对象（choices/message/tool_calls/usage/finish_reason），下游（编造引用核对、用量统计、失败分类、澄清决策事件）零改动。
- 评测协议：推理臂 96 题（每类前 16），基线复用 FULL300 的同 96 题子集；配置 = gpt-6-luna + protocol=responses + reasoning_effort=medium，其余与 FULL300 一致（--governance --hybrid、bge-m3 集合）。
- 费控：6 题冒烟实测 token/耗时 → 外推 96 题 → 实际超外推 2 倍停手。

## 章程检查

`.specify/memory/constitution.md` 不存在；按 CLAUDE.md / AGENTS.md 自查：

- 适配器实现放 `core/llm/chat/`（通用能力层），不依赖 workflows/scripts ✓
- workflows 层零改动，不把业务循环藏进下层 ✓
- 不新增第三方依赖（openai SDK 已在依赖内，responses 端点走同一 SDK 的 `client.responses` 面）✓
- 公共方法声明类型；已知结构不退回 dict[str, Any] ✓
- 无 DB schema 变更；配置变更有注释说明证据 ✓

无违反项。

## 批次

| 批次 | 内容 | 门禁 |
|---|---|---|
| B1 | Responses 适配器 + 流聚合器 + protocol 分流（TDD） | 新增单测全过；全量 pytest 基线保持；ruff/basedpyright 全绿 |
| B2 | 6 题冒烟（容器内，protocol=responses + medium） | run.failed=0；链路事件正常；实测 token/耗时外推 96 题预算，报告给 Ban |
| B3 | 推理臂 96 题 + 机制/attribution/对照报告 | 机制门三项不回归；四项指标 vs FULL300 子集；翻盘/回退清单 |
| B4 | 收尾包：knowledge_hybrid 默认关 + KB-API-017 补缺清单 | 配置切换后抽查检索一致；清单落 specs/008 |
| 收尾 | 质量门禁 + 交付报告 | make quality 全绿；报告落 specs/008 |

## 分层接线

### core/config/settings.py

- `ModelSettings.protocol: Literal["chat_completions", "responses"] = "chat_completions"`；
  loader 从环境变量 `FLOWPAY_MODEL_PROTOCOL` 覆盖（与现有三键同一机制）。
- request_options 的保留字集合按协议区分：chat 端保持现状；responses 端新增保留键
  `input/tools/stream/previous_response_id/ reasoning` 等，防止配置越权覆盖应用构造字段。

### core/llm/chat/openai_responses.py（新增）

- `OpenAiResponsesModel(settings)`，实现 ChatModel Protocol：
  - `prepare(messages, tools)` → 记录用请求对象：url=base_url+/responses，body=input items +
    tools + reasoning（从 request_options 提取 reasoning_effort，档位白名单 low/medium/high）+
    stream_options；不发送请求、不含密钥。
  - `invoke(request, emit)` → `client.responses.create(stream=True)`；逐帧喂给聚合器；
    连接失败一次重试语义与 chat 适配器一致；结束缺 `response.completed` 视为中断不当作成功。
  - `capabilities`：`{"tools": True, "streaming": True, "reasoning": True}`。
- messages → input 翻译规则（完整重放）：
  - system/user → `{"role": ..., "content": ...}`
  - assistant（纯文本）→ `{"role": "assistant", "content": ...}`
  - assistant.tool_calls → 逐个 `{"type": "function_call", "call_id", "name", "arguments"}` +
    对应 tool 结果 `{"type": "function_call_output", "call_id", "output"}`
  - 推理项：上一轮聚合器保存的 reasoning item（`type=reasoning`，带 encrypted_content）按
    原始顺序插回对应 assistant 轮之前——由 AgentTurn 的消息序列保证配对。
- 用量：`response.completed` 帧的 usage 映射为 prompt/completion/total tokens
  （input_tokens/output_tokens/total_tokens → 既有字段名）。

### core/llm/chat/responses_stream.py（新增）

- `ResponsesStreamAccumulator.consume(frame)`：处理事件类型
  `response.output_item.added/`、`response.content_part.*`、`response.output_text.delta`、
  `response.function_call_arguments.delta/`、`response.reasoning_summary_*`、
  `response.completed/failed`；返回公开文本增量。
- 产物构造 `ChatCompletion` 形状：choices[0].message.content / tool_calls（id=call_id、
  function.name/arguments）/ finish_reason=tool_calls|stop；usage；response_fields 存
  reasoning items 原文（encrypted_content）供下一轮重放。
- 无增量文本时返回空串；tool 调用参数不混入展示文本（与 chat 聚合器同纪律）。

### bootstrap.py

- `protocol == "responses"` → `OpenAiResponsesModel(settings.model)`；否则现状
  `OpenAiChatModel`。一行分流，stage 构造不变。

### scripts/benchmark/run.py

- 隔离项 `BENCHMARK_ISOLATION` 不动；推理臂经环境变量
  `FLOWPAY_MODEL_PROTOCOL=responses` + `FLOWPAY_MODEL_REQUEST_OPTIONS`（reasoning_effort=medium）
  注入；报表的检索/治理标注行追加 protocol 与 reasoning 标注，臂间对照可读。

### 收尾包

- `local.toml`：`knowledge_hybrid = false`，注释记录两次全量评测逐格相同的证据与复开方法。
- `specs/008-responses-reasoning/dataset-gap-kb-api-017.md`：补缺清单（缺失编号、引用题目、
  建议知识域与要素、验证步骤）；不改 dataset 文件。

## 验收路径

1. B1 单测（容器内）：input 翻译（含 tool_calls/推理项回插）、聚合器各事件类型、usage 映射、
   protocol 分流、保留字校验。
2. B2 冒烟：容器内跑 `--per-type 1`（6 题），protocol=responses + reasoning_effort=medium；
   验 run.failed=0、意图/拆解/澄清链路正常、token 外推报告。
3. B3 推理臂：96 题 + 机制/attribution/逐题对照（翻盘/回退）。
4. B4：改默认开关后抽查检索一致（对比 bge-m3 基线纯向量口径）；补缺清单评审。
5. 收尾：make quality + 容器 pytest + 交付报告。

## 风险与备注

- **SDK responses 面的流式事件形态**以实测为准；聚合器按事件类型白名单处理，未知事件落
  model.chunk 原文不阻断。
- **encrypted_content 跨轮重放**依赖服务端解密；若供应商拒绝带推理项的请求，回退策略是
  丢弃推理项重试一次（记 model.retry），并在报告中注明该模型的回退行为。
- **推理 token 计费**：reasoning tokens 计入 output；预算外推以冒烟实测为准，不做理论折算。
- **费控止损**：冒烟外推后，96 题实际用量超 2 倍停手；单题 token 超冒烟中位数 5 倍的题记
  异常并在报告列出。
- **基线可比性**：推理臂与 FULL300 子集的对照是同模型同嵌入空间的推理开关对照；DeepSeek
  43/96 只是参考刻度，不构成门禁。
- **Windows 环境**：测试在容器内跑；脚本类可用宿主 venv。
