# 数据模型：007 多轮澄清覆盖与文档召回

无数据库迁移；新增/变更均为会话检查点 JSON 与模型工具 schema。

## 主题枚举（ClarificationTopic）

| 值 | 题库类别 | 含义 | 用途 |
|---|---|---|---|
| `goal` | goal | 用户想确认/解决的诉求 | 选择调查方向、检索词 |
| `symptom_details` | symptom_details | 现象、报错、操作步骤 | 检索词与排查分支 |
| `occurrence_time` | occurrence_time | 发生时间/时间范围 | 记录查询时间窗 |
| `api_version` | api_version | API 版本/请求头 | 规则与兼容性判断 |

条件字段沿用 `ConditionField`（account_id、environment、payment_id、request_id、event_id、
delivery_id、endpoint_id、customer_id）；benchmark 映射：account_id/environment →
account_context，各类编号 → identifiers。

## ClarificationState

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `asked_topics` | tuple[Topic, ...] | 枚举去重 | 已经向用户发问过的主题 |
| `answers` | dict[Topic, str] | 值 ≤500 字符 | 用户已提供的主题值 |
| `unavailable_topics` | tuple[Topic, ...] | 枚举去重 | 用户明确表示拿不到的主题 |

更新规则（写入即覆盖旧值，顺序确定）：

1. `answer(topic, value)`：写 `answers[topic]`；若在 `unavailable_topics` 中则移除（“先说拿不到、后又提供”）。
2. `mark_asked(topics)`：只在 synthesize 最终采纳 clarify 决策、编造编号重试结束后写入一次。
   worker 提议但模型改判作答的不算“问过”；同一轮 pending 内的去重由 `pending` 承担。
3. `mark_unavailable(topics)`：写入 `unavailable_topics`，并从 `answers` 移除旧值。
4. `missing_topics()`：目标主题中既不在 `answers`、也不在 `unavailable_topics`、也不在
   `asked_topics` 的项；供 plan/synthesize 提示词使用。
5. 恢复检查点时：未知主题丢弃，超长值截断到 500 字符；同一主题同时出现在 answers 与
   unavailable 时以 answers 为准。

检查点形状（`saved_state`）：

```json
{
  "clarification": {
    "asked_topics": ["goal"],
    "answers": {"symptom_details": "创建支付后回调一直 500"},
    "unavailable_topics": ["occurrence_time"]
  }
}
```

`Case` 不新增字段；查询条件与背景主题分别持久化。

## 澄清请求（ClarificationRequest）

| 字段 | 类型 | 约束 |
|---|---|---|
| `conditions` | tuple[ConditionField, ...] | 可为空；去重 |
| `topics` | tuple[Topic, ...] | 可为空；去重 |
| `question` | str | 1–500 字符 |

同轮多个 worker 的请求并入 `SharedTurn.pending`；`conditions` 与 `topics` 取并集，
`question` 保留各自原文供 synthesize 摘要使用。`SharedTurn.pending` 不再使用
`manual_loop.AskUserResult`（旧阶段不受影响）。

## 模型工具 contract

### `ask_user`（multi-agent 版）

```json
{
  "type": "object",
  "properties": {
    "missing": {"type": "array", "items": {"enum": ["account_id", "environment", "..."]}},
    "topics":  {"type": "array", "items": {"enum": ["goal", "symptom_details", "occurrence_time", "api_version"]}},
    "question": {"type": "string", "minLength": 1, "maxLength": 500}
  },
  "required": ["question"],
  "additionalProperties": false
}
```

校验：`missing` 与 `topics` 至少一个非空；跨字段校验在 handler 内完成（JSON Schema 保留
`required: question`，避免把模型挡在协议外）。已问/已回答/不可提供的主题回填 notice，
不进入 pending。

### `set_tasks` 扩展

在现有 `tasks` 之外增加：

```json
{
  "clarification": {
    "answers": [{"topic": "goal|symptom_details|occurrence_time|api_version", "value": "string"}],
    "unavailable": ["goal|symptom_details|occurrence_time|api_version"]
  }
}
```

默认 `{"answers": [], "unavailable": []}`；`extra="forbid"` 不变。解析失败按空更新处理，
不阻塞拆任务。

### `set_response`（synthesize 决策）

