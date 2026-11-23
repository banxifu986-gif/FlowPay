# 010 Code Review：逐断言校验（发布前校验器）——合并后、评测前只读审查

- 审查对象：`git diff 00117f4..HEAD`（约 20 提交：树A 能力侧 7 + 树B 消费侧 7 + T018 真联调 2 + spec 套件/门禁收口）
- 审查方式：只读。全量阅读 spec 套件（spec/plan/research/data-model/contracts/tasks/quickstart/checklists）、树A `core/verify/` 全部实现、树B 接线（assertion_gate.py + investigation.py 接线段）、配置面（settings/loader/local.toml）、回放脚本 `scripts/benchmark/assertion_audit.py`（1308 行）与全部新增测试（7 个测试文件 ≈1900 行）；宿主形态跑测试与静态检查；未跑任何 API 评测。
- 对照基线：spec.md FR-1..FR-10 / SC-1..SC-6、research D1..D10、contracts/verdict-schema.json、CLAUDE.md 纪律。

## 一、质量门基线（宿主形态实测）

| 项 | 结果 | 判定 |
| --- | --- | --- |
| pytest（`tests -q --continue-on-collection-errors`） | 555 passed / 6 failed / 23 skipped / 12 collection errors | 与已知基线一致：6 失败=fcntl×3 + os.symlink WinError 1314×3（容器-only）；12 收集错误同为 fcntl。010 改动面零交集 ✓ |
| ruff | All checks passed | ✓ |
| basedpyright | 0 错 0 警 | ✓ |

010 新增测试全部通过（verify 系 + assertion_gate 系 + assertion_audit 系）；未滚动任何失败基线。

## 二、审查清单逐条结论

### 1. FR-1..FR-10 逐条对照

| FR | 结论 | 依据（实现锚点） |
| --- | --- | --- |
| FR-1 装配 | ✅ 通过 | `assembly.py`：绑定引用原文（不受预算影响）+ 全轮片段 score 降序字符封顶 + 记录摘录（缺失记 `missing_records` 不阻塞）+ 用户问题；`known_ids` 含裸/全双形态；零新增检索调用。单测逐项覆盖（`test_verify_assembly.py`） |
| FR-2 抽取 | ✅ 通过 | `extractor.py`：一次调用、JSON 解析（容忍栅栏）、空清单、逐字去重（防同一句双重替换）、refs/record_ids 规范化；草稿 prompt 未动 |
| FR-3 ID 存在性 | ✅ 通过 | `verifier.screen_assertions`：ref-missing / no-binding / excerpt-missing 三类缺口强制 `insufficient`，不经语义校验；测试断言被强制断言不进待判定清单、零语义调用 |
| FR-4 批量语义校验 | ✅ 通过 | `verifier.py`：一次调用四档；partial 缺 overreach（dimension ∈ 对象/时间/数字/条件）或缺 revision 视同 insufficient；contradicted 缺 conflict 保留档位详情为空；对不上回传的条目兜底 insufficient；三类越界样例 fixture 覆盖（SC-3） |
| FR-5 处置 | ✅ 通过（一处行为偏差见 P2-4） | `actions.py`：keep / replace（子集校验：数字 ∪ 编号 ∪ 引用三维，越界降级）/ downgrade（幂等：判定层 `DOWNGRADE_MARK in sentence` 跳过 + 机械层锚点护栏）；矛盾只降级、conflict 不进回答 |
| FR-6 fail-open | ✅ 通过 | `errors.py audit_response`：调用失败/解析失败/装配异常/ID 核对异常全部放行原文 + `fail_open=True`、不抛出；CancelledError 穿透（002 先例）；费用已发生调用照常入账；测试六类注入全覆盖 |
| FR-7 接线与开关 | ⚠️ 部分（P1-2/P1-3） | 开关字段、getattr 兜底、筛选、事件、预算耗尽跳过均有；但**生产装配链断**：`graph.py:176` 构造 `synthesize_node` 不传 gate，`gate_from_settings` 在生产代码零调用、AssertionAuditor 无任何实现——开关开启后在线路径仍零校验零事件。另 DISPUTE 重试绕过 gate（P1-3） |
| FR-8 旁路回放 | ✅ 通过（同构性缺口见 P1-3/P2-6） | `assertion_audit.py`：恢复原始草稿（最后一次成功决策调用=重试终态）+ 重放 `check_citations`（不直接校验发布文本）+ 不重跑 agent；编造核对不重试、以 `invented:*` deviation 暴露（对 D6 文字的合理保守化，注释自证） |
| FR-9 断点归类 | ✅ 通过 | 四类规则与 spec US2 逐条一致（partial→overreach；insufficient∧∉集→not_recalled；∧∈集∧未绑定→mis_bound；contradicted 独立档）；无对照不猜（expected=None 退化）；四类分布+逐条样例+矛盾占比；误伤基线以必需断言覆盖对齐（评测同口径 text_supported） |
| FR-10 冒烟 | ❌ 阻断（P1-1） | `--questions` 首跑必崩（见第四节定性复核）；SC-4 未出数 |

