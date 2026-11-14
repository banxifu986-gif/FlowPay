# Tasks: 模型用量聚合与请求前上下文投影（pi 思想借鉴）

**Feature**: specs/002-usage-projection（spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md）

> 说明：任务按 spec.md 用户故事组织。实际执行顺序 Setup → Foundational → US1 → US2 → US4 → US5 → US3（回归验收）→ Polish，与下方依赖图一致。
> 与 plan.md 七阶段映射：Phase A = T002-T004；Phase B/C = Phase 3（US1）；Phase D/E = Phase 4/5（US2/US4）；Phase F = Phase 6（US5）；Phase G = Phase 7/8。
> 硬约束：默认策略（settings 缺省）= 现行为；**既有测试断言不得修改**；容器基线（T001 记录数字）失败集合不得扩大。
> 操作约定（2026-11-14 Ban 确认）：①提交节奏 = **每阶段一提交**，信息沿用 `类型（中文描述）` 风格，不带署名；②容器回归已授权直接执行（`docker run --rm --env-file .env …`，.env 只引用不读取其内容）；③langgraph 对齐改动（pyproject.toml/uv.lock）作为**独立 chore 先提交**落在分支最前，与本特性提交分离。

## Phase 1: Setup（基线确认）

- [x] T001 容器基线确认（2026-11-14）：`13 failed / 160 passed / 21 skipped`。13 条既有失败逐条记录：`test_benchmark_harness.py::test_chunk_alignment_skips_the_title_only_block`、`test_evidence_matches_by_heading_not_by_number`（数据集相关）+ `test_agent_loop.py` 的 manual-loop 11 条（`test_model_can_repair_its_own_request[manual-loop-unknown_tool/invalid_json/invalid_limit/boolean_limit/blank_query/truncated]`、`test_budget_is_per_turn_and_tool_history_survives`、`test_current_state_is_saved_and_collection_mismatch_is_rejected`、`test_cancel_after_success_keeps_previous_checkpoint`、`test_unknown_citation_does_not_retry_or_discard_answer`、`test_previous_workflow_version_requires_new_session`——数据源策略未配置）。该口径贯穿 T004/T023/T026

## Phase 2: Foundational（P3 fixtures 增强，无行为变化）

- [x] T002 fixtures `completion()` 加可选 `usage` 参数（backend/tests/workflows/fixtures.py；缺省 None，不改变既有用例响应形状；usage 形状见 data-model §1：prompt/completion/total_tokens）。验收：既有用例零断言修改全部通过
- [x] T003 fixtures `Model` 加可选分片开关（backend/tests/workflows/fixtures.py；`chunked_content` 正文拆多 chunk、`chunked_tool_args` 工具参数 JSON 拆 3 片发射；开启时经 ChatStreamAccumulator 重组返回；缺省关闭 = 现有单 chunk 行为）。验收：分片路径由后续用量/投影用例驱动，既有用例不受影响
- [x] T004 fixtures 回归（2026-11-14）：容器全量 `13 failed / 160 passed / 21 skipped`，失败集合与 T001 基线逐条一致，零断言修改

## Phase 3: User Story 1 — 每轮回答可见 token 用量（Priority: 高）

独立验收：构造含/缺 usage 的响应跑一轮，前端收到回答时附带用量汇总（SC-2）。

