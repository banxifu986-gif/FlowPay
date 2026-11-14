# Tasks: 上下文管理——LLM 摘要式压缩、直连节点护栏与会话内记忆治理

**Feature**: specs/003-context-management（spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md）

> 说明：任务按 spec.md 用户故事组织。实际执行顺序 Setup → US1（压缩）→ US2（直连护栏）→ US3（P0 回归验收+真机冒烟）→ US4（P1）→ US5（P2）→ Polish，与下方依赖图一致。
> 与 plan.md 七阶段映射：Phase A/B = Phase 3（US1）；Phase C = Phase 4（US2）；Phase D = Phase 5（US3）；Phase E = Phase 6（US4）；Phase F = Phase 7（US5）；Phase G = Phase 8。
> 硬约束：治理全部 opt-in（settings 缺省 0 = 现行为）；**既有测试断言不得修改**；容器基线（T001 记录数字）失败集合不得扩大；core 新模块只依赖 stdlib/openai 类型/flowpay.core.json（test_boundaries 强制）。
> 操作约定：①提交节奏 = **每批次一提交**（P0/P1/P2 各一批），信息沿用 `类型（中文描述）` 风格，不带署名；②容器回归已授权直接执行（002 先例）；③真机冒烟门禁分级——P0 必做（校准），P1/P2 尽力/补做；④TDD：每个实现任务前对应的测试任务先行（全红→实现→全绿）。

## Phase 1: Setup（基线确认）

- [x] T001（2026-11-14）：分支 feat/003-context-management；容器基线 13 failed / 187 passed / 21 skipped，13 条失败与 002 收官口径逐条一致（manual-loop 11 条数据源策略未配置 + 数据集相关 2 条） 分支创建 `feat/003-context-management` + 容器全量 pytest 基线确认，失败集合逐条记录（沿 002 T001 格式：失败用例全名列表）。该口径贯穿 T008/T013/T016/T020/T022

## Phase 2: Foundational

无共享阻塞前置——ContextPolicy 载体已存在，配置链归入 US1；clip_text 归入 US2。

## Phase 3: User Story 1 — 长排查历史的 LLM 摘要式压缩（Priority: 高，P0）

独立验收：开启压缩构造超触发线会话，检查点消息流出现至多一条摘要信封，被压缩编号由代码提取完整保留，跨轮引用 0 误拦，摘要调用不占预算但 usage 可对账（SC-2/SC-5/SC-8）。

- [x] T002：backend/tests/chat/test_compaction.py 十条用例（判定/触发/切点/信封/截断/单摘要/prompt/畸形/组合不变量）；红态由模块不存在导入错误构成，实现后全绿 [US1] compaction 纯函数测试先行（backend/tests/chat/test_compaction.py 新建六类：①`is_summary_message` 判定 + 畸形 content/缺 kind/非 JSON 返回 False；②`select_compaction_range`——切点整组不拆配对、保留集合（最近 2 个完整交换/全部 user/首条 system/既有摘要/最后一段交换）、不足返回 None；③`compact_messages`——信封形状（kind/text/record_ids/rounds）、替换不追加、流内至多一条、record_ids 由代码从被删消息提取且排序去重；④二次合并——`summary_user_prompt` 含 `<previous-summary>` 与上限声明；⑤畸形输入原样返回/None；⑥摘要后对消息流跑投影的组合不变量——配对完整、存活集合含摘要、畸形原样返回）。验收：全红
- [x] T003 [US1] compaction 纯函数实现（backend/src/flowpay/core/chat/compaction.py：四个纯函数 + SUMMARY_PROMPT/SUMMARY_INPUT_CAP/KEEP_RECENT_ROUNDS=2 常量；`core/chat/__init__.py` 公共导出）。验收：T002 全绿
- [x] T004 [US1] 配置链（core/chat/projection.py ContextPolicy 增 `compaction_trigger_chars: int = 0`、`summary_char_budget: int = 4000`；core/config/settings.py 增 context_compaction_trigger_chars/context_summary_char_budget 缺省 0/4000；bootstrap 装配透传，缺省 0=关闭）。验收：缺省配置全量回归与 T001 基线逐条一致
- [x] T005：backend/tests/workflows/test_agent_compaction.py 五条（未触碰/触发时序/计费口径/restore 兼容+disputed 放行/失败跳过） [US1] 编排接入测试先行（backend/tests/workflows/test_agent_compaction.py 新建：①未配置/remember=False 时请求逐字一致；②开启+超线——首请求含摘要信封、切点前历史消失、最近交换原样；③摘要调用不入 spent_model_calls 但入 usage_entries（FIFO 受控 Model 多排一条摘要响应）；④checkpoint 含摘要、restore 后下一轮正常续跑且 disputed 对被压缩编号不误拦；⑤摘要调用失败（FIFO 排入异常响应）→ 消息流不动、trace 留痕、本轮照常）。验收：全红
- [x] T006 [US1] turn.py 编排实现（`_compact_if_needed`：think() 入口、投影之前；显式 `self.remember` 判断；触发→`chat.call(..., output="internal")` 摘要→超限正文代码截断→`compact_messages` 替换→trace `context.compacted`/`context.compaction_failed`；`known_record_ids` 扩扫信封 record_ids 结构化字段，判定规则不变）。验收：T005 全绿
- [x] T007（2026-11-14）：容器全量 13 failed / 209 passed / 21 skipped，失败集合与 T001 逐条一致，零断言修改 [US1] 缺省回归（US1 收口）：容器全量与 T001 基线逐条一致，零断言修改

