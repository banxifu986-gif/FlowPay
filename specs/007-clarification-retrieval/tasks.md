# 任务：007 多轮澄清覆盖与文档召回

批次与门禁见 plan.md。每个 US 完成后跑该 US 验收项，通过再进下一个；Pi 实施，Claude 按批审核。

## Phase 1：Setup

- [x] T001 复核基线快照：读取 `var/benchmark/20261117-071251-67e7d4/report.md` 与逐题 jsonl，记录 C 臂四项指标（23/96、32/96、多轮 14/64、单轮 9/32）及事件口径等待轮（C 总 101 / 多轮 79 / 单轮 22 / 多轮最终 8；A 对应 101 / 81 / 20 / 7），报告给 Claude；由 Claude 回填 plan.md 基线附录（已完成）
- [x] T002 [P] 建测试骨架：只在本批实现行为时新建对应测试文件，不为未来批次预留长期红灯；批末容器 pytest 失败集合必须为空。`test_clarification.py` 本批已建，另两个测试文件在 T020/T029–T032 各自批次内先红后绿
- [x] T003 核对 `.env` 模型与 `backend/configs/local.toml` 检索/治理配置：确认 `FLOWPAY_MODEL_NAME=deepseek-v4-1-flash-economy-261017`、混合检索开启；不改默认值，记录到交付报告

## Phase 2：Foundational（阻塞所有产品用户故事）

- [x] T004 [P] 新建 `backend/src/flowpay/workflows/multi_agent/clarification.py`：`ClarificationTopic` 枚举、`ClarificationState`（asked_topics / answers / unavailable_topics；值 ≤500 字符；更新与恢复规则按 data-model.md）、`ClarificationRequest`；导出到包内使用点
- [x] T005 `backend/src/flowpay/workflows/multi_agent/state.py`：`SharedTurn.pending` 改为 `list[ClarificationRequest]`，增加 `clarification: ClarificationState`；同步修改受影响的类型引用与既有测试
- [x] T006 `backend/src/flowpay/workflows/multi_agent/graph.py`：轮初恢复 `ClarificationState`，把 `clarification_note(...)` 与 case_note、记忆段拼接注入简报；轮末写回检查点 `"clarification"`；补充简报内容测试

## Phase 3：US1（P0）先查后问，一次问全

- [x] T007 [P] [US1] `clarification.py`：实现 `clarify_tool(handler)`（工具名 `ask_user`，参数 `missing` + `topics` + `question`，至少一项非空）；handler 拒绝已问/已回答/不可提供主题与已给出条件，合并同轮多 worker 请求并记 `asked_topics`
- [x] T008 [US1] `backend/src/flowpay/workflows/multi_agent/investigation.py`：`sql_worker_node` 改用 `clarify_tool`；更新 `SQL_WORKER_PROMPT`（先执行可用查询、一次问全、不重复问）；补充工具去重与合并测试
- [x] T009 [US1] `investigation.py`：`SupervisorPlan` / `SET_TASKS` 增加 `clarification`（`answers` / `unavailable`，默认空，`extra="forbid"`）；解析后先更新状态再拆任务；非法主题与超长值按空/截断处理。**tasks 段必须内联自包含，禁止悬空 `$ref`（当前 `TASKS_SCHEMA` 引用了未随附的 `#/$defs/SupervisorTask`）**；`clarification` 超 4 条或含非字符串项时不得抛异常，去重/截断或按空更新处理；补 schema 自包含与容忍解析用例
- [x] T010 [US1] `investigation.py` 的 `SUPERVISOR_PROMPT`：从用户最新消息抽取主题回答与否决声明；改口取最新值；补充假模型用例验证状态写入与任务拆解共用一次调用
- [x] T011 [US1] 集成测试：缺账户 + 文档可查时，先产生文档报告、只产生一条合并追问，状态为 waiting；证据足够时不发追问
- [x] T012 [US1] 验收 US1：跑 `backend/tests/workflows/test_clarification.py`，覆盖先查后问、一次问全、条件/主题去重；记录结果到交付报告

## Phase 4：US2（P0）等待轮带结论

- [x] T013 [US2] `investigation.py`：新增 `set_response` 结构化工具（`mode: answer|clarify`、`answer`、`question`、`topics`、`conditions`）与解析函数；解析失败回退（pending → clarify，否则正文作答）
- [x] T014 [US2] `investigation.py` 的 `SYNTHESIZE_PROMPT` 与 `synthesize_node`：clarify 正文由系统确定性拼接“报告结论摘要 + 缺项清单 + 模型 question”，无报告时明确写“尚未查到结论”；模型 question 不得单独充当整段正文；决策声明的 topics/conditions 先过滤已回答、不可提供、已问过与 Case.known，正文/`asked_topics`/事件只记过滤后的仍缺项。两种模式正文都过现有编造编号核对；`mark_asked` 必须在重试结束、最终决策采纳后执行一次，避免被丢弃的决策污染状态。每次决策追加一条 `clarification.decided` 事件（mode、source、过滤后的 topics/conditions、has_reports）。补用例：模型 clarify 只给问题不含摘要；模型 clarify 重复索要已回答主题/已提供条件时正文不出现它们
- [x] T015 [US2] 集成测试：等待轮回答包含文档结论与引用、只含一条合并追问；无报告时明确写“尚未查到”；强制模型返回非法结构时走回退
- [x] T016 [US2] 验收 US2：按 quickstart 手工走“缺条件 → 带结论追问 → 补齐 → 继续查”，记录对话与事件

