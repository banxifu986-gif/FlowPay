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

- [x] T005 [US1] 用量提取与聚合纯函数（backend/src/flowpay/core/chat/usage_view.py：`UsageEntry`/`UsageSummary` dataclass + `from_completion`/`sum_entries`；容错口径 R-3——usage 缺失/None/空对象 → None 字段，不按 0 补齐，聚合不推测 total，空序列记 None）。验收：T006 七条单测全绿
- [x] T006 [US1] usage_view 单测（backend/tests/chat/test_usage_view.py 新建 7 条：带 usage / None / 空对象 / 部分缺失 / 全量求和 / 缺失位 None / 空 entries）。验收：容器 7 passed
- [x] T007 [US1] TurnResult 新增 `usage: UsageSummary | None = None`（backend/src/flowpay/core/execution/contracts.py；docstring 注明零模型调用轮为 None）。验收：编译与类型检查随 T026
- [x] T008 [US1] AgentTurn 用量累计与写入（turn.py：usage_entries 字段，think() 每次 chat.call 后提取——**含被 ModelOutputError 拒绝的调用**；accept()/pause() 写 sum_entries；give_up 保持 None）。验收：T012 拒绝重答用例
- [x] T009 [US1] multi-agent 用量汇总（state.py SharedTurn.usage_entries + record_usage()；investigation.py `investigate()` finally 并入——pending 短路同样收集；route/plan/synthesize/直答节点调用点各自登记；graph.py run() 汇总写入）。验收：T012 五调用点用例
- [x] T010 [US1] manager 提交 usage（manager.py `_execute`：message.assistant payload 增加可缺失的 `usage` 键，仅 outcome.usage 非 None 时写入；提交原子性不动）。验收：T012 端到端
- [x] T011 [P] [US1] session_view 用量读取（types.py ChatMessage 增加必填 `usage: JsonObject | None`；session_view 两个分支缺键宽容，缺键记 None）。验收：T012 旧事件重放用例
- [x] T012 [US1] 用量链路契约测试（backend/tests/chat/test_usage_wiring.py 新建 4 条；manual-loop 全链路被数据源门控，改走 multi-agent 轻路径：直答求和 / 全缺 usage 记 None / 五调用点含被拒重答 / 旧事件缺键宽容）。验收：容器 4 passed；全量 13 failed / 171 passed / 21 skipped，失败集合与 T001 基线逐条一致

## Phase 4: User Story 2 — 长会话上下文受控且引用可信（Priority: 高）

独立验收：开启策略构造超预算会话，后续请求体积受控，占位摘要编号可核查，disputed 对编造编号仍拦截（SC-3）。

- [x] T013 [US2] 投影纯函数（backend/src/flowpay/core/chat/projection.py：`ContextPolicy` + `project_messages`；两层机制 R-1——占位替换优先（target 回查自前驱 assistant 的工具名、rows 解析自原文、record_ids 用共享模式扫描），仍超限整组删最老可删轮次；record_ids 挪至 core/chat/record_ids.py 消解 turn↔projection 循环依赖，turn.py 保留同名导入）。验收：T014 六条全绿
- [x] T014 [US2] 投影不变量单测（backend/tests/chat/test_projection.py 6 条：①占位替换形状与编号清单；②配对完整（含删除路径）；③存活集合；④畸形输入五类原样返回；⑤预算不可满足存活优先；⑥长会话体积有界）。验收：容器 6 passed
- [x] T015 [US2] think() 投影接入（turn.py：请求组装 `[*projected, *correction, *closing]`——投影只作用于检查点原生消息，纠正草稿与收尾消息原样跟随；policy None 短路 = 现行为）。验收：默认关闭全量回归逐条一致（T023/T026）
- [x] T016 [US2] 策略参数与配置键（turn.py start() 加 context_policy；settings.py 增 context_char_budget/context_tool_result_budget 缺省 0=关闭；router.StageFactory 扩一位、create_stage 透传；bootstrap 两键都>0 才构造策略；manual_loop/multi_agent 两阶段透传至 AgentTurn.start）。验收：缺省配置全量回归逐条一致
- [x] T017 [US2] 长会话场景测试（test_projection.py::test_long_session_volume_is_bounded：20 轮大结果 → 体积受控 ≤ char_budget、输入深拷贝比对原样、存活集合原样、占位 record_ids 可核查；disputed 语义与投影正交——占位暴露编号即其可核查面）。验收：容器 passed

## Phase 5: User Story 4 — 跨轮引用不再误拦（Priority: 中）

独立验收：恢复检查点后回答引用历史轮编号不触发 disputed；编造编号仍拦截（SC-4）。

- [x] T018 [US4] seen 重建修复（turn.py：`given_record_ids` 扩展为 `known_record_ids`——并集增加历史轮 tool 消息 JSON 正文的编号；assistant 历史消息仍不计入；disputed() 判定规则不动；R-5）。验收：T019 两条用例
- [x] T019 [US4] 跨轮引用回归测试（backend/tests/workflows/test_seen_restore.py 新建：恢复检查点后用户编号与历史轮工具返回编号均在 seen（修复点）、assistant 历史文本编号不计入）。验收：容器 2 passed；全量 13 failed / 179 passed / 21 skipped，失败集合与 T001 基线逐条一致

## Phase 6: User Story 5 — 并行调查下的投影隔离（Priority: 中）

独立验收：multi-agent 并行下各 worker 投影互不影响，model.request 如实记录投影后请求体（SC-5）。

- [x] T020 [US5] 两阶段 opt-in 装配（与 T016 合并落地：settings→bootstrap→router→manual_loop/multi_agent→workers→AgentTurn.start 全链穿透，关闭时传 None）。验收：缺省全量回归逐条一致 + T021 开启路径
- [x] T021 [US5] Send 隔离测试（backend/tests/workflows/test_projection_optin.py::test_worker_requests_are_projected_and_isolated：两 docs worker 经 Send 并行分发（max_concurrency=1 固定 FIFO 断言时序）、各请求只含自己 objective、非存活 search 结果占位替换而最后一段交换原样、九调用点 usage 汇总）。验收：容器 passed
- [x] T022 [US5] multi-agent 长会话场景（同文件 test_usage_is_per_turn_independent：两轮会话（文档问答 2 调用 + 排查 4 调用）各自 TurnResult.usage 独立汇总，不跨轮累计）。验收：容器 passed；全量 13 failed / 181 passed / 21 skipped，失败集合与 T001 基线逐条一致

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
