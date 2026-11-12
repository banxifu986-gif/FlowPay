# Tasks: LangGraph 多 Agent 编排（意图路由 + 并行协作排查）

**Feature**: specs/001-langgraph-multi-agent（spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md）
**实施状态**: 全部完成并验收通过（2026-11-12）。复选框记录实际完成情况；每条附验收口径。

> 说明：任务按 spec.md 用户故事组织。实际执行顺序为 Setup → Foundational → 意图（US1/US2 的路由前提）→ 各故事 → 收尾，与下方依赖图一致。

## Phase 1: Setup（对应实施 Phase A，无行为变化）

- [x] T001 创建分支 `feat/langgraph-s5`，添加依赖 `langgraph>=0.4,<0.6`（backend/pyproject.toml、uv.lock；锁定小版本区间防 Send/conditional edge API 漂移）
- [x] T002 阶段改名：`s4.py` → `manual_loop.py`，`S4Workflow` → `ManualLoopWorkflow`（backend/src/flowpay/workflows/manual_loop.py），注册名 `s4` → `manual-loop`（backend/src/flowpay/router.py）；workflow id 不变 `flowpay-multi-turn-rag`（FR-12）
- [x] T003 同步受影响测试并确认容器全量基线（backend/tests/workflows/test_manual_loop.py 等；基线 13 failed / 144 passed / 17 skipped，失败均为既有数据源配置问题，此口径贯穿后续所有验证）

## Phase 2: Foundational（对应实施 Phase B，骨架）

- [x] T004 图状态与共享资源：`GraphState`（reports 用 `operator.add`）、`SharedTurn`（case/done/findings/retrieved/budget/actions/pending）、`InvestigationTask`/`WorkerReport`/`Finding`、`Node` 协议（backend/src/flowpay/workflows/multi_agent/state.py）
- [x] T005 图骨架与阶段入口：`MultiAgentWorkflow` + 条件边分支（backend/src/flowpay/workflows/multi_agent/graph.py），注册 `multi-agent`（backend/src/flowpay/router.py）；节点一律 `async def`（R3）
- [x] T006 骨架冒烟测试（backend/tests/workflows/test_multi_agent.py：规则短路零模型调用）

## Phase 3: User Story 1 — 闲聊轻量回应（P1）（对应实施 Phase C/D 前半）

- [x] T007 [US1] 意图识别：`IntentDecision`（extra=forbid，reason 1..200 非空白）+ `SET_INTENT` 工具定义（Schema 由 pydantic 生成）+ `read_intent`（未调工具/解析失败返回 None）+ `conditions_route` 规则短路（backend/src/flowpay/workflows/multi_agent/intent.py；R2/R6）
- [x] T008 [US1] route 节点接入分类链：规则短路（零模型调用，SC-1）→ LLM 分类（`output="internal"` 不进答案投影）→ 兜底 troubleshoot；trace 事件 `intent.decided`（backend/src/flowpay/workflows/multi_agent/graph.py）
- [x] T009 [US1] 闲聊节点：`chitchat_node` 直调模型、零工具零检索、不经过 AgentTurn（backend/src/flowpay/workflows/multi_agent/simple.py）；SC-2 测试断言请求不带 tools 且无检索（backend/tests/workflows/test_multi_agent.py）
- [x] T010 [US1] fixtures `intent_call`（backend/tests/workflows/fixtures.py；R9：LLM 分类用例显式排 intent_call，不与 FIFO 错位）

## Phase 4: User Story 3 — 并行调查（P1）（对应实施 Phase E，最重）

- [x] T011 [US3] 模块级纯同步临界区 `claim(shared, tool, target)`：check-then-act 间无 await，靠单事件循环原子性；跨轮去重键 `tool:target.casefold()`（backend/src/flowpay/workflows/multi_agent/investigation.py；R3/FR-5/SC-6）
- [x] T012 [US3] 结论摘要与简报：`row_digest`/`passage_digest`（行数+首行首字段 / 片段数+首片编号，纯结构化不做模型总结）+ `turn_briefing`（已知条件 + findings，R4/FR-6）
- [x] T013 [US3] 协调者 `plan_node`：`SET_TASKS` 结构化调用（SupervisorPlan，extra=forbid），解析失败按空任务兜底；`budget.extend(model, tools + 3*len(tasks))` 加码（backend/src/flowpay/workflows/multi_agent/investigation.py）
- [x] T014 [US3] Send 分发条件边 `dispatch`：按 kind 分发 sql_worker/doc_worker，空任务直达 synthesize（backend/src/flowpay/workflows/multi_agent/graph.py；FR-4）
- [x] T015 [US3] 双调查员：`sql_worker_node`（SQL_TOOLS/LOG_TOOLS + ask_user，复用 manual_loop 工具表与条件机制）与 `doc_worker_node`（检索/整篇读回，片段登记共享 PassageSet）；共享 `investigate` 循环 `while may_think() and not pending`（backend/src/flowpay/workflows/multi_agent/investigation.py）
- [x] T016 [US3] 汇总 `synthesize_node`：pending 非空直接以追问结束（waiting_user，R5）；否则合并报告 + 简报合成
- [x] T017 [US3] 图组装与检查点：`run()` 构建 SharedTurn（含跨轮恢复）→ ainvoke → 组装 `case/queried/findings/evidence/stop_reason` 检查点（backend/src/flowpay/workflows/multi_agent/graph.py；FR-11）
- [x] T018 [US3] 受控测试：并行耗时对照 SC-3（SlowSearch 延时）、同轮去重 SC-6、文档调查带证据与 findings 落检查点（backend/tests/workflows/test_multi_agent.py、fixtures SlowSearch/tasks_call）