## Phase 5：US3（P1）澄清状态跨轮不重复

- [x] T017 [US3] `clarification.py` 恢复逻辑：未知主题丢弃、超长值截断、answers 与 unavailable 冲突时 answers 优先；补充单元测试
- [x] T018 [US3] 多轮状态测试：接受/改口/否决主题在后续轮次简报中可见；已问主题不再被 `clarify_tool` 接受
- [x] T019 [US3] 验收 US3：集成测试覆盖“用户说查不到 → 下轮不再问 → 作答标注未确认”；跨轮检查点恢复后状态不丢

## Phase 6：US4（P1）登录态账户不再索要

- [x] T020 [P] [US4] `backend/src/flowpay/core/data_sources/tools.py`：`sql_tool` 从 required 与 properties 移除 `scope_overrides` 已绑定字段；补充商户/内部两种 schema 单测
- [x] T021 [P] [US4] `tools.py`：`log_tool` 必填改为第一个未被覆盖的过滤字段；全部被覆盖时 required 为空并保留运行期 notice；补充单测
- [x] T022 [US4] 工具描述在有绑定时追加“账户由系统绑定，无需提供”；`SQL_WORKER_PROMPT` 改为“必填项以工具 schema 为准，已绑定账户不会出现在参数里”；`Clarifier` 接收 `runtime.scope_overrides`，把已绑定条件当作已知项拒绝索要并回填“已由系统绑定”；集成测试确认商户会话不索要 account_id（schema、追问、模型请求都不得出现）、内部会话行为不变

## Phase 7：US5（P1/P2）文档召回诊断与改进

- [x] T023 [P] [US5] 新建 `scripts/benchmark/retrieval.py`：CLI 参数（`--per-type`、`--limit`、`--config`、`--output`）；读取 96 题样本与 `required_documents` / `planned_evidence_ids`
- [x] T024 [US5] `retrieval.py`：查询口径（含实际修复记录）（initial / observed / full facts）、纯向量与混合两条路径、top-k recall（k=1/3/5/10/20，按 `source.doc_id` 命中）。`observed` 从 `--observed-arm` 指定臂的事件库读取每题首条之后的 `message.user` 回复，拼到首条消息；无该臂时 observed=initial。**`collect` 必须按 `case.text(dialect)` 取文本（当前实现把 observed 误用了 full）**；补测试断言 observed 检索收到的是 observed 文本而非 full。用假 store 单测 recall 计算与口径构造。**修复完成（T028 重开轮）：collect 改 case.text(dialect)，双用例守住；后续按 FR-8 修订又加 actual 第四口径与 --answers-arm**
- [x] T025 [US5] 运行诊断并保存 `var/benchmark/20261117-retrieval-diagnosis.md`：`--observed-arm var/benchmark/20261117-071251-67e7d4`，记录分题型 recall@k、`observed-initial` 与 `full-initial` 差值、不可召回文档清单；确认全程无聊天模型调用。知识集合或嵌入服务不可用时停手报告，不伪造数据
- [x] T026 [US5] 写诊断决策记录：以 `observed-initial` 的 recall@10 差值判定查询缺信息；`full-initial` 只作上界。若 observed ≥10pp 选查询改写；若 observed <10pp 而 full ≥10pp，停下讨论是补澄清覆盖还是换改造；若两者都低选召回侧的一项。任何候选都无法达到 observed recall@10 +10pp 时停下与 Ban 讨论，不硬凑
- [x] T027 [US5] 实施所选改造（改动点按 T026 记录，可能涉及 `workflows/multi_agent/investigation.py` 的查询构造或 `core/knowledge` 的检索参数）；只改选定的一项
- [x] T028 [US5] 修复 `collect` 的 observed/full 混用后重跑 `retrieval.py`：observed-initial recall@10 = +2.6pp，未达标；记录为旧 C 臂原始回复的上界诊断，不宣称通过。T027 暂保留，最终由 T037 的新臂 actual 口径判定

## Phase 8：US6（P2）评测口径修复