| 字段 | 类型 | 约束 |
|---|---|---|
| `mode` | `answer` / `clarify` | 必填 |
| `answer` | str | `mode=answer` 时非空 |
| `question` | str | `mode=clarify` 时非空；1–500 字符 |
| `topics` | Topic[] | `mode=clarify` 时可为空；仍缺主题 |
| `conditions` | ConditionField[] | `mode=clarify` 时可为空；仍缺条件 |

解析失败回退：`pending` 非空 → `clarify`（正文用报告摘要 + 合并问题）；否则用模型正文
`answer`。两种模式的正文都过现有“编造编号”核对。

`clarify` 的用户可见正文由系统确定性拼接：**报告结论摘要 + 缺项清单 + 模型 question**。
模型 question 不再单独充当整段正文，避免模型漏写摘要时违反 FR-4；无报告时摘要段明确写
“尚未查到结论”。

决策声明的 `topics`/`conditions` 先过滤再使用：主题剔除已回答、不可提供、已问过；
条件剔除 `Case.known` 中已提供的字段。正文缺项、`asked_topics` 与
`clarification.decided` 事件都只记录过滤后真正仍缺的集合；模型重复索要已知项不得
原样呈现给用户（FR-5/SC-3）。

## 工具 schema 的绑定账户差异

| 场景 | query_payments 的 required | 说明 |
|---|---|---|
| 内部账号（`scope_overrides={}`） | `account_id`, `environment` | 与现状一致 |
| 商户账号（绑定 account_id） | `environment` | account_id 从 properties 移除，服务端强制注入 |

`log_tool` 同理：取第一个未被覆盖的过滤字段作为必填；全部被覆盖时 required 为空，
运行函数仍返回“至少给出一个过滤条件”的 notice。

## 澄清决策事件

synthesize 每次决策都追加 `clarification.decided`（append-only，不进模型上下文）：

```json
{
  "mode": "answer" | "clarify",
  "source": "model" | "fallback",
  "topics": ["goal", "symptom_details", "occurrence_time", "api_version"],
  "conditions": ["account_id", "environment", "payment_id", "request_id", "event_id", "delivery_id", "endpoint_id", "customer_id"],
  "has_reports": true
}
```

- `source=fallback` 表示模型未调用 `set_response` 或参数非法；用于统计结构化决策的真实
  使用率（风险观察，不改变产品行为）。
- `mode=clarify` 时 `topics`/`conditions` 是过滤后仍缺的项；回退路径取 `pending` 并集。
- `has_reports` 只作诊断信息；T032 的覆盖率读 topics/conditions 与检查点 answers，
  “等待轮是否带结论”按用户可见正文判定，不按 `has_reports` 推断。
- 该事件只做观测与验收统计，不写回检查点、不注入后续轮次。

## 基准指标

- **逐题结果行**：`signals` 包含每轮判定，含最终轮；`topics` 不写入逐题行，由事件读取。
- **覆盖率**：仅统计至少发起过一次追问的多轮题；把三处映射为 benchmark 类别后整题取并集：
  1. `tool.started` 中 `ask_user` 的 `topics` 与 `missing`；
  2. `clarification.decided` 事件的 `topics` 与 `conditions`（模型决策通道）；
  3. 最终检查点 `clarification.answers` 的主题（含 plan 从首条消息抽取的已回答项）。
  与题库 `required_clarification_categories` 比较；已回答项不算漏问。
- **重复问**：同一类别在两轮及以上被索要即计一次重复；输出题号与类别。
- **等待轮带结论**：硬门以确定性假模型的集成测试为准，覆盖“有报告摘要”与“无报告时明确
  说明”两态，两态均须通过。基准侧辅助检查最终 waiting 正文是否非空，且含进展说明或至少
  一个 finding/片段引用；抽检题号与结果写入交付报告。
- **检索诊断报告**：每题记录必需文档、查询口径（initial / observed / full）、检索路径
  （vector/hybrid）、k、命中文档 ID；聚合输出分题型 recall@k、`observed-initial` 与
  `full-initial` 差值、不可召回文档清单。`observed` 用今天 C 臂事件里用户的实际回复
  拼接，用于批量前诊断；`full` 仅作上界。最终确认再用新臂 actual 口径：initial + 检查点
  `clarification.answers` 的主题值，代表产品真实改写输入。
