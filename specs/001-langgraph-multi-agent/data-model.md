# Data Model: LangGraph 多 Agent 编排

Date: 2026-11-12。类型归属见 plan.md Project Structure；本文定义字段、校验与状态迁移。

## 实体

### IntentDecision（意图决策，intent.py）

| 字段 | 类型 | 校验 |
|---|---|---|
| intent | `Literal["troubleshoot","docs","chitchat"]` | 枚举，JSON Schema enum 同步给 set_intent 工具 |
| reason | `str` | 1..200 非空白；仅供 trace，不参与路由 |

生命周期：route 节点内构造，写进 GraphState 后只读。extra="forbid"。

### InvestigationTask（调查任务，state.py）

| 字段 | 类型 | 说明 |
|---|---|---|
| kind | `Literal["sql","docs"]` | Send 分发依据 |
| objective | `str` | 1..500；协调者给调查员的一句话目标 |

生命周期：plan 节点产出 → Send 按 kind 分发 → 轮内用完即弃（不跨轮）。

### WorkerReport（调查报告，state.py）

| 字段 | 类型 | 说明 |
|---|---|---|
| worker | `Literal["sql","docs"]` | 产出方 |
| text | `str` | 结论摘要（含依据记录/引用） |

生命周期：worker 节点产出，经 `operator.add` reducer 合并进 GraphState.reports。

### Finding（查询发现，跨轮，state.py）

| 字段 | 类型 | 说明 |
|---|---|---|
| tool | `str` | 工具名 |
| target | `str` | 条件串（排序后 `k=v` 拼接） |
| rows | `int` | 命中行数（0 也是有效结论） |
| digest | `str` | 纯结构化摘要：行数 + 首行主键字段；不做模型总结 |

校验：target 已由 claim 去重保证会话内唯一。生命周期：每次工具调用追加 → 轮末进 saved_state["findings"] → 下轮 briefing。

### SharedTurn（共享轮次资源，state.py，不进 GraphState）

| 字段 | 类型 | 说明 |
|---|---|---|
| case | `Case`（复用 manual_loop） | 跨轮已知条件；每轮自 saved_state["case"] restore，意图切换不清空 |
| done | `set[str]` | 查询去重键（`tool:target` casefold）；自 saved_state["queried"] restore |
| findings | `list[Finding]` | 本轮新增 + 上轮恢复 |
| seen | `set[str]` | 可引用记录编号：用户消息与 case 给出的 + 调查员工具返回的；synthesize 据此核对最终回答（SC-4），不跨轮持久化 |
| retrieved | `PassageSet` | 共享引用池；自 saved_state["evidence"] restore |
| budget | `Budget(16, 12)` | 全体调查员共享；plan 按任务数抬工具额度（12+3n）；synthesize 不占预算（research R3 实现口径） |
| actions | `list[ToolAction]` | trace 用 |
| pending | `list[AskUserResult]` | ask_user 去向；置位后短路 worker 循环 |

不变量：SharedTurn 每轮由 `MultiAgentWorkflow.run()` 构建一次，闭包注入各节点；节点不得替换其字段引用，只原地修改。图（`build_graph`）同样每轮重建——runtime（追踪出口、模型调用入口）是每轮创建的资源，节点以闭包捕获 model/prepare_query/runtime/shared，不引入跨轮的图实例。

### GraphState（图状态，state.py，TypedDict）

| 字段 | 类型 | reducer |
|---|---|---|
| user_message | `str` | 覆盖 |
| saved_state | `JsonObject` | 覆盖；节点不读写，run() 轮末组装新检查点 |
| intent | `str` | 覆盖 |
| tasks | `list[InvestigationTask]` | 覆盖 |
| reports | `list[WorkerReport]` | `operator.add` |
| answer | `str` | 覆盖 |
| waiting | `bool` | 覆盖；synthesize 置位 |

## saved_state JSON Schema（跨轮检查点，轮末组装）

```json
{
  "case":     {"known": {"<ConditionField>": "<value>"}},   // 与 manual-loop 同键同语义
  "queried":  ["<tool:target>"],                            // 与 manual-loop 同键，sorted
  "findings": [{"tool": "...", "target": "...", "rows": 0, "digest": "..."}],  // 新增
  "evidence": [/* Passage 投影，与 manual-loop 同键 */],
  "stop_reason": "<answered|waiting_user>"                  // 新增；budget_exhausted 不可达——synthesize 不占预算且必然作答（见 research R3 实现口径）
}
```

恢复端：`Case.restore`（原样复用）、`restore_queried`（原样复用）、findings/evidence 由 multi_agent 自己读回。

## 状态迁移

1. **route**：`saved_state` + `user_message` → intent（规则短路优先，只认业务编号前缀；环境词不参与路由，仅由 case 累积）。
2. **chitchat/doc_qa**：单节点产出 answer → END；轮末照常重建检查点（case/queried/findings 等效携带——闲聊与文档消息不含业务编号，case.apply 为空操作），并新增 stop_reason/findings 键。`TurnResult.evidence` 恒为共享片段池全量（`shared.retrieved.all()`），因此这些轮会把此前轮次检索的片段一并交给界面做引用展开。
3. **plan → Send → worker → synthesize**：
   - 正常：worker 产出 reports → synthesize 合成 answer → END。
   - 编造核对（SC-4）：synthesize 合成后核对回答中的记录编号是否都在 shared.seen，对不上的回传纠正重试一次；重试仍编造则接受，由界面据证据清单标注。
   - 追问：worker 内 pending 置位 → 该 worker 短路收尾 → 其余 Send worker 自然收束 → synthesize 跳过合成、置 waiting → run() 返回 `status="waiting_user"`。
4. **预算耗尽**：claim 拒绝 + `may_think` 短路 → synthesize 以现有材料合成部分结论（参照 manual-loop"依据不足给部分结论"语义）。
