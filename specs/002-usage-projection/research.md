# Research: 模型用量聚合与请求前上下文投影

Date: 2026-11-14。所有决策已在计划定稿（pi-agent-snuggly-floyd.md）与 pi-mono 源码复核中确认，无遗留 NEEDS CLARIFICATION；本文记录决策、依据与备选。

## R-1 投影策略的逐层判定

- **Decision**: 两层机制，按序应用。(a) 超限的旧工具结果先做**占位替换**：保留 role=tool 与原 tool_call_id，content 换为占位摘要（target、行数、记录编号清单、原始字符数）；(b) 替换后仍超预算，从最老**完整轮次**（assistant + 其全部尾随 tool 结果）**整组删除**，直到预算满足或触及不可删集合（首条 system、任何 user、最后一段 assistant 交换）。
- **Rationale**: 占位替换保住配对（tool_call_id 原样）与编号可核查性（disputed 不误拦）；整组删除保证永不拆散 tool_call_id 配对（供应商要求 assistant.tool_calls 与 tool 消息成对）；只从最老删起 = 极保守默认（决议 3）。
- **Alternatives considered**: tiktoken 精确计量（拒：新增依赖，违反 FR-9）；只删不占位（拒：编号证据静默消失，跨轮引用误拦）；投影结果持久化（拒：FR-8 要求检查点存原生消息、每轮确定性重算）。

## R-2 阈值校准口径

- **Decision**: 阈值用单测模拟数据定保守默认值，settings 提供可配置键；opt-in 试点验证后按 P1 真实用量数据微调。
- **Rationale**: 决议 3——不阻塞实施；字符数与 token 比率随内容波动，保守默认 + 试点微调比精确计量更稳。
- **Alternatives considered**: P1 先攒真实数据再定阈值（拒：P2 被阻塞，串行依赖过强）。
- 注（2026-11-14 实施口径）：阈值最终由 settings 注入决定（缺省 0 = 关闭），代码内无常量默认——"保守默认"落在配置而非代码，试点时按本条原意校准配置值。

## R-3 usage 提取与容错口径

- **Decision**: `from_completion` 只认 `completion.usage` 为非空对象时的 prompt_tokens / completion_tokens / total_tokens；缺失、None、空对象 → 对应字段记 None；聚合不推测 total（input 或 output 任一缺失时 total 记 None）。
- **Rationale**: `stream.py:109-111` 已容忍缺失（usage 为 None）；嵌入侧 `usage_batches` 同口径"不推测缺失字段"（embedding/contracts.py:29）；compatible 端点可能不回 usage。被纠正循环拒绝的输出同样计费，照常累计。
- **Alternatives considered**: 缺失按 0 补齐（拒：掩盖故障，"0"与"缺失"语义混同）。

## R-4 用量的前端挂载点

- **Decision**: TurnResult 新增 `usage` 字段；manager 提交时并入 `message.assistant` payload 的 `usage` 键；session_view 的 ChatMessage 增加 usage 字段（缺键宽容）。
- **Rationale**: 与回答同批原子提交，前端在回答旁展示"本轮用量"；不新增事件类型（FR-2）；SSE 只推已持久化事件的约定不变。
- **Alternatives considered**: 挂 run.finished（拒：该事件语义是轮状态，不承载消息级数据）；新增独立 usage 事件类型（拒：违反 FR-2）。

## R-5 seen 重建修复

- **Decision**: 恢复检查点时，seen 除 question + user 消息编号外，扫描历史 tool 消息内容，把工具真实返回过的编号并入；assistant 历史消息仍不计入（模型自己说的）。
- **Rationale**: docstring 本义"工具真的返回过的编号"；`given_record_ids` 已把用户给的编号跨轮累计，同一逻辑理应覆盖工具返回的编号（决议 1：缺口而非刻意严格）；防线语义不变——seen 仍只收真实来源。
- **Alternatives considered**: 维持严格（已否决：多轮排查每次引用旧编号都触发误拦改写）。

## R-6 并行调查的投影隔离

- **Decision**: 不做任何跨 worker 投影协调：每 worker 独立 AgentTurn（remember=False），投影在各自 think() 内独立计算；测试断言各 worker 的 model.request 互不污染、共享对象（budget/findings/retrieved）不受投影影响。
- **Rationale**: investigation.py 现状即每节点新建 AgentTurn，无共享消息状态；投影无跨轮持久化需求（FR-8）。
- **Alternatives considered**: 共享投影缓存（拒：引入并发写，违背确定性重算）。

## R-7 fixtures 增强

- **Decision**: `completion()` 加可选 usage 参数（None 缺省 = 现状，不改变既有用例的响应形状）；`Model.invoke` 加可选分片开关——正文拆多 chunk 发射、工具参数 JSON 分片发射。仅此两项，不引入抽象。
- **Rationale**: P1 聚合与 ChatStreamAccumulator 分片路径当前无测试驱动（P3 结论）。
- **Alternatives considered**: faux provider 化（拒：scripted fixtures 已覆盖且走真实事件日志链路断言，更强）。

## R-8 multi-agent 的 usage 汇总归属

- **Decision**: SharedTurn 增加 usage_entries 收集：worker 的调用经 AgentTurn 累计、`investigate()` 收尾时并入（finally，pending 短路路径同样收集）；route / plan / synthesize 的直连 `ChatClient.call` 调用点各自登记一条 entry（这些节点不经 AgentTurn——2026-11-14 实施核实）；graph.run 末尾 `sum_entries` 汇总写入 TurnResult.usage。
- **Rationale**: SharedTurn 已是本轮全体 Agent 共享资源的既有落点（budget/findings 同模式）；直连调用点是诚实用量口径的必然覆盖面——只统计 worker 会漏掉意图分类、拆解与汇总的调用。汇总维度是"本轮"，与 FR-1 的 per-run 语义一致。
- **Alternatives considered**: 只统计 AgentTurn 调用（拒：漏计约半数调用，口径失真）；每 worker 单独上报、前端自行累加（拒：前端拿到的是合成回答，用量应与回答对齐）。