SC 检查：SC-1 ✅（专项测试可证伪：关闭态零新增调用零事件、作答与 009 一致）；SC-2 ✅（宿主形态全绿，容器全量留终门禁）；SC-3 ✅；SC-4 ❌（P1-1）；SC-5/SC-6 待批准（合规）。

### 2. 契约三方一致性

- `core/verify/contracts.py` ↔ `contracts/verdict-schema.json`：四档枚举、维度枚举、actions 三枚举、verdicts 条目 required（text/refs/record_ids/verdict）、actions 条目 required（sentence/action）完全对齐 ✓；overreach/revision/conflict 以"字段缺失"代替 `null` 序列化，语义兼容 ✓。
- `assertion_gate.AssertionAuditor`（在线）↔ 回放 `ReplayVerifier`：**两个调用面分裂**——在线面 `(text, question, passages) -> AssertionAudit`，回放面 `AuditInput -> tuple[AssertionVerdict, ...]`。CoreVerifier 只实现回放面；在线面无任何实现与装配（归入 P1-2）。
- `AssertionAudit.conflict_details` 与 `AssertionAction.breakpoint_hint` 为**无消费字段**：conflict 详情实际经 verdicts 条目内 `conflict` 落事件（D10 口径 ✓），`conflict_details` 无载荷出口；`breakpoint_hint` 由 `map_actions` 产出但两份 payload（gate.action_payload、回放 verdict_payload）都不序列化，回放归类走 verdicts 独立计算——契约定义了两个死字段（P2-3）。
- 事件载荷 `text` 字段语义漂移（P2-1）：契约定义"处置后的最终文本"，在线 gate 正常判定路径实发 auditor 返回的 `audit.text`（gate 输入是候选句拼接文本 → 事件 text=拼接文本的处置结果），不是发布文本。

### 3. 树B 桩残留与切换完整性

- FixtureVerifier 仅作为回放默认桩（`--verifier` 未注入时），判定明确标注"不是语义结论"（词面/ID 两级），不冒充真校验器 ✓。
- `--verifier module:attr` 注入 CoreVerifier 切换完整；CoreVerifier 复用运营模型配置（responses/chat 协议分流）、`_NullSink` 保证回放零落事件、`usage_entries` 不传保证回放零计入用量 ✓。
- `gate=None` 默认零变化 ✓（graph 不传 gate，`gate_response` 直接返回原文——这一侧反而被装配断层"保护"成零变化）。
- getattr 兜底在树A Settings 合并后正确：`gate_from_settings` 的字段名与 T013 实际字段一致，对偶测试（SettingsStub / DisabledStub / object()）+ loader 装载测试锁定 ✓；ENV 豁免通道测试锁定 FLOWPAY_ASSERTION_AUDIT_* 不生效 ✓。

### 4. 测试质量

