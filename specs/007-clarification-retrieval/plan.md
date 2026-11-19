# 实施计划：007 多轮澄清覆盖与文档召回

需求唯一来源见 spec.md；本文件记录落地路径、分层接线、批次与验收口径。

## Technical Context

- 语言/运行时：Python 3.12，沿用后端既有依赖；**不新增第三方包**。
- 影响层：
  - `workflows/multi_agent/`：新增澄清模块，改 state、investigation、graph；阶段独占逻辑不落到 core。
  - `core/data_sources/tools.py`：工具 schema 对被服务端绑定的隔离列不再标必填（US4）。
  - `core/agent/turn.py` 与 `core/execution/`：不改协议；等待状态与检查点沿用现有通道。
  - `scripts/benchmark/`：报表/归因补计最终轮、主题映射、覆盖率指标；新增离线检索评测。
  - `backend/tests/`：新增澄清与 schema 专项用例；更新受 pending 类型变化影响的既有用例。
- 数据存储：无新表、无迁移；澄清状态写入既有 `saved_state` JSON 检查点。
- 模型调用：澄清状态更新复用 plan 调用；证据判定复用 synthesize 调用；**不新增每轮模型请求**。
- 评测协议：固定 96 题、固定模型 `deepseek-v4-1-flash-economy-261017`、固定 C 臂配置；基线复用
  `var/benchmark/20261117-071251-67e7d4`（23/96、32/96、多轮 14/64、单轮 9/32）。

## 章程检查

`.specify/memory/constitution.md` 不存在；按 `CLAUDE.md` / `AGENTS.md` 的项目约定自查：

- 阶段编排、提示词与澄清状态留在 `workflows/multi_agent/`，不把业务循环藏进 core。
- workflows 不创建 SDK/数据库客户端；离线检索脚本放 `scripts/`，直接构造既有客户端。
- core 不依赖 workflows；`core/data_sources/tools.py` 只做 schema 与绑定值语义。
- 不新增第三方依赖；不改数据库 schema；不硬编码凭据。
- 公共方法声明输入/返回类型；已知结构不退回 `dict[str, Any]`；`__init__.py` 仅导出。
- 修改超过 3 个文件，按批次拆开；每批完成后跑对应门禁。

无违反项，无需要豁免的例外。

## 批次

| 批次 | 内容（US） | 门禁 |
|---|---|---|
| P0-1 | US1 先查后问 + US2 等待轮带结论 + US3 澄清状态（同一套接线） | 专项单测全过；手工多轮对话可见“结论 + 追问”；既有测试基线一致 |
| P0-2 | US6 评测口径修复（所有 waiting 轮信号、主题映射、覆盖/重复指标） | 用既有两臂事件重算：A/C 等待轮各 101（多轮 A 81/C 79，单轮 A 20/C 22）；归因不再漏 |
| P1-1 | US4 登录态绑定账户不再索要 | 商户工具 schema 不含 account_id 必填；内部会话用例不变 |
| P1-2 | US5 离线检索诊断 | 96 题诊断报告生成：两种查询口径、recall@k、纯向量 vs 混合；无聊天模型调用 |
| P2-1 | US5 召回改造（按诊断选一项） | recall@10 相对基线 +10pp；单轮检索不降 |
| P2-2 | 端到端验收 | 同模型同 96 题：总≥23、多轮≥17、证据≥32、单轮≥9；机制门全达标 |
| 收尾 | 质量门禁与交付 | 容器 pytest 基线一致；ruff/basedpyright/前端全绿；报告落 specs/007 |

## 分层接线

### workflows/multi_agent/clarification.py（新增）

- `ClarificationTopic = Literal["goal", "symptom_details", "occurrence_time", "api_version"]`；
  与题库类别一一对应（symptom_details 对应 benchmark 的 symptom_details）。