## Phase 5: User Story 4 — 跨轮状态与意图切换（P1）（对应实施 Phase E/F）

- [x] T019 [US4] 跨轮恢复：`restore_findings`（缺键宽容）+ `Case.restore`/`restore_queried`/`PassageSet.restore` 于 run() 装配（backend/src/flowpay/workflows/multi_agent/state.py、graph.py；FR-10/FR-11）
- [x] T020 [US4] 追问通道：sql worker `ask_user` 置位 `shared.pending`；已提供字段退回 notice 不再发出（复用 manual_loop 语义）；追问后本轮其余工具结果照常回填（FR-7）
- [x] T021 [US4] 受控测试：意图切换（排查→闲聊→排查）条件保留且简报注入 SC-5；等待后补条件不重复索要 FR-7；pending 置位后其余 worker 自然收束 R5（数据集门控用例带 `snapshot_ready` skipif，与 test_manual_loop 口径一致）

## Phase 6: User Story 2 — 文档问答带引用（P2）（对应实施 Phase D 后半）

- [x] T022 [US2] `doc_qa_node`：prepare_query 检索一次 + 片段入共享 PassageSet + 单次作答带片段编号引用；零命中直接明示依据不足、不消耗模型调用（backend/src/flowpay/workflows/multi_agent/simple.py；FR-9）
- [x] T023 [US2] `TurnResult.evidence` 接通：`run()` 返回 `shared.retrieved.all()`，界面按证据清单做引用展开（引用编号协议层不改写正文，与答案契约一致）
- [x] T024 [US2] 受控测试：带证据作答（断言消息 evidence 含命中片段编号）与零命中明示不足（backend/tests/workflows/test_multi_agent.py）

## Phase 7: Polish（对应实施 Phase F 收尾 + G + H）

- [x] T025 默认阶段切换：settings 默认 `multi-agent`（FR-13）、cli/Makefile/compose/CLAUDE.md/local.toml 注释同步；routing 测试补 multi-agent 参数与默认值测试；全仓 s4/s5 序号清零（backend/src/flowpay/core/config/settings.py、cli.py、Makefile、compose.yaml 等）
- [x] T026 质量门禁全绿：ruff check/format + basedpyright 0 error 0 warning（含授权修复的 scripts 既有债务：20 处 ruff、5 处 pyright warning、22 文件 format）；langgraph 标注不全在 graph.py 用文件级 `# pyright: reportUnknownMemberType=false` 注明原因关闭
- [x] T027 容器全量回归：13 failed / 157 passed / 20 skipped，失败集合与 T003 基线逐条一致，无扩大
- [x] T028 HTTP 冒烟（quickstart 五场景 + 追问后继续）：真实模型六轮全绿——intent.decided×6、SQL 24 次/文档检索 14 次、13 条去重拦截（SC-6 生效证据）、waiting_user 追问与补条件续查、findings/queried/case/evidence 检查点五键齐全；模型走火山 coding plan 端点 `https://ark.cn-beijing.volces.com/api/coding/v3` + `deepseek-v4-flash-ga-260731`

## Post-Review Fixes（2026-11-12 代码审查后回流）

- [x] R01 [HIGH] synthesize 按 shared.seen 核对回答中的记录编号，编造回传纠正重试一次（SC-4 防线接回）
- [x] R02 [MEDIUM] 意图规则短路只认业务编号前缀，环境词不参与路由（FR-2/FR-9）
- [x] R03 [MEDIUM] 改口换账户/环境时 discard_stale 作废旧 scope 的 findings 与 done 键（US4）
- [x] R04 [MEDIUM] SupervisorPlan.tasks 上限 4，超限按空任务兜底
- [x] R05 [LOW] 契约措辞（findings 条目级严格）、测试断言钉准调查员请求、计时测试形状断言、data-model budget 行描述

## Dependencies

```text
T001 → T002 → T003（Phase A 串行，全绿后进骨架）
T003 → T004/T005/T006（Foundational）
T006 → T007/T008 → T009（US1 依赖意图链路）
T008 → T022（US2 依赖路由；与 T009 无依赖，可并行）
T006 → T011..T018（US3 依赖骨架；T013 依赖 T011/T012，T014 依赖 T013，T017 依赖 T014-T016）
T017 → T019 → T020/T021（US4 依赖 US3 的图组装与检查点）
全部 → T025 → T026 → T027 → T028（Polish 串行收尾）
```

## Parallel Execution Examples

- US1 内：T009（simple.py）与 T010（fixtures.py）改不同文件，可并行
- US3 内：T015 的 sql/doc worker（同文件不同函数，实际串行编辑）；T018 各测试用例相互独立
- US1（T009）与 US3（T011 起）分属不同文件，理论可并行；实际按依赖顺序执行

## Implementation Strategy

- MVP = US1（闲聊 + 意图路由）：一次结构化分类即可独立验收
- US3 是核心增量：plan → Send 并行 → synthesize 的骨架先行，worker 内部机制（claim/简报/追问）逐个钉住
- 所有受控测试用 fixtures FIFO 模型（R9 口径），真实模型只在 T028 冒烟出现