- 可证伪形态达标：关闭态断言零事件+零调用+作答一致；开启态断言事件载荷（verdicts/actions/fail_open/conflict）+ 发布文本变化 + auditor 收到的输入；故障态断言原文放行+fail_open+无 run.failed+无 turn.progress；预算耗尽断言 auditor 未被调用（防既有兜底空跑通过）✓。
- fail-open 覆盖：模型故障/抽取坏输出/校验坏输出/装配异常/ID 核对注入异常/取消穿透/费用入账，六类齐 ✓。
- 幂等覆盖双层次（判定层 keep + 机械层锚点护栏）；子集校验覆盖数字/记录编号/引用三维；多处出现首处命中（core 版）✓。
- 断言外部可见行为为主：事件序列、JSONL 形态、发布文本；对 prompt 内容的断言仅限装配口径（证据块进 prompt、被强制断言不进待判定清单），属校验器行为的可观测边界，不属锁内部结构 ✓。
- 缺口：①生产装配链（graph → bootstrap → gate_from_settings）无任何测试——P1-2 正是从这里漏掉的；②DISPUTE 重试路径无集成测试——P1-3 无回归锁；③gate 版 `apply_actions` 的"同句多处全部替换"行为无测试（与 core 版行为分歧无人发现）。

### 5. 事件兼容

- `assertion.audit` 经既有 `tracer.emit` JSON 事件通道承载 ✓；零断言/fail-open/预算耗尽/正常判定每作答恰好一次事件，与 data-model"开关开启的每次最终作答校验后"一致 ✓。
- 不改既有事件类型、检查点（saved_state 的 case/done/findings/retrieved）形状 ✓；`clarification.decided` 载荷不变 ✓。
- 用量口径：`audit_response(usage_entries=...)` 支持入账，用量单测锁定；但在线装配缺失导致生产入账链同样断（归 P1-2 清偿范围）。
- 观察项（不计缺陷）：`Settings.snapshot()` 新增两键 → `run.started` 载拼多多两键，属配置面变化的自然投影；评测报表按既有键读取不阻断。

### 6. 精简性（CLAUDE.md"最少代码"对照）

- 总体克制：无过度抽象、无未经要求的可配置化（回放参数走 CLI 不进配置面 ✓、两字段命名与 D9 一致）；断点归类/误伤基线/报告渲染各司其职。
- 冗余点见 P2-3（契约死字段）与 P2-5（五处同义词面复制——分层禁依赖的代价，同步注释已立，但 gate 与 core 各持一份 `apply_actions`/`downgrade_text`/`split_sentences` 已产生实际行为分歧，P2-2/P2-4 即其后果）。

## 三、分级意见

### P0（阻断评测/合并）

无。T019 冒烟属回放链路，机制完整可跑；阻断它的 P1-1 是脚本层小修。

### P1（应修——P1-1 阻断 T019 首跑；P1-2/P1-3 必须在 T022 前清偿）

**P1-1 `--questions` 显式题单崩溃：恢复层不带题单过滤，全库 300 题对 96 题臂逐题报缺**

- 位置：`scripts/benchmark/assertion_audit.py:507-509`（`replay_arm` 内 `selected_cases(per_type)`）与 `scripts/benchmark/assertion_audit.py:1279`（`--questions` 时 `per_type=0`）。
- 问题：`--questions` 只在 `main()` 的 required/expected 查询用了题单过滤（`selected_cases(per_type, args.questions)`），`replay_arm` 内部恢复层调 `selected_cases(per_type)` 不带 questions——`per_type=0` 展开为 `blueprints()` 全库 300 题，逐题对 `load_rows(arm)` 结果文件查询（`assertion_audit.py:516-518`），R96B2 臂仅含 R96B 96 题子集（SR 系列到 0061、每 4 取 1），命中第一个臂外题号即 `raise ValueError(f"{arm} 结果文件缺题 …")`。`--all` 走同一恢复路径，T021 全量回放会同样崩。
- 依据：tasks.md T019（12 题显式题单口径）、T021（`--all` = 96 题量级回放）；FR-8（回放消费事件与题库，臂是数据源事实）。
- 建议修法：见第四节定性复核——以臂结果文件题集为权威，题单过滤下推到恢复层；`--all` 同口径收窄为臂内全量。修法极小（恢复层遍历"题单/臂题集 ∩ 结果文件"，blueprints 只作 required/expected 查询），不要静默跳过臂外题。

**P1-2 在线装配断层：开关读了但无人装配，开启态在生产链路静默无效**