- `ClarificationState`：`asked_topics`、`answers: dict[Topic, str]`、`unavailable_topics`；
  `missing_topics()` 返回尚未回答、未标记不可提供、也未问过的目标主题。
  值长度单条 ≤500 字符；状态随检查点持久化（无上限条目数，主题枚举封闭）。
- `ClarificationRequest`：`conditions`（现有 ConditionField）+ `topics` + 问题文本。
- `clarify_tool(handler)`：模型可见工具仍叫 `ask_user`，参数在现有 `missing` 之外增加
  `topics`；二者至少有一个非空。处理函数负责：
  1. 条件字段已由用户给出或本轮已问过 → 回填说明、拒绝重复索要；
  2. 主题已回答/不可提供/已问过 → 回填说明、拒绝重复索要；
  3. 否则把请求并入 `shared.pending`（同轮多个 worker 合并，不产生第二条追问）；
  4. 把本次问到的主题写入 `asked_topics`。
- 简报：`clarification_note(state)` 输出“已问/已回答（主题=值）/不可提供”，由 graph 与
  case_note、记忆段拼接后注入 plan/worker/synthesize。

### workflows/multi_agent/state.py

- `SharedTurn.pending` 由 `list[AskUserResult]` 改为 `list[ClarificationRequest]`；
  `SharedTurn` 增加 `clarification: ClarificationState`，随轮初恢复、轮末写回检查点。
- 检查点新增 `"clarification"` 键；`restore_*` 对未知主题与超长值直接丢弃/截断。

### workflows/multi_agent/investigation.py

- `sql_worker_node` 的 `ask_user` 处理改为上述 `clarify_tool`；worker 提示词增加：
  “先执行当前条件能执行的查询；只有缺必需条件或证据不足时才追问；一次把所有缺失
  条件与主题问全；已知/已问/拿不到的不要重复问。”
- `plan_node`：
  - `SET_TASKS` schema 增加 `clarification`：`answers: [{topic, value}]`、
    `unavailable: [topic]`，默认空；`extra="forbid"` 仍生效。模型可见的整个
    parameters 必须自包含：tasks 段内联 `SupervisorTask` properties，不留下
    没有 `$defs` 的 `#/$defs/...` 引用（供应商对 $ref 支持不一致）。
  - 解析后先写回 `shared.clarification`（answered 覆盖旧值、unavailable 去重），再拆任务；
    该更新与本次任务拆解共用同一次模型调用（零新增请求）。
  - plan 提示词增加：从用户最新消息抽取主题回答与“拿不到”声明；已有值改口取最新。
- `synthesize_node`：
  - 现有直连模型调用增加结构化决策工具 `set_response`：
    `mode: "answer" | "clarify"`；`answer` 与 `question` 至少一个非空；clarify 时附
    `topics`/`conditions` 仍缺项。解析失败或模型未调用时回退：有 pending → clarify，
    否则用模型正文作答。
  - clarify：用户可见正文由系统确定性拼接——报告结论摘要 + 缺项清单（pending 与
    决策声明的并集）+ 模型 question；无报告时摘要段明确写“尚未查到结论”。
    模型 question 不再单独作为整段正文，避免漏写摘要违反 FR-4；`waiting=True`。
  - answer：沿用现有作答与编造引用核对（SC-4）；clarify 正文同样过一次编号核对。
  - 无 pending 时也允许模型选择 clarify（用于“查了但证据不足”的主题补问）。
  - 决策声明的 topics/conditions 先用与 worker 相同的过滤口径剔除已回答、不可提供、
    已问过与 Case.known；正文缺项、asked_topics 与事件只记录过滤后的仍缺集合。
  - 每次决策发 `clarification.decided` 事件（mode、source=model/fallback、
    topics/conditions、has_reports），供 96 题统计模型决策使用率与覆盖率。事件在
    编造编号重试结束后只为最终采纳的决策发一条，避免同题重复计数。
- `graph.py`：轮初恢复 `ClarificationState` 并拼入简报；检查点写回；
  `fold_finding_record_ids` 等现有逻辑不变。