## Phase 4: User Story 2 — 直连调用点的上下文护栏（Priority: 高，P0）

独立验收：policy=None 五节点输出与现拼接逐字一致；开启后变量段超限截断带显式标记、question 原样（SC-4）。

- [x] T008 [US2] clip_text 测试先行（backend/tests/chat/test_projection.py 扩：未超限原样返回/超限尾部截断+标记含截断字符数与「可按记录编号重查」提示/空串与 0 预算边界）。验收：全红
- [x] T009 [US2] clip_text 实现（backend/src/flowpay/core/chat/projection.py + CLIP_MARKER 常量）。验收：T008 全绿
- [x] T010 [US2] 五节点接入（backend/src/flowpay/workflows/multi_agent/graph.py 闭包捕获 context_policy 并透传 route；investigation.py plan 简报/synthesize 简报+每份报告（读取端上限 4096 模块常量）；simple.py doc_qa 检索片段/chitchat；**格式 f-string 留在调用点**，policy 存在时仅对变量段调 clip_text）。验收：T011 全绿
- [x] T011：policy=None doc_qa/plan/synthesize 逐字一致（含本轮 findings 简报的确定性形状）；开启后片段/简报/报告截断带标记、question 原样 [US2] optin 用例扩（backend/tests/workflows/test_projection_optin.py：①policy=None 五节点与现拼接逐字一致；②开启后超长简报/报告/片段截断带标记、question 原样；③synthesize 多报告合成正常）。验收：全绿；缺省回归与 T001 基线一致

## Phase 5: User Story 3 — 默认行为完全不变（Priority: 高，P0 回归验收）

独立验收：全量测试原样通过 + 真机冒烟校准落盘（SC-1/SC-8）。

- [x] T012（2026-11-14）：容器全量 13 failed / 209 passed / 21 skipped，失败集合与 T001 逐条一致 [US3] 全量容器回归基线核对（失败集合与 T001 逐条一致；US1/US2 的缺省逐字一致断言专项核对）
- [x] T013 [US3] 冒烟脚本（scripts/smoke_003_context.py：六轮长会话 + 事件库分析——压缩事件/体积曲线/信封编号完整性断言；ruff+basedpyright 双检通过）（scripts/smoke_003_context.py 新建：六轮长会话——问候/文档问答/排查开场/补条件/结合条件继续排查/追问历史编号核对；断言压缩发生、摘要信封在事件流、体积曲线输出）
- [x] T014 [US3] 真机冒烟执行（2026-11-15 三组实测：30000 推荐试点零触发/语义正确；8000 机制验证 11 次压缩 0 失败、编号跨压缩链累积 4→204→209、disputed 拦一次编造；multi-agent 暴露直连口径差距记入 P1；数据回填 quickstart.md）（**P0 必做**）+ scripts/analyze_smoke.py 校准（compaction_trigger_chars/summary_char_budget 三组对照：关/松/紧）+ 数据回填 specs/003-context-management/quickstart.md

## Phase 6: User Story 4 — 子 agent 输出预算与失败证据（Priority: 中，P1）

独立验收：报告产出端有界带标记；调查者故障/预算耗尽产出说明性报告，synthesize 照常作答（SC-6）。

- [x] T015 [US4] 测试先行（backend/tests/workflows/test_multi_agent.py 四条：故障转错误报告/产出端截断/预算耗尽 findings 切片/取消穿透——CancelModel 桩）（backend/tests/workflows/test_multi_agent.py 扩：①报告超 report_char_budget 截断带标记；②worker 内部故障 → 错误报告含 error_type/message/任务目标，其余 worker 照常、synthesize 产出部分结论；③预算耗尽无报告 → findings 切片预算说明报告；④CancelledError 穿透不被吞）。验收：全红
- [x] T016 [US4] 实现（investigate 重构：故障/耗尽转说明报告不让整轮失败，报告产出端 clip_text；ContextPolicy.report_char_budget+settings+bootstrap；claim 话术细化保「已经执行过」锚定子串；worker PROMPT 加简短结论要求；含 Ban 批准的直连口径修正——synthesize/plan/doc_qa 变量段改剩余分配）（investigation.py：产出端 clip_text + except Exception 转错误报告 + 无报告 findings 切片说明报告；SQL/DOC_WORKER_PROMPT 加「结论摘要保持简短、引用编号不罗列数据」；claim 拒绝话术细化为「换更精确条件重查」；ContextPolicy 增 `report_char_budget: int = 4096` + settings 键 + T010 读取端常量迁入 policy）。验收：T015 全绿
- [x] T017 [US4] 容器回归（13 failed / 213 passed / 21 skipped 基线保持，净增 4 条 P1 测试）+ 冒烟尽力（直连剩余分配生效：max request 43664→13957）（与 T001 基线一致）+ 冒烟尽力（人为故障场景真机跑通则回填 quickstart，不作为硬门禁）

