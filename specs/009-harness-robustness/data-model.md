# 数据模型：009 harness 鲁棒性

复用既有实体（FailureInfo、SharedTurn、事件日志）的扩展与新增实体。所有新事件为 JSON 事件，无 DB schema 变更。

## 实体

### FailureInfo（扩展，core/execution/failures.py）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `retryable` | bool | 既有：是否可重试。现状模型类 400 已归 other 档（`retryable=False`，不烧重试预算）。 |
| `deterministic` | bool | 新增：确定性错误。约束：`deterministic=True` 时 `retryable` 必为 False。**不改变既有重试语义**——消费方是工具包装层（驱动链路禁用）与 `tool.outage` 事件载荷（`failure_class` 可观测）。 |

判定白名单：嵌入/检索结构性 4xx（400/422 输入类拒绝）、供应商参数确定性拒绝；429/超时/5xx/连接错误 → transient。

### ToolOutage（新增，SharedTurn 字段 `tool_outages: set[str]`）

- 元素：链路标识（如 `embedding`、`sql:<source>`、`logs`）。
- 生命周期：run 内登记，轮次终止清空；不跨轮、不跨会话。
- 状态转换：无 → 工具包装层捕获确定性失败（FR-1 白名单）→ 登记 + 发 `tool.outage`（载荷含 `failure_class=deterministic`）→ 触发登记的任务按既有 error_report 路径收束（**禁用即收束**，携带已查发现与失败说明）；本轮后续对已禁用链路的调用在包装层立即抛失败终止对应任务，不再发起请求。

### SubtaskProgress（新增，SharedTurn 字段 `task_progress: dict[str, SubtaskProgress]`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `key` | str | 幂等键 = 任务目标 + worker 类型。 |
| `worker` | str | sql_worker / doc_worker。 |
| `objective` | str | 任务目标原文。 |
| `done_keys` | list[str] | 该子任务的已查询键（并入 SharedTurn.done 语义）。 |
| `findings` | list[str] | 已查发现摘要。 |
| `evidence_refs` | list[str] | 证据引用（文档片段 / 记录编号）。 |
| `finished` | bool | 子任务是否已收束（含预算收束）。 |

落盘：worker 收尾处写 `turn.progress` 事件（幂等键去重）；恢复时 manager 层读取最近 progress、**按幂等键 merge**——并行 worker 落库顺序无保证，已 `finished=True` 的条目不被未 finished 的同名条目覆盖——merge 后注入 `TurnInput.saved_state`。

### TaskBudget（新增，SharedTurn 字段 `task_tool_calls: dict[str, int]`）

- key 同 SubtaskProgress.key；值 = 该子任务累计工具调用次数。
- **独立记账**：与既有全局 `SharedTurn.budget`（Budget(16,12)，`claim` 统一记账）并存——工具包装层 claim 时两处同时记数；单任务达 `investigation_tool_budget` 上限即收束该任务（finished=True，携带已查发现），全局预算余量不受影响，发 `task.budget_exhausted` 事件。
- 链路禁用联动（FR-2）：任务触发链路禁用登记时，该任务随即按既有 error_report 收束——禁用即收束，不依赖两套预算耗尽。

### CapabilityMap（新增，workflows/multi_agent/investigation.py 内静态表）

- 映射：任务目标/工具诉求 → sql_worker（业务记录、SQL、日志）| doc_worker（规则、文档、知识库）。
- 用途：分发前校验；不匹配 → 改派 → 重计划（至多一次）→ 丢弃（`task.dropped` 事件）。

### UnverifiedMark（输出口径，citation_check.py / resolve_response 产物）

- 语义：结论句无引用标记时确定性降级的统一表述（"未能核实 X"）。
- 覆盖来源：查不到证据、预算收束、链路禁用收束、synthesize 空输出占位；不含 waiting_user（互斥；存在 pending 时维持 clarify 优先，未证实口径仅约束无 pending 场景——2026-11-23 拍板）。
- 可观测：报表按口径统计（SC-5）；编号真伪仍由既有编造核对机制负责。

### ModelOutputRetry（新增行为口径，FR-12）

- 触发：synthesize 末次调用无 `set_response` 决策或正文为空（`answer_text` 抛 `ModelOutputError`）。
- 处置：确定性重试一次（参照 003 编造核对 DISPUTE 重试先例）；重试仍空 → 诚实占位文本正常完成本轮（不 `run.failed`）。
- 不适用：上下文超限、模型不可用、配额拒绝等既有硬失败分类（仍走既有终态）。
- 事件形态：重试走既有 `model.retry` 通道；占位完成走既有 `message.assistant` + `run.finished`，不新增事件类型。

## 新增事件（JSON，复用既有事件日志通道）

| 事件 | 载荷要点 | 时机 |
| --- | --- | --- |
| `tool.outage` | 链路标识、`failure_class=deterministic`、失败原因分类 | 工具包装层捕获确定性失败并登记禁用时 |
| `turn.progress` | SubtaskProgress 全量字段 | worker 子任务收尾（幂等键去重） |
| `task.reassigned` | 任务目标、原 worker、新 worker | plan 校验改派 |
| `task.dropped` | 任务目标、原因 | 重计划后仍不匹配 |
| `task.budget_exhausted` | 幂等键、调用次数、上限 | 预算收束 |

兼容性：前端与既有报表对未知事件不阻断（既有纪律）；`turn.progress` 不改变检查点形状。

## 恢复流程（状态转换）

1. 轮次执行中失败（可恢复类）→ 既有语义：`run.failed` / 重启后 `run.interrupted`。
2. 用户显式继续 → 既有 `resume_run` → `_submit_run(..., resume=True)`：日回合计数跳过（`daily_run_count` 过滤 `resumed=true` 的 run.started——恢复轮的 run.started 照常落库、载荷已带 resumed 标记），**并发名额正常预留**（与 `_execute` finally 的 `_release_quota` 对称，防净减）；manager 层读最近 `turn.progress` 事件按幂等键 merge，注入 `TurnInput.saved_state` → `run` 重建 SharedTurn 预填（clarification 既有恢复不动）→ `dispatch` 过滤已完成子任务 → 继续执行。
3. 恢复后零重查：done_keys 预填，重复查询被既有去重键拦截（SC-4 事件序列可证）；多跳两段式拼接值有序化，同一 findings 集合不因顺序漂移产生新查询。
