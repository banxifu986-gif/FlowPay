# Data Model: 模型用量聚合与请求前上下文投影

Date: 2026-11-14。新增实体全部为强类型 dataclass；JSON 边界只经 `flowpay.core.json` helper 序列化。本特性无状态机变迁——投影无状态、每轮确定性重算；用量按轮累计、轮末汇总。

## 1. UsageEntry（单次模型调用的用量）

| 字段 | 类型 | 语义 |
|---|---|---|
| input_tokens | int \| None | completion.usage.prompt_tokens；缺失记 None |
| output_tokens | int \| None | completion.usage.completion_tokens；缺失记 None |
| total_tokens | int \| None | completion.usage.total_tokens；缺失记 None |

- 提取规则（R-3）：`completion.usage` 为非空对象才取字段；缺失 / None / 空对象 → 全 None。
- 不按 0 补齐：None 与 0 语义严格区分（0 = 供应商报了 0，None = 未提供）。

## 2. UsageSummary（按轮聚合）

| 字段 | 类型 | 语义 |
|---|---|---|
| model_calls | int | 本轮模型请求次数（含被纠正循环拒绝的调用——费用已发生） |
| input_tokens | int \| None | 各 entry 之和；任一 entry 该位缺失 → None（不推测） |
| output_tokens | int \| None | 同上 |
| total_tokens | int \| None | 同上 |

JSON 形状（`message.assistant` payload 的 `usage` 键）：

```json
{"model_calls": 3, "input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}
```

## 3. ContextPolicy（投影策略）

| 字段 | 类型 | 语义 |
|---|---|---|
| char_budget | int | 本次请求消息总字符数预算（近似计量，len() 口径） |
| tool_result_budget | int | 单条工具结果字符数上限，超限触发占位替换 |

- None 策略 = 现行为，是所有既有路径的默认值（调用方短路，不进入投影）。
- char_budget 是尽力目标而非硬上限：不可删集合自身超预算时，以 FR-5 存活不变量优先（contracts §4 第 5 条）。
- 阈值默认值由单测模拟数据校准（R-2）；settings 提供覆盖键。

## 4. 占位摘要（被压缩工具结果的替身）

保留原 tool 消息的 role 与 tool_call_id（配对不破），content 替换为：

```json
{"placeholder": true, "target": "sql:source=pay_db scope=acct_bg_001", "rows": 50, "record_ids": ["pay_bg_001_0003"], "original_chars": 12000}
```

- `record_ids` 来自被压缩原文的 `record_ids()` 扫描——跨轮引用可核查，disputed 不误拦（不变量 3）。
- 占位替换不改变消息条数与配对关系；整组删除按"assistant + 尾随全部 tool 结果"为单位，两种动作都不拆配对。

## 5. TurnResult（既有契约，新增字段）

| 字段 | 类型 | 语义 |
|---|---|---|
| usage | UsageSummary \| None | 本轮用量汇总；一次模型都没调时 None |

## 6. message.assistant payload（新增键）

```json
{"content": "...", "evidence": [...], "usage": {"model_calls": 3, "input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}}
```

- `usage` 键可缺失（既有事件、零调用轮）；session_view 按缺键宽容读取，旧事件流重放不受影响。
- `run.finished` / `run.failed` / `run.canceled` / `run.interrupted` 的 payload 一律不动。

## 7. seen 集合重建规则（修复后）

并集来源：

1. question 文本中的编号（既有）
2. 历史轮 user 消息编号（既有 `given_record_ids`）
3. **新增**：历史轮 tool 消息 content 中的编号
4. 本轮 `observe()` 收集的编号（既有）

不计入：assistant 历史消息（模型自己说的）。防线判定规则（disputed）不变。

## 8. 持久化边界（不变）

- `saved_state.messages` 永远是未压缩原生消息；投影结果不落任何持久化通道（FR-8）。
- 事件日志 schema 不变：usage 已随 `model.response` 事件的 completion dump 持久化，本特性零新增事件类型。
