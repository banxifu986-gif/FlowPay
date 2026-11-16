# 数据模型：006 网关加固与上线增强

## 配置项（Settings / local.toml）

| 键 | 类型 | 默认/试点值 | 说明 |
|---|---|---|---|
| `quota_merchant_daily_turns` | int | 200 | 每商户每 UTC 日 `run.started` 上限；0=关闭 |
| `quota_internal_daily_turns` | int | 500 | 每个内部账号每 UTC 日上限；防止单账号吃满全局额度；0=关闭 |
| `quota_global_daily_turns` | int | 5000 | 全局每 UTC 日 `run.started` 上限；0=关闭 |
| `quota_merchant_concurrency` | int | 1 | 每商户并发运行上限；0=关闭 |
| `failure_retry_attempts` | int | 2 | 可重试失败的最大额外尝试数 |
| `failure_retry_delay_seconds` | float | 1.0 | 线性退避基数（第 n 次等待 n*基数） |
| `knowledge_hybrid` | bool | true | 生产开启混合检索；测试直接构造客户端时默认关闭 |
| `knowledge_keyword_limit` | int | 20 | 关键词路召回上限 |
| `knowledge_vector_limit` | int | 20 | 向量路召回上限 |
| `knowledge_rrf_k` | int | 60 | RRF 融合常数 |

US3 回填的上下文/记忆默认值（来自 003/004 smoke 校准）：

| 键 | 值 | 依据 |
|---|---|---|
| `context_char_budget` | 30000 | 002/003 试点；正常会话不触发压缩 |
| `context_tool_result_budget` | 1600 | 单条旧工具结果占位阈值 |
| `context_compaction_trigger_chars` | 30000 | 与投影预算同值 |
| `context_summary_char_budget` | 4000 | 摘要正文实测不触发截断 |
| `context_report_char_budget` | 4096 | 报告产出/读取端预算 |
| `context_briefing_cap` | 6 | manual-loop 简报封顶 |
| `memory_recall_char_budget` | 2000 | 记忆注入段落预算 |
| `memory_semantic_limit` | 2 | 语义兜底候选数 |
| `memory_max_notes` | 200 | per-owner 总量封顶 |

## 用量守卫

计量不新建表：SQLite 事件日志内查询
`type='run.started'` 且 `substr(json_extract(data,'$.timestamp'),1,10)=UTC 日期`。
商户归属由同会话 `session.created.payload.user_id` 决定。
并发计数由管理器内存中的 `active_principals` 维护，提交时预留、任务结束释放。

超限结构（与 005 401/403 一致）：

```json
{"detail": "今日回合预算已用尽，请明天再试。", "code": "quota_exceeded"}
```

HTTP 429，`Retry-After` 指向下一个 UTC 日边界。

## 失败分类

| category | 识别 | 策略 | 用户文案方向 |
|---|---|---|---|
| `timeout` | TimeoutError / 类名含 Timeout、Connect | 退避重试 | 服务响应超时，请稍后重试 |
| `rate_limit` | ModelHTTPError 429（非配额关键词） | 退避重试 | 当前限流，请稍后再试 |
| `context_over_limit` | 400 且命中超限关键词 | 已有 reactive compact；仍失败则终态 | 上下文超限，请新建会话或精简 |
| `quota` | 429/402 且正文含 quota/billing/额度等 | 不重试 | 配额用尽，请联系管理员 |
| `model_5xx` | ModelHTTPError 5xx | 退避重试 | 模型服务暂时不可用 |
| `other` | 其余异常 | 不重试 | 本轮执行失败，记录已保留 |

`run.failed` 载荷：`{"error_type", "category", "message"}`；`message` 为上述友好文案，
完整异常只进服务端日志（沿用 005 F3 脱敏口径）。重试过程记 `run.retry`。

## 混合检索

- 迁移 `0003_trigram`：`CREATE EXTENSION IF NOT EXISTS pg_trgm`；
  `chunks.content` 上 `gin_trgm_ops` GIN 索引。
- 关键词路：`similarity(content, query)` + `ILIKE` 精确子串加分；阈值 0.1。
- 向量路：既有余弦相似度查询。
- 融合：RRF `score = Σ 1/(k + rank)`，按 `Passage.id` 去重；返回仍为 `Passage`，
  `source.collection_id/doc_id/chunk_no/path/sha256/locator/metadata` 不变。
- 事件 `knowledge.response` 仍写完整 `results`，可附加检索模式标记。

## 反馈

事件类型 `feedback.received`，append-only：

```json
{"run_id": "<被评价轮次>", "rating": "up"|"down", "user": "<主体用户名>", "comment": "可选附言"}
```

- 同一 `(session_id, run_id, user)` 只追加一次；重复提交幂等返回成功，不写新事件。
- 反馈只接受已终态轮次；运行中或不属于该会话的 `run_id` 返回 409。
- 会话 `session_view` 忽略该事件，不进入 `messages` 或检查点，因此不注入后续对话。
- 前端从事件投影读取每轮反馈状态；接口 `POST /api/sessions/{id}/feedback`。
- 反馈按会话归属校验，商户不能评价他人会话（404 不泄漏存在性）。