- [ ] T005 [US1] 用量提取与聚合纯函数（backend/src/flowpay/core/chat/usage_view.py：`UsageEntry`/`UsageSummary` dataclass + `from_completion`/`sum_entries`；容错口径 R-3——usage 缺失/None/空对象 → None 字段，不按 0 补齐，聚合不推测 total；导出经 core.chat __init__）
- [ ] T006 [US1] usage_view 单测（backend/tests/chat/test_usage_view.py 新建：带 usage / usage 为 None / usage 空对象 / 部分字段缺失 / 聚合含 None 位 → None / 空 entries → model_calls=0 全 None）
- [ ] T007 [US1] TurnResult 新增 `usage: UsageSummary | None = None`（backend/src/flowpay/core/execution/contracts.py；docstring 注明零模型调用轮为 None）
- [ ] T008 [US1] AgentTurn 用量累计与写入（backend/src/flowpay/core/agent/turn.py：新增 usage_entries 累计，think() 每次 chat.call 返回后提取一条，**含被 ModelOutputError 拒绝的调用**；accept()/pause() 以 sum_entries 写 TurnResult.usage；give_up 保持 None）
- [ ] T009 [US1] multi-agent 用量汇总（backend/src/flowpay/workflows/multi_agent/state.py SharedTurn 增加 usage_entries；investigation.py `investigate()` finally 并入 agent.usage_entries——pending 短路路径同样收集；route/plan/synthesize 的直连 call 调用点各自登记 entry（不经 AgentTurn，见 R-8）；graph.py run() 末尾 sum_entries 写入 TurnResult.usage）
- [ ] T010 [US1] manager 提交 usage（backend/src/flowpay/core/execution/manager.py `_execute`：message.assistant payload 增加可缺失的 `usage` 键，仅 outcome.usage 非 None 时写入；提交原子性与其余 payload 不动）
- [ ] T011 [P] [US1] session_view 用量读取（backend/src/flowpay/core/chat/types.py 的 ChatMessage 增加 usage 字段、session_view.py message.assistant 分支缺键宽容读取，缺键记 None；旧事件重放不受影响）
- [ ] T012 [US1] 用量链路契约测试（新建 backend/tests/chat/test_usage_wiring.py：manual_loop 一轮后 message.assistant payload 含 usage 且数值 = 各次调用之和；零调用轮 give_up → payload 无 usage 键；旧事件流重放缺键不出错）

## Phase 4: User Story 2 — 长会话上下文受控且引用可信（Priority: 高）

独立验收：开启策略构造超预算会话，后续请求体积受控，占位摘要编号可核查，disputed 对编造编号仍拦截（SC-3）。

- [ ] T013 [US2] 投影纯函数（backend/src/flowpay/core/chat/projection.py：`ContextPolicy`（char_budget / tool_result_budget，data-model §3）+ `project_messages`；两层机制 R-1——超限旧工具结果先占位替换（role=tool、tool_call_id 原样，content 为含 target/rows/record_ids/original_chars 的占位摘要，data-model §4），仍超限整组删除最老完整轮次；不可删集合：首条 system、任何 user、最后一段 assistant 交换；导出经 core.chat __init__）
- [ ] T014 [US2] 投影四不变量单测（backend/tests/chat/test_projection.py 新建：①配对完整——tool_call_id 无一拆散；②存活集合——首 system/任何 user/最后一段交换全部存活；③占位编号清单——被压原文编号全部出现在占位摘要 record_ids；④全函数——缺字段/配对破损/空列表原样返回不抛异常；⑤预算不可满足——不可删集合自身超预算时存活优先、尽力压缩其余，不抛异常）
- [ ] T015 [US2] think() 投影接入（backend/src/flowpay/core/agent/turn.py `think()`：请求组装改为 `[*projected, *correction, *closing]`——投影只作用于检查点原生消息，纠正草稿与收尾系统消息永不投影；policy 为 None 时短路，请求内容与现行为逐条一致）
- [ ] T016 [US2] 策略参数与配置键（backend/src/flowpay/core/agent/turn.py `start()` 加 `context_policy: ContextPolicy | None = None`；backend/src/flowpay/core/config/settings.py 新增投影预算两键，缺省/0 = None 策略 = 关闭；确切键名实现时与既有命名风格一致）
- [ ] T017 [US2] 长会话场景测试（backend/tests/chat/test_projection.py：构造超预算多轮会话断言体积受控、占位摘要可核查、checkpoint 原生消息未压缩（FR-8）、disputed 对无来源编号仍拦截）

## Phase 5: User Story 4 — 跨轮引用不再误拦（Priority: 中）

独立验收：恢复检查点后回答引用历史轮编号不触发 disputed；编造编号仍拦截（SC-4）。