- 位置：`backend/src/flowpay/workflows/multi_agent/graph.py:176`（`synthesize_node(model, runtime, shared, case_note, context_policy)` 未传 `gate`）；`backend/src/flowpay/workflows/multi_agent/assertion_gate.py:80-92`（`gate_from_settings` 生产零调用）；`AssertionAuditor` Protocol 无任何实现（`backend/src` 全库 grep 仅定义与导出）。
- 问题：`local.toml` 置 `assertion_audit_enabled=true` 后，生产/评测臂链路（`make benchmark` 起服务评测）中 gate 恒为 None——`gate_response` 直接返回原文，零校验零事件。T022 开启态评测臂将静默测出"关闭态数据"，白烧 96 题 × 每作答 2 次调用的 API 费用；`usage_entries` 入账链同样不成立。`test_assertion_gate.py:3` 文件头自述"graph 装配链在树A 合并后接通"，但 T018 合并后未接——这是双树文件面纪律（graph.py 属既有文件、两树都不含）留下的合并缝，B3 集成（T018）也无人补。
- 依据：spec FR-7（接线点为统一作答出口的单点追加；配置面开关默认关）、US3 Given/When/Then（"开关开启，校验执行"）、tasks T014/T015/T022、SC-6。
- 建议修法：①bootstrap（或 graph 构造处）从 Settings 装配：`gate_from_settings(settings, auditor)`，auditor 为 AssertionAuditor 的 core 实现包一层（复用 T018 CoreVerifier 的协议分流装配，传 `shared.usage_entries` 入账）；②`graph.py` 构造 `synthesize_node` 时透传 gate；③补一条装配级回归测试（settings 开启 → 事件出现），消除"注入测试全绿但生产 wiring 断"的盲区。工作量小，不改契约。

**P1-3 DISPUTE 编造核对重试绕过 gate：重试作答零校验发布，且第一次 audit 事件与最终发布文本错位**

- 位置：`backend/src/flowpay/workflows/multi_agent/investigation.py:1307-1314`（gate 处置后 `invented` 非空 → `resolved = await decide()` 直接覆盖，其后不再过 `gate_response`）。
- 问题：统一出口（编造核对 + 引用校验）的主核对点在 decide 外层——第一次 `decide()` 的输出过 gate 后才做 `invented` 核对；一旦首次作答含 `shared.seen` 外编号（模型转述错编号在评测集真实出现过，009 编造核对机制即为此设），DISPUTE 重试的第二次 `decide()` 输出**不经 gate 校验直接发布**。开启态下该路径作答零校验零事件；第一次已发的 `assertion.audit`（verdicts/actions/text）与最终发布文本错位。同时破坏 spec 边界"回放与在线的同构性"：回放草稿=最后一次成功决策（DISPUTE 终态，`draft_of` 覆盖重试终态）——回放校验了这些题而在线没有，回放结论不可迁移。
- 依据：spec 边界"多轮会话：每轮作答类回复都校验"；FR-8/D6 同构性承诺；plan.md 接线点定义（"audited_response 之后、发布之前"——DISPUTE 分支的发布同样在接线点之后）。
- 建议修法：把 `gate_response` 单点调用移到 `invented` 核对与 DISPUTE 分支收敛之后、`mark_asked`/`clarification.decided` 之前（对最终采纳的 `resolved` 做一次校验）——仍满足"统一出口之后、发布之前"，且唯一调用点不变。注意 `budget_exhausted` 取值与处置后文本不再过外层核对：处置引入 seen 外编号的风险由子集校验（编号 ⊆ 原句 ∪ 绑定证据，摘录 ⊆ seen）兜住，可在 gate 后保留一次轻量核对或依赖既有安全侧论证，报告中说明。补一条 DISPUTE 触发的集成测试锁行为。

### P2（建议）

