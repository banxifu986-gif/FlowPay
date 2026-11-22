# 任务：009 harness 鲁棒性

批次与门禁见 plan.md，决策依据见 research.md，实体形状见 data-model.md，验证步骤见 quickstart.md。实施在新分支进行；每批完成后向 Ban 提交验收报告。

## Phase 1：Setup

- [ ] T000 [P] 提交门：将 `specs/009-harness-robustness/`、`.gitignore`（/docs/wiki/ 行）、`.specify/feature.json` 提交入库——并行 worktree 建立的前置门（worktree 不共享未跟踪文件；需 Ban 批准 commit）
- [ ] T001 基线快照：从 `var/benchmark/20261122-101317-2ca71c/multi-agent.jsonl` 固化 R96B 逐题对照清单（逐题 pass/evidence/断言命中），存 `specs/009-harness-robustness/baseline-r96b.jsonl`；记录总 20/96、单轮 16/32、多轮 4/64、证据 54/96、multi_hop 证据 11/16、多轮证据未中 27、run.failed=1 的归因（**synthesize 空输出**——无决策+空正文→ModelOutputError→other 档直接终态；嵌入 400 那轮经 error_report 降级正常完成，2026-11-23 审查核实）
- [ ] T002 [P] 核对环境：`.env` 密钥与 bge-m3 集合就绪；确认 `resume_run`/`_submit_run(*, resume)` 现签名（manager.py:167-173）、`TurnInput.resumed` 字段（manager.py:344）与 `_persist_run` 的 run.started 载荷 resumed 标记（resumed 轮不写 message.user 但写 run.started）；确认新配置项 `investigation_tool_budget` 无需环境覆盖通道

## Phase 2：US1（P0）分级失效处置与降级作答

- [ ] T003 [P] [US1] `backend/src/flowpay/core/execution/failures.py`：`FailureInfo` 增 `deterministic: bool = False`（约束：为 True 时 `retryable` 必为 False）；`classify_failure` 白名单——嵌入/检索结构性 4xx（400/422 输入类拒绝）、供应商参数确定性拒绝 → deterministic；429/超时/5xx/连接 → transient；**模型侧重试语义零改动**（现状 400 已归 other 档不可重试），档位消费方=工具包装层与 `tool.outage` 载荷；先写失败用例（data-model.md 白名单逐行）
- [ ] T004 [P] [US1] `backend/src/flowpay/core/execution/manager.py`：模型侧重试与终态语义零改动（`_execute` 重试点不动）；以 classify_failure 函数级输入输出断言白名单分类（不锁内部结构），并断言硬失败终态事件 category 字段与既有行为一致
- [ ] T005 [US1] `backend/src/flowpay/workflows/multi_agent/state.py`：SharedTurn 增 `tool_outages: set[str]`、`task_progress: dict[str, SubtaskProgress]`、`task_tool_calls: dict[str, int]`（形状按 data-model.md，幂等键=任务目标+worker 类型）；恢复/保存路径兼容（US2 复用）；单测
- [ ] T006 [US1] `backend/src/flowpay/workflows/multi_agent/investigation.py` 四处工具包装（`run_sql`/`run_logs`/`search_documents`/`read_document`）：调用前查 `tool_outages`——已禁用链路**立即抛失败**，由既有 catch-all error_report 收束该任务（禁用即收束，不空转）；包装内捕获自身链路异常 → `classify_failure` → deterministic 则登记 `tool_outages` + 发 `tool.outage`（载荷 `failure_class=deterministic` + 链路标识）→ 重抛走既有收束路径；**不做 catch-all 拆分**；单测覆盖"登记 → 后续调用立即失败"两步
- [ ] T007 [US1] `backend/src/flowpay/workflows/multi_agent/investigation.py` `resolve_response` 与 synthesize node：无证据子项统一"未能核实"表述（pending 存在时维持既有 clarify 优先回退，未证实口径仅约束无 pending 场景）；新增 FR-12——无决策/空正文（`ModelOutputError`）→ 确定性重试一次（DISPUTE 先例口径）→ 仍空则诚实占位文本正常完成，不 `run.failed`；单测三态（部分证据/全部工具失效/空输出重试与占位）
- [ ] T008 [P] [US1] `backend/src/flowpay/core/config/settings.py` + `backend/configs/local.toml`：`investigation_tool_budget` 配置项，默认 10；**新增一次性统计脚本（或 sqlite 直查）**：从事件库 `tool.started` 按任务分组统计单任务调用分布，作为默认值校准依据（007 机制报表无此统计，审查 C5），依据记入配置注释
- [ ] T009 [US1] 集成测试（**可证伪形态**，审查 C1——防既有 error_report 路径"空跑通过"）：①注入确定性嵌入故障 → 断言 `tool.outage` 载荷 `failure_class=deterministic`、该任务 error_report 收束、后续对该链路调用不再发起请求、无 `run.retry`、整轮正常完成；②注入 synthesize 空输出 → 断言 `model.retry` 恰一次 + 占位完成 + 无 `run.failed`；③部分证据场景断言未证实标注出现——SC-2/SC-3 契约
- [ ] T010 [US1] 验收 US1：全量容器 pytest 基线保持 + ruff/basedpyright 全绿；报告 US1 完成状态

## Phase 3：US2（P0）节点级断点续跑