## Phase 7: User Story 5 — 会话内记忆治理（Priority: 中，P2）

独立验收：findings ≤ cap 且 discard_stale 对 merged 条目命中；evidence 淘汰不影响当轮展示（SC-7）。

- [x] T018 [US5] findings 合并测试先行（backend/tests/workflows/test_multi_agent.py 两条：合并形状+子串匹配+rows 求和+digest 截断 / 不超 cap 原样返回）（backend/tests/workflows/test_multi_agent.py 扩：超 20 条最旧合并为一条/merged.target 含全部成员 target（discard_stale 子串命中）/rows 求和/digest 拼接截断/不超 cap 不动）。验收：全红
- [x] T019 [US5] merge_old_findings 实现（state.py：FINDINGS_CAP=20/MERGED_DIGEST_CAP=2000 常量不进配置面——审核砍多余可配置化；graph.py checkpoint 组装前调用）（backend/src/flowpay/workflows/multi_agent/state.py：`FINDINGS_CAP=20` 常量 + 纯函数；graph.py checkpoint 组装前调用）。验收：T018 全绿
- [x] T020 [US5] PassageSet.prune 测试先行 + 实现（backend/tests/core/test_answer_contract.py 三条；prune(max_passages, max_chars) 按登记序保最新；接线两处——AgentTurn.start 恢复时与 graph.py 恢复后；上限 EVIDENCE_MAX_PASSAGES=200/EVIDENCE_MAX_CHARS=40000 常量，不引入 policy 字段——与文档差异注记见下方）（backend/tests/core/test_knowledge.py 扩：按登记序保最新/返回淘汰数/上限内不动；core/knowledge/passages.py 实现 `prune(max_passages, max_chars)`；接线调用两处——manual-loop 的 AgentTurn.start（remember=True 恢复时）与 multi-agent 的 graph.py run()（从 saved_state.evidence 恢复 shared.retrieved 后），试点上限冒烟后定）。验收：全绿
- [x] T021 [US5] 容器回归（2026-11-15：13 failed / 218 passed / 21 skipped 基线保持，净增 5 条 P2 测试）+ 冒烟尽力（multi-agent 六轮真机：findings 封顶生效、直连剩余分配 max request 43664→13957）（与 T001 基线一致）+ 冒烟尽力

## Phase 8: Polish（002 修订标注与收尾）

- [x] T022 [P] 002 supersedes 标注 10 处（2026-11-14 完成：spec.md FR-8/US2/FR-3/FR-10/范围外、data-model、plan、research R-5 与被拒备选、interfaces §5、tasks T024；仅加注不改历史原文）（specs/002-usage-projection/：spec.md FR-8/US2/FR-3/FR-10/范围外、data-model.md、plan.md、research.md R-5 与被拒备选、contracts/interfaces.md §5——各处加注「003 已修订，见 specs/003-context-management/spec.md 对 002 的修订节」；tasks.md T024 仅加注不改内容；不改写历史原文）
- [x] T023 批次提交整理（6049524 P0 / 370b5ee P1 / 6e0dda9 P2+Polish，每批 make quality 门禁 + 容器回归通过）：P0（T001-T014）/ P1（T015-T017）/ P2+Polish（T018-T023）各一批，`make quality` 全绿后按操作约定提交

## 依赖与执行顺序

```
T001 → T002 → T003 → T004 → T005 → T006 → T007
                                    ↓
                              T008 → T009 → T010 → T011
                                                  ↓
                                          T012 → T013 → T014（P0 收口）
                                                        ↓
                                          T015 → T016 → T017（P1）
                                                        ↓
                                          T018 → T019 → T020 → T021（P2）
                                                        ↓
                                          T022 → T023（Polish）
```

严格串行为主：US2 依赖 US1 的 ContextPolicy 扩展（T004）；US3 依赖 US1+US2 全部接线；P1/P2 依赖 P0 配置链。唯一并行点：T022（002 加注）与代码无耦合。

## 并行执行示例

- T022 可与 T015-T021 任一阶段并行（纯文档加注，不碰代码）
- T008（clip_text 测试）可在 T006 完成后与 T005 的收尾并行推进（不同文件）

## 实施策略

- **MVP = Phase 3（US1）**：压缩纯函数 + 编排接入完成后，长会话的核心问题（证据丢失/检查点无界）已解决一半；US2 补齐直连缺口后 P0 即可独立交付验收
- 每批次提交前必须：容器全量回归（基线不扩大）+ `make quality`；P0 批次额外真机冒烟
- 全程不做：slim_for_checkpoint、recall 工具、token 触发线、压缩重试（见 spec 范围外与 research R-8）