### core/data_sources/tools.py

- `sql_tool`：`required = [n for n in spec.scope if n not in overrides]`；被绑定列从
  properties 与 required 中一并移除（模型看不到也不必填写），`scope_of` 继续用服务端值。
- `log_tool`：必填过滤项改为“第一个未被绑定覆盖的过滤字段”；无剩余时 required 为空，
  运行函数仍按“至少一个过滤条件”返回 notice。
- 工具描述在存在绑定时追加“该账户由系统绑定，无需提供”，避免模型仍按旧文案索要。
- `SQL_WORKER_PROMPT` 不复述“账户必填”，改为“必填项以工具 schema 为准”；已绑定账户
  不出现在参数列表，也不进入追问。
- `Clarifier` 接收 `runtime.scope_overrides`：已绑定条件视为已知，拒绝索要并回填
  “已由系统绑定”，防止模型绕过 schema 追问账户。
- 内部账号 `scope_overrides={}`，schema 与行为保持现状。

### scripts/benchmark

- `conversation.py`：`asked_fields` 同时读取 `missing` 与 `topics`；`FIELD_CATEGORY` 增加
  主题到 benchmark 类别的映射（goal / symptom_details / occurrence_time / api_version）。
- `run.py`：
  - 单轮题在 `status=waiting_user` 时也记录 signal，不因 `if not multi: break` 漏记；
  - 多轮最终轮（`turn == limit`）先追加一次 `Exchange`（`disclosed=[]`）再记
    `max_clarifications_exceeded`，使 `signals` 涵盖所有 waiting_user 轮次。
  - 报表的“澄清信号”行按新口径输出；主题类别进入 repeated/unexpected 判定。
- `attribution.py`：信号与失败主因按新口径重算；输出中区分“最终轮等待”和“中间轮等待”。
- 新增 `scripts/benchmark/clarification.py`：
  - 读一个或多个臂目录 + 题库 `required_clarification_categories`；
  - 输出覆盖率（整题合并的主题/条件映射 vs 必需类别，仅统计发起追问的题）、
    重复问次数、等待轮数、等待题终态；供 SC-1/SC-3 验收。
- 新增 `scripts/benchmark/retrieval.py`：
  - 读 96 题样本与 `required_documents` / `planned_evidence_ids`；
  - 三种查询：`initial`（首条消息）、`observed`（首条消息 + 今天 C 臂事件里用户实际回复）、
    `full`（首条消息 + 全部 available_user_facts，仅作上界）；
  - 每个查询分别用纯向量与混合检索取 top-k（k=1/3/5/10/20），按题目计算必需文档
    recall@k（片段 `source.doc_id` 命中即算召回）；
  - 输出 Markdown：分题型、两种口径、两种检索路径、k 曲线与瓶颈判断；
  - 知识客户端直接构造 `PostgresVectorStore` + `EmbeddingClient` + 空事件 sink，
    **不调用聊天模型**。
- 诊断决策规则（写进报告与交付记录）：
  - `observed - initial` 的 recall@10 提升 ≥10pp → 主因是查询缺信息，改造为“把澄清得到的
    主题值拼入检索查询”（属查询改写）；`observed` 用今天 C 臂事件里用户实际回复拼接，
    代表产品能拿到的信息。
  - `full` 只作上界：若 `full - initial` ≥10pp 但 `observed - initial` <10pp，说明查询侧
    有空间但澄清覆盖不足以拿到这些事实——停下讨论是先补覆盖还是换改造，不直接验收。
  - `observed` 与 `full` 都低且差异 <10pp → 主因是召回，改造在混合检索参数、分块或关键词路
    中选一项。
  - 必需文档不在集合 → 单列为不可召回，不计入查询改造效果。

## 验收路径

1. 专项单测（容器内）：澄清状态序列化、工具去重/合并、plan 抽取、synthesize 决策与回退、
   绑定账户 schema、报表最终轮信号、覆盖率计算、检索 recall 计算（fake store）。