- [ ] T018 [US4] seen 重建修复（backend/src/flowpay/core/agent/turn.py `__post_init__`：并集增加历史轮 tool 消息 content 编号；assistant 历史消息仍不计入；disputed() 判定规则不动；R-5）
- [ ] T019 [US4] 跨轮引用回归测试（backend/tests/workflows/ 相应测试文件新增用例：上一轮工具返回编号 → 恢复检查点 → 本轮引用不误拦；无来源编号仍触发改写）

## Phase 6: User Story 5 — 并行调查下的投影隔离（Priority: 中）

独立验收：multi-agent 并行下各 worker 投影互不影响，model.request 如实记录投影后请求体（SC-5）。

- [ ] T020 [US5] 两阶段 opt-in 装配（backend/src/flowpay/workflows/manual_loop.py `AgentTurn.start` 调用点、backend/src/flowpay/workflows/multi_agent/investigation.py 两个 worker 调用点：从 settings 读策略透传 context_policy；关闭时传 None）
- [ ] T021 [US5] Send 并发隔离测试（backend/tests/workflows/test_multi_agent.py 新增：并行两 worker 各自超预算，断言双方 model.request 互不污染、共享 budget/findings/retrieved 不受投影影响、各自请求如实记录）
- [ ] T022 [US5] multi-agent 长会话场景（opt-in 后多轮并行调查：体积受控 + message.assistant usage = 全部 worker 与 synthesizer 调用之和）

## Phase 7: User Story 3 — 默认行为完全不变（Priority: 高，回归验收）

独立验收：缺省配置下全量既有用例原样通过、请求内容与现行为一致（SC-1）。依赖 US1/US2/US4/US5 全部落地。

- [ ] T023 [US3] 缺省配置全量回归（容器口径：既有用例原样通过，**零断言修改**；失败集合与 T001 基线逐条一致）
- [ ] T024 [US3] 现行为抽断（新增测试：settings 关闭投影时，多轮用例的 Model.requests 与关闭前逐条一致；usage 键在缺省下同样写入 message.assistant——用量可见不依赖投影开关）

## Phase 8: Polish（质量门禁与收尾）

- [ ] T025 质量门禁全绿，按组件口径执行：pytest 走容器全量（Makefile 的 VENV 是 Unix 路径，Windows 宿主机不可直跑 `make test`/`make quality`）；ruff + basedpyright 宿主机或容器执行均可（宿主机 ruff 快查时 EXE002 误报忽略——Windows 挂载文件全带执行位）；前端构建 `npm --prefix frontend run build` 宿主机执行
- [ ] T026 容器全量回归终验：失败集合与 T001 基线逐条一致无扩大；新增用例全绿
- [ ] T027 手工冒烟（可选，per quickstart）：opt-in 长排查会话——model.request 体积受控、占位摘要保留 record_ids、前端 usage 展示、跨轮引用不误拦
- [ ] T028 收尾：tasks.md 复选框回填、计划文档与记忆状态更新

## 依赖与执行顺序

```
T001 ─→ T002 ─→ T003 ─→ T004 ─→ T005 ─→ T006 ─→ T007 ─→ T008 ─→ T009 ─→ T010 ─→ T012
                                                                    │（T011 可与 T010 并行）
                                                            T013 ─→ T014 ─→ T015 ─→ T016 ─→ T017
                                                                                                    │
                                                                            T018 ─→ T019 ─→ T020 ─→ T021 ─→ T022 ─→ T023 ─→ T024 ─→ T025 ─→ T026 ─→ T027/T028
```

- US1（T005-T012）与 US2（T013-T017）文件不相交，可交错并行；US4 独立于投影，T018 可与 Phase 4 并行（同文件 turn.py 需串行提交）。
- US3（T023/T024）必须最后：它是全部功能落地后的回归验收门。

## 并行执行示例

- US1 内部：T010（manager.py）与 T011（types.py/session_view.py）不同文件，可并行。
- 跨故事：T005-T006（usage_view）与 T013-T014（projection）完全独立，可两路并行。

## 实施策略

- **MVP = US1**（T001-T012）：独立可交付——用量可见不依赖投影，先合入先见效。
- US2 投影默认关闭合入（T013-T017），US5（T020-T022）开启试点。
- 每阶段结束跑 T001 口径的容器回归，失败集合扩大即停下排查，不得带病前进。
