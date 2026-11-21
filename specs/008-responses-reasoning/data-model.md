# 数据模型：008 推理能力验证与检索收尾

无 DB schema 变更；新增/变更集中在模型配置、适配层内部结构与事件载荷。

## ModelSettings 扩展

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `protocol` | `"chat_completions" \| "responses"` | `"chat_completions"` | 聊天协议选择；装配层按此分流适配器 |
| 环境覆盖 | `FLOWPAY_MODEL_PROTOCOL` | — | 与既有三键同一覆盖机制 |

`request_options` 保留字校验按协议区分：

- chat_completions 端（现状）：`model/messages/tools/stream/stream_options`
- responses 端新增：`input/stream/previous_response_id/reasoning/include`
  ——防止配置越权覆盖应用构造字段。

## 推理参数

| 键（request_options 内） | 值域 | 默认（008 推理臂） | 说明 |
|---|---|---|---|
| `reasoning_effort` | `low \| medium \| high` | `medium` | 白名单校验，非法值启动即败；chat 路径维持 `none` 现状 |

## input items 翻译表（messages → Responses input）

| messages 元素 | input item |
|---|---|
| `system` / `user` | `{"role": "system"/"user", "content": <text>}` |
| assistant 纯文本 | `{"role": "assistant", "content": <text>}` |
| assistant.tool_calls[k] | `{"type": "function_call", "call_id", "name", "arguments"}` |
| tool 结果（tool role） | `{"type": "function_call_output", "call_id", "output"}` |
| 推理项（上一轮聚合器保存） | `{"type": "reasoning", ...original, "encrypted_content": <keep>}` 按原序插回对应 assistant 轮之前 |

推理项随聚合产物存于 `response_fields["reasoning_items"]`（原文数组），下一轮由适配器按
消息序列配对回插；AgentTurn 的 tool_call_id ↔ tool 消息配对机制保证配对完整（既有约束）。

## ResponsesStreamAccumulator 事件处理表

| 事件类型 | 处理 |
|---|---|
| `response.output_item.added` | 记 item 类型（message / function_call / reasoning）；function_call 建 tool_calls 槽 |
| `response.output_text.delta` | 拼公开文本增量（返回值） |
| `response.function_call_arguments.delta` | 按 item id 追加参数片段 |
| `response.reasoning_summary_text.delta` | 追加推理摘要（存扩展字段，不进展示文本） |
| `response.output_item.done` | reasoning item 原文存 `reasoning_items`；function_call 定稿 |
| `response.completed` | 终态：usage 映射 + finish_reason；`response.incomplete` 同样视作完成但记录原因 |
| `response.failed` | 上抛异常（走既有失败分类） |
| 其他/未知事件 | 原文落 model.chunk，不阻断聚合 |

## usage 映射

| Responses 字段 | ChatCompletion 字段 |
|---|---|
| `usage.input_tokens` | `prompt_tokens` |
| `usage.output_tokens` | `completion_tokens`（含 reasoning tokens，服务端口径） |
| `usage.total_tokens` | `total_tokens` |
| `usage.input_tokens_details.cached_tokens` | 事件原文保留，不映射既有字段 |

## 事件载荷增量

| 事件 | 变更 |
|---|---|
| `model.request` | `request.body` 为 input items + tools + reasoning；新增 `protocol: "responses"` 标记 |
| `model.response` | `completion` 为 ChatCompletion 同形状；`response_fields.reasoning_items` 随 exclude_unset 保留 |
| `model.chunk` | 原始 SSE 帧照旧全量落库 |
| `model.retry` | 新增原因 `encrypted_content_rejected`（推理项被拒后的降级重试） |

## 基准臂标注

报表"检索路径/治理配置"行追加：`协议：responses；推理：medium`——臂间对照可读；隔离项
`BENCHMARK_ISOLATION` 不变。

## 收尾包数据

- `knowledge_hybrid` 默认 `false`（local.toml）；注释引用两份全量对照报告路径。
- `dataset-gap-kb-api-017.md`：缺失文档编号、引用它的题目清单（从 FULL300 逐题
  `required_documents` 反查）、建议知识域与要素、验证步骤（重建后检索诊断复查）。