2. 手工对话（内部账号）：缺账户/编号 → 先查文档 → 带结论追问；补充后继续查；再说拿不到
   → 不再问并作答标注。商户账号：不索要账户。
3. 离线检索：`retrieval.py` 出诊断报告（initial / observed / full），选改造后先用
   observed 上界复测；最终用新臂 actual 口径（initial + 检查点 `clarification.answers`）
   确认 recall@10 +10pp。
4. 端到端：同模型、同 96 题、同 C 臂配置跑新版；用 `clarification.py` 与 `attribution.py`
   核对机制门，再比 SC-4 四项。

## Clarifications 落地

1. 双轨范围照 spec；US5 先诊断后改。
2. worker 发问，synthesize 合并并附结论。
3. conditions 沿用现有枚举；topics 四个背景主题。
4. 独立 ClarificationState，Case 不扩展。
5. 先查后问；证据足够直接作答。
6. 等待轮必须带结论摘要。
7. 用户说拿不到 → unavailable，不再问。
8. 登录态绑定列不再必填；内部不变。
9. 检索诊断两种查询口径，无参考检索词；recall@10 +10pp。
10. 模型与 96 题基线固定。
11. 机制硬门 + 方向性提升，不要求统计显著。
12. 覆盖只统计确实发问的题。
13. 报表/归因补计最终轮信号。
14. spec-kit 交付，Pi 实施，Claude 审核与验收复核。

## 风险与备注

- **模型一致性**：复用今日 C 臂的前提是该模型与 `deepseek-v4.1-flash-1017` 行为一致；
  首次新版跑前先跑一次基线复核（同 96 题），若总分偏离超过 ±3 题则基线作废并停手讨论。
- **结构化输出兼容性**：`set_tasks` / `set_response` 依赖供应商工具调用；解析失败必须有
  确定性回退，不能让整轮失败。
- **过度追问**：模型可能对可直接回答的题也补问主题；用 SC-4 总通过不降与 over_clarify
  专项计数兜底，必要时收紧 plan/synthesize 提示词。
- **状态增长**：主题值单条封顶 500 字符、主题枚举封闭；不新增 DB 迁移。
- **manual-loop**：不共享新的 ClarificationState 与提示词；旧 `ask_user` 工具保持原 schema，
  避免旧阶段回归。
- **Windows 开发环境**：本机原生 pytest 因 `fcntl` 不可用，门禁在容器内跑；脚本单测用 venv。
- **检索改造不确定性**：observed（旧 C 臂原始回复）+2.6pp 只说明旧对话的原始文本不足以
  达到阈值，不等于 T027 失败：产品实际用的是新系统抽取后的 `clarification.answers`。
  T027 暂保留，最终由 T037 的新臂 actual 口径判定；actual <10pp 时停在 P2-1 与 Ban 讨论
  补覆盖、调整改写或撤换，不硬凑指标。

## 基线附录（T001 核对，2026-11-17）

- C 臂 `var/benchmark/20261117-071251-67e7d4`：总通过 23/96、证据全中 32/96、多轮 14/64、单轮 9/32。
- 事件口径等待轮（run.finished status=waiting_user）：

| 臂 | 总等待轮 | 多轮 | 单轮 | 多轮最终轮 |
|---|---:|---:|---:|---:|
| A | 101 | 81 | 20 | 7 |
| C | 101 | 79 | 22 | 8 |

- 报表当前只记录 exchanges 中的信号：A waiting_user 74 + text 3，C waiting_user 72 + text 3。
  差额来自两处：多轮最终轮（A 7 / C 8）与单轮等待轮（A 20 / C 22）都未进入 `signals`。
- US6 修复后，两臂 waiting_user 信号应分别为 A 101 / C 101（text 信号另计）。
- T001 的基线数据由 Claude 回填，Pi 不改 `specs/` 工件。