**P2-1 `assertion.audit` 事件 `text` 字段语义漂移**
- 位置：`backend/src/flowpay/workflows/multi_agent/assertion_gate.py:225-231`（`gate_response` 正常路径直接 `audit_payload(audit)`，text=auditor 对候选句拼接文本的处置结果）。
- 问题：契约（data-model）定义 `text`="处置后的最终文本；fail_open 或零断言时=输入原文"。在线档"输入原文"是 audited_response 完整文本，而事件 text 是候选句拼接文本的处置结果（缺非断言句、换行版式不同）；发布文本（gate 回填结果）与事件 text 不是同一物。
- 依据：data-model AssertionAudit.text、verdict-schema text 描述。
- 建议：正常判定路径在 `gate_response` 内以回填后的发布文本覆写 payload["text"]（或契约注明"校验器输入视角"并让报表按 actions 锚点对账）。一行级修改。

**P2-2 句级切分"声称同义但不同义"：citation_check 不按行 / actions 按行**
- 位置：`backend/src/flowpay/workflows/multi_agent/citation_check.py:86-91`（全文 findall，不按行）vs `backend/src/flowpay/core/verify/actions.py:38-44`（splitlines 后逐行切）。
- 问题：`actions.py:31-32` 注释声称"与 citation_check.split_sentences 同义——处置锚点与筛选层必须同一份切分口径"，实际不同义。无句尾标点的换行（列表项、表格行）在筛选层被连成跨行"句子"，core `_locatable` 按行切对不上 → 一律降级（安全侧但粒度失真：整段降级口径过大）；若 LLM 抄录与跨行切分不一致 → gate 回填锚点失配放行。触发面窄（绝大多数句子有句号），但漂移风险真实存在。
- 依据：research D5（句级等值匹配的锚点前提）；plan.md"筛选复用 split_sentences"。
- 建议：收口为一份实现（actions 版按行切更稳，citation_check 侧同步或在 core 提供单一实现供 gate 引用），并在测试锁一条跨行样例。

**P2-3 契约死字段：`breakpoint_hint` 全链路无消费、`conflict_details` 无载荷出口**
- 位置：`contracts/verdict-schema.json:74-77` + `contracts.py:84`（breakpoint_hint）；`contracts.py:101`（conflict_details）；`assertion_gate.action_payload` 与 `audit_payload` 均不序列化二者。
- 问题：breakpoint_hint 由 `map_actions` 产出但无任何消费方（回放归类走 verdicts 独立计算）；conflict_details 在 `audit_response` 填充、测试消费，但事件载荷走 verdicts 条目内 conflict（D10 口径）。两字段是契约先行阶段留下的超前设计。
- 建议：要么补上序列化出口，要么从 schema/contract 移除——按"最少代码"准则倾向移除（回放归类不依赖 hint 的事实已被 T010 证明）。

**P2-4 gate 版 `apply_actions` 同句多处全部替换，与 research D5"命中多处取第一处"不符**
- 位置：`assertion_gate.py:144-152`（replacements dict + 逐句重建，所有匹配句都替换）vs `actions.py:122-135`（find 首处替换，`test_verify_actions.py:178-190` 锁定首处语义）。
- 问题：同句在原文出现两处时，core 版只改第一处、gate 版全部替换——两份实现行为分歧；D5 文字承诺"第一处"。全部替换语义上其实更自洽（同句同判定同处置），但文字与实现须一致。
- 建议：二选一——改 D5/注释为"同句统一处置"，或 gate 版对齐首处；并补 gate 版该行为测试。

**P2-5 五处同义词面复制，已有分歧实例**
- 位置：`verdict_payload`（gate:165-185 / 回放:713-733 逐字重复）、`apply_actions`、`downgrade_text`（gate:110-115 / actions:47-53）、`split_sentences`（三处）、`PASSAGE_REF`（两处）。
- 问题：core 不得依赖 workflows/scripts 的分层约束导致同义词面多处复制，注释已立同步纪律，但 P2-2/P2-4 正是已兑现的漂移。
- 建议：回放脚本可直接 import core（`scripts` 依赖 `core` 不违分层），把可下沉的实现（verdict_payload、切分、downgrade 文本）收口到 core 单份；workflows 侧因反向禁依赖可保留同义词面但每处加对偶测试。非本轮阻断，随交付清偿。