- [ ] T011 [P] [US2] `backend/src/flowpay/workflows/multi_agent/investigation.py`：worker 子任务收尾写 `turn.progress` 事件（载荷按 data-model.md SubtaskProgress；幂等键去重防 Send 并行重复收尾）
- [ ] T012 [US2] `backend/src/flowpay/workflows/multi_agent/graph.py`：恢复分支——从最近 `turn.progress` 重建 SharedTurn 预填（done 键/findings/evidence；clarification 既有恢复逻辑不动）；`dispatch` 按 `task_progress` 过滤已完成子任务
- [ ] T013 [US2] `backend/src/flowpay/core/execution/manager.py` + `backend/src/flowpay/core/event_log/store.py`：resume 计量修正（审查 B1）——`daily_run_count` 过滤 `resumed=true` 的 run.started（恢复轮 run.started 照常落库、载荷已带 resumed 标记，不新增事件类型）；resume 提交仍走 `_reserve_quota` 预留并发名额（与 `_execute` finally 的 `_release_quota` 对称，防净减一个名额）；manager 恢复路径读最近 `turn.progress` 按幂等键 merge 后注入 `TurnInput.saved_state`（workflow 不碰事件库，审查 C2）
- [ ] T014 [US2] 集成测试：多子任务轮次中断→显式继续——恢复后查询键集合与断点前不交集（零重查，SC-4）；**恢复轮日回合计量为 0**（过滤生效）；并发名额恢复前后守恒；既有轮级恢复语义测试全数通过
- [ ] T015 [US2] 验收 US2：全量测试 + 报告

## Phase 4：US3（P0）未证实断言的结构化校验

- [ ] T016 [P] [US3] `backend/src/flowpay/workflows/multi_agent/citation_check.py`（新增）：结论句引用标记解析（记录编号、片段引用两种模式）+ 无标记断言确定性降级为未证实表述；只看标记存在性（宽松判定：带标记放行，真伪归编造核对机制）；先写失败用例（含误判边界）
- [ ] T017 [US3] `investigation.py` `resolve_response` 接入校验：输出前解析→降级或至多一次定向重写；未证实口径与 US1"未能核实"统一（覆盖查不到、预算收束、全部工具失效三来源）
- [ ] T018 [US3] `backend/scripts/benchmark`（`clarification.py`/`run.py`）：报表增未证实口径统计（SC-5 数据源）；不改既有指标口径
- [ ] T019 [US3] 验收 US3：全量测试 + 报告

## Phase 5：US4（P1）多Agent 最小约束包

- [ ] T020 [P] [US4] `backend/src/flowpay/workflows/multi_agent/investigation.py`：CapabilityMap 静态表（业务记录→sql_worker；规则/文档→doc_worker）+ 分发前校验——不匹配改派（`task.reassigned`）→ 重计划一次（带纠错提示）→ 仍不匹配丢弃（`task.dropped`，不影响其余任务）
- [ ] T021 [US4] 集成测试：改派成功 / 重计划一次后成功 / 丢弃且其余任务正常完成——三路径事件断言
- [ ] T022 [US4] 验收 US4：全量测试 + 报告

## Phase 6：US5（P1）多跳两段式检索

- [ ] T023 [P] [US5] `backend/src/flowpay/workflows/multi_agent/investigation.py`：`query_with_findings`——从本轮 findings 提取记录编号/关键值拼进第二跳查询（清洗沿用 ≤500 字符口径）；与 `query_with_facts` 叠加；无第一跳结论时不改变现有行为
- [ ] T024 [US5] 单测：两跳拼接、无结论不拼接、超长清洗；`scripts/benchmark/retrieval.py` 机制抽查对接（确认第二跳查询构造可见）
- [ ] T025 [US5] 验收 US5：全量测试 + 报告

## Phase 7：B6 全臂验收 + Polish

- [ ] T026 冒烟：R96B 同口径跑 `--per-type 1`（6 题）；run.failed=0、事件链路正常（含新事件形状）；实测单题耗时/token 外推 96 题预算，报告给 Ban（超外推 2 倍停手条款生效前置）；**由主线会话统一执行费控门**（并行会话不得自行放行）；**待 Ban 放行 T027**
- [ ] T027 全臂 96 题：一次出 SC-1（机制门）/SC-2（run.failed=0）/SC-5（未证实口径）/SC-6（证据 ≥50/96）/SC-7（multi_hop ≥11/16）数据；逐题对照 vs R96B（翻盘/回退清单，±4 噪声解读纪律）；**只在主 worktree 执行，产物落盘核对在主仓 `var/benchmark/` 路径**（副 worktree 的 var/ 不共享，跑错位置交付物错乱）
- [ ] T028 报表核对：SC-4 零重查事件证据（如有恢复场景）+ SC-7 第二跳机制抽查（retrieval.py 离线诊断）
- [ ] T029 质量门禁：`make quality` 全绿；失败基线为空（不滚动）
- [ ] T030 交付报告：`delivery-report.md` 落盘（SC-1~SC-8 逐项给证据、遗留观察）

## 依赖与并行

- T000 是并行 worktree 建立的前置门（提交 specs/009 + .gitignore + feature.json）。
- T005 是 US1/US2 共享前置（SharedTurn 扩展），US2 全部依赖 T005；**副轨①（US3）的切出点必须含 T007**（T017 改的就是 T007 改过的 resolve_response，切出点只含 T005 会集中爆发 rebase 冲突）。
- T003/T004/T008/T016/T020/T023 不同文件可并行；T006/T007 依赖 T005；T009 依赖 T003–T008。
- US2（T011–T015）依赖 US1 的 outage/progress 框架；US3（T016–T019）依赖 T007 的未证实口径；US4/US5 相互独立，可与 US2/US3 并行。
- T026 冒烟依赖 US1–US5 全部完成；T027 依赖 T026 报告且 Ban 放行（费控门，主线会话统一执行）。
- MVP 范围：T000–T010（US1 分级失效与降级作答可独立交付）。