- [x] T029 [P] [US6] `scripts/benchmark/conversation.py`：`asked_fields` 同时读取 `ask_user` 事件的 `missing`/`topics` 与 `clarification.decided` 事件的 `topics`/`conditions`；`FIELD_CATEGORY` 增加 goal / symptom_details / occurrence_time / api_version 映射
- [x] T030 [US6] `scripts/benchmark/run.py`：单轮题在 `status=waiting_user` 时也要记录 signal（不能因 `if not multi: break` 跳过）；多轮最终轮先追加 `Exchange` 再记 `max_clarifications_exceeded`；补充单测，确保 `signals` 含两类等待轮
- [x] T031 [US6] `scripts/benchmark/attribution.py`：按新信号重算，并用合成行单测覆盖“单轮等待/多轮最终等待”两类归因。旧臂行无法回填新信号，改用 `scripts/benchmark/clarification.py` 从旧事件库核对两臂 waiting 轮均 101（多轮 A 81 / C 79，单轮 A 20 / C 22），多轮最终等待 A 7 / C 8；新臂在 T037 用新报表核对。注意 `waiting_breakdown` 以 `max_clarifications_exceeded` 推断最终等待，旧臂存在 1 例最终等待无该偏离、2 例有该偏离但最终作答，归因拆分只作诊断，SC-6 以事件库等待轮计数为准
- [x] T032 [US6] 新建 `scripts/benchmark/clarification.py`：读臂目录的事件库，覆盖率取三处并集——`ask_user` 的 `missing`/`topics`、`clarification.decided` 的 `topics`/`conditions`、最终检查点 `clarification.answers`；与题库 `required_clarification_categories` 比较。**覆盖率只统计“确实发过问的多轮题”；单轮题不得进入覆盖分子/分母（它们没有 required 类别，当前实现把 20/22 道单轮等待题算成全覆盖，虚高）**；单轮等待仍计入等待轮统计。输出多轮发问题数、整题/类别级覆盖率、重复问、等待轮是否带结论；单测 + 在两臂旧数据上跑通

## Phase 9：Polish & 端到端验收

- [x] T033 质量门禁：`make quality`；容器 pytest 失败集合与基线逐条一致；ruff/basedpyright 全绿
- [x] T034 基线复核：同模型、同 96 题、同 C 臂配置跑一次，与 `20261117-071251-67e7d4` 对比；总分差 >±3 题则停手讨论。**2026-11-19 完成：+24 题（47 vs 23），远超 ±3；原因是本轮即新版端到端（007 机制已生效），非模型口径漂移。已与 T035 合并为同一轮运行（T034+T035 共用 20261118-134203-c49ac0）**
- [x] T035 新版端到端：同配置跑 96 题，产出目录记录到交付报告。**2026-11-19 首轮（20261118-134203-c49ac0）：47/96、证据 53/96、多轮 26/64、单轮 21/32（vs C 臂 +24/+21/+12/+12）；因机制门两项未达标，修复后重跑一轮作为正式产物**
- [x] T036 机制门：`clarification.py` 新臂 vs C 臂核对覆盖 ≥90%、重复问 0、等待轮带结论；未达标先调提示词再跑，仍不达标停下讨论。**2026-11-19 首轮实测（20261118-134203-c49ac0）：覆盖 57/64=89.1%（差 1 题临界）、重复问 37 处（synthesize 决策声明未并入去重集合，全部来自 decided 通道）、等待轮带结论 87/87=100%。拍板：修重复问去重 + 收覆盖缺口提示词后完整重跑一轮 96 题，重跑结果作为 T036/T037 正式验收数据**
- [x] T037 端到端门槛：总通过 ≥23、多轮 ≥17、证据 ≥32、单轮 ≥9；记录四项对比与噪声说明。另用新臂最终检查点的 `clarification.answers` 构造 actual 查询（initial + 实际收集到的主题值；建议 retrieval.py 增加 `--answers-arm` 读取 run.finished 的 state），核对 recall@10 是否 +10pp。这是 SC-5 的最终产品口径，observed 只作上界；actual <10pp 时停下与 Ban 讨论：先收紧澄清覆盖并复测，再考虑调整改写或撤换 T027
- [x] T038 交付报告：`specs/007-clarification-retrieval/quickstart.md` 补实际命令与结果；交付报告写 US 验收、改动文件、检索诊断结论、遗留风险；不 commit / 不 push

## 依赖与并行

- Phase 2 的 T004 → T005 → T006 顺序执行；T004 可与 T029/T023 并行（不同文件）。
- US1（T007–T012）依赖 T004–T006；US2（T013–T016）依赖 US1 的 pending 结构。
- US3（T017–T019）依赖 US1/US2；US4（T020–T022）独立，可与 US1 并行。
- US5 T023–T025 可与 US1 并行；T026 依赖 T025；T027–T028 依赖 T026。
- US6 T029–T032 与产品代码独立，但必须在 T034–T037 之前完成，否则机制门无法验收。
- MVP 范围：T001–T019（US1+US2+US3），可独立交付“先查后问、带结论追问、不重复问”。