**P2-6 多轮题回放证据池口径：会话最终池 vs 在线当轮池**
- 位置：`assertion_audit.py:300-317`（`passages_of` 取最后一次 `run.finished` 的 `state.evidence`）。
- 问题：多轮会话在线 gate 每轮拿 `shared.retrieved.all()`（当轮池），回放拿跨轮 prune 后的最终池——回放证据集偏大，判定可能偏松（not_recalled 误归为 mis_bound 的方向）。单轮题（R96B2 主体）无影响。
- 建议：报告口径注明"多轮题回放证据池为会话最终池"；若 T021 后多轮题归类出现系统性偏移，再按轮次重放（每轮 run.finished 快照）。

**P2-7 quickstart.md 冒烟命令误导：`--sample 12` 语义是每题型 12 道（≈72 题）而非 12 题**
- 位置：`specs/010-assertion-verification/quickstart.md:25-28`。
- 问题：`sample()` 是"每种题型取前 per_type 道"（run.sample 口径），`--sample 12` 实际恢复 72 题；T019 已改用 `--questions` 显式题单，文档未同步。
- 建议：quickstart 冒烟命令改为 `--questions <题单>` 形态或注明 sample 语义。

## 四、已知问题复核：T019 冒烟 `--questions` 首跑报"结果文件缺题 FP-BM-SR-0065"

**机制确认（成立）**：`main()` 中 required/expected 查询用了 `selected_cases(per_type, args.questions)`（题单过滤 ✓），但 `replay_arm` 恢复层调用 `selected_cases(per_type)` 不带 questions；`--questions` 分支置 `per_type=0` → `sample(rows, 0)` 返回 `blueprints()` 全库 300 题 → 逐题对 `load_rows(arm)` 结果文件查（`assertion_audit.py:516-518`），臂外题号（R96B2 仅 96 题：SR 到 0061 每 4 取 1）即抛"结果文件缺题"。FP-BM-SR-0065 正是 SR 系列 0061 之后的第一个全库序号越过点。`--all` 走同一恢复路径，T021 同样会崩——影响判断正确。

**定性修正方向（同意 Ban 建议，细化两点）**：
1. **以臂结果文件题集为权威**：恢复层遍历"题单 ∩ 臂结果题集"（`--all` 则为"臂结果题集 ∩ blueprints 可查集"），blueprints 仅提供 required_assertions / expected_evidence 证据映射查询。臂结果文件的题就是本次评测实际跑过的题——回放的数据源事实以它为准，spec"96 题旁路回放"的口径天然成立。
2. **保留显式报错，不静默跳过**：题单内的题若臂结果文件缺行（真正的数据缺陷）应继续 `raise ValueError`——现在的报错语义本身是对的，错的只是题集边界（题库 ≠ 臂）。蓝本缺行与臂缺题是两种不同的失败，都该显式。

**不建议实施**（遵审查纪律）：仅定性，未改任何代码。

## 五、结论：go / no-go

**NO-GO（现状直接进 T019 冒烟）→ 修正 P1-1 后 GO。**

- T019 冒烟（回放链路）：机制完整——草稿恢复/校验链重放/CoreVerifier 真联调/四类归类/误伤基线/JSONL 均就位，树A 质量门全绿；唯一阻断是 P1-1 的题单边界（脚本层小修）。修 P1-1 后即可进入 T019（仍需 Ban 对冒烟执行的常规批准，费用 ≈24 次校验调用量级）。
- T022 开启态评测臂前置条件（未清偿不得执行，否则静默测空 + 白烧 API）：**P1-2（装配断层）与 P1-3（DISPUTE 绕过 gate）必须清偿并补对应回归测试**；SC-6 的"断言覆盖提升"数据依赖在线校验真实生效，装配断层会让评测臂产出无效结论。
- P2 各项不阻断冒烟与评测，建议随 T023 交付报告一并清偿（P2-1/P2-4 一行级改动优先；P2-3/P2-5 是收口型清理；P2-6/P2-7 口径与文档同步）。
- 合并层面：当前分支形态对 SC-1/SC-2/SC-3 合格；US3 的生产可用性以 P1-2/P1-3 清偿为前提。建议清偿后本报告随交付报告归档（tasks.md T024 流程）。

—— 审查完毕。本报告只读产出，未改任何代码、未跑任何 API 评测。
