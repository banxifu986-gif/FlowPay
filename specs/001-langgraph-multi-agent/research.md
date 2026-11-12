# Research: LangGraph 多 Agent 编排

Date: 2026-11-12。所有决策均已与需求方确认；本文记录决策、依据与备选。

## R1: LangGraph 接管边界

- **Decision**: LangGraph 只接管轮内编排（意图路由 + Agent 调度 + loop）；SessionManager 继续负责跨轮持久化、幂等、identity 校验、聊天历史投影。不引入 LangGraph checkpointer（轮内 `ainvoke` 一次跑完）。
- **Rationale**: saved_state 已是稳定跨轮通道，事件溯源与幂等重做风险高；轮内状态天然在内存。
- **Alternatives**: LangGraph checkpointer 全面替代事件日志——迁移彻底但 UI 投影、幂等、恢复语义全部重做，被否决；仅做路由层不引入依赖——无法满足多 Agent 协作目标，被否决。

## R2: 意图结构化输出通道

- **Decision**: route 节点 offer 一个 `set_intent` 工具（parameters = IntentDecision JSON Schema），取 `tool_calls[0].function.arguments` 用 pydantic 解析。
- **Rationale**: 复用现有 ChatModel 协议、tracer 记录与 fixtures.Model 的 tool_calls 通道，零新 API。
- **Alternatives**: langchain structured output——需引入 langchain 模型包装层，与自研 ChatModel 冲突，被否决。

## R3: 并行下的共享状态原子性

- **Decision**: 所有图节点一律 `async def`；claim 提为模块级纯同步函数（无 await），依赖"单事件循环内同步临界区天然原子"；注释声明不变量。
- **Rationale**: langgraph 同步节点走线程池，check-then-act 会被打断；async 节点共享同一事件循环，无需锁。
- **Alternatives**: `asyncio.Lock` 包裹 claim——引入不必要的开销与复杂度；若未来 langgraph 改变 async 调度方式，作为后备方案记录在案。
- **配套（实现口径，2026-11-12 实施后确认）**: Budget(16,12) 全体共享，记账范围只覆盖调查员（worker）的模型与工具调用；plan 节点按任务数加码 `budget.extend(model_calls, tool_calls + 3*len(tasks))`（extend 只升不降，初始值 16/12 下不能调低模型额度，故只抬工具额度，每个子任务约 3 次查询）；synthesize 直调模型、不占预算，因此不需要 R3 草案里的"synthesize 前 extend 兜底"——`must_answer` 在并发下的失真由 worker 的强制收尾答案承载，汇总节点总能拿到材料。

## R4: 跨轮去重与上下文的矛盾

- **Decision**: 新增 findings（`{tool, target, rows, digest}` 结构化摘要，digest = row_count + 首行主键），轮末进 saved_state，轮初以 briefing 注入 supervisor 与 worker。
- **Rationale**: manual-loop 依赖跨轮 MessageLog（被去重挡掉的查询结果还在消息历史里）；multi-agent 每轮 worker 从头开始（remember=False），没有这段历史。
- **Alternatives**: 保留跨轮消息历史——上下文膨胀且与 remember=False 矛盾；模型总结摘要——先做纯结构化，不够再升级，避免额外调用。

## R5: waiting_user 实现

- **Decision**: 不用 langgraph `interrupt()`。sql 调查员的 ask_user 工具正常返回并置位 `shared.pending`（追问通道仅存在于 sql 调查员：其查询依赖用户条件；doc 调查员检索不依赖条件，不提供该工具）；worker 循环条件 `while agent.may_think() and not shared.pending`；synthesize 检查 pending 非空则跳过合成，把 question 写入 answer 并标记 waiting；run() 返回 `TurnResult(status="waiting_user")`。
- **Rationale**: 图内无协程挂起，与 SessionManager"waiting_user 本轮结束不挂起"的模型吻合；pending 置位后当前 tool 结果照常 observe 回填，保证无未配对 tool_calls（供应商会拒绝）。
- **Alternatives**: `interrupt()` + checkpointer——需要持久化 checkpointer，违反 R1；异常控制流——破坏"追问是正常返回"的语义。

## R6: 意图兜底策略

- **Decision**: 规则短路（`conditions_in` 命中业务编号 → troubleshoot，零模型调用）→ LLM 分类 → 解析失败/未调工具 → 默认 troubleshoot。
- **Rationale**: 排查是文档问答的超集（含 document 工具），路由错误代价最低；业务编号是强信号。
- **确认记录**: 需求方明确确认"默认进排查"。

## R7: 阶段命名

- **Decision**: 现有阶段 `s4` → `manual-loop`（文件 `manual_loop.py`，类 `ManualLoopWorkflow`）；新阶段 `multi-agent`（包 `multi_agent/`，类 `MultiAgentWorkflow`）；workflow id 不变（会话 identity 只绑 id+version，改名不影响旧会话判断）。
- **Rationale**: 摆脱教学序号，名字表达编排方式。需求方确认："用更清晰的名字指代，而不是只说 s4"。

## R8: 旧会话兼容

- **Decision**: 不做迁移。旧会话提交时 identity 校验自然抛 Conflict，走前端已有处理路径。
- **确认记录**: 需求方明确确认"按你说的不做迁移"。

## R9: 测试确定性

- **Decision**: 排查测试消息一律带业务编号触发规则短路（不耗 FIFO 响应）；LLM 分类用例显式排 `intent_call`；双 worker 用例用 `max_concurrency=1` 强制串行或"形状可互换"响应。fixtures.py 新增 `intent_call(intent)` 构造器。
- **Rationale**: fixtures.Model 是全局 FIFO `pop(0)`，分类调用与并行 Send 会使响应错位。
