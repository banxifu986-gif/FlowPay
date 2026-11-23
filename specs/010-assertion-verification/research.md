# 研究：010 逐断言校验

覆盖 spec 澄清后仍属实施选型的决策点。每项给出决策、依据与被否选项。

## D1 校验器的代码位置与分层

**Decision**：`core/verify/` 新包承载通用能力（类型契约、证据装配、抽取/校验 LLM 交互、处置映射、fail-open）；业务筛选（哪些句子要校验）与接线放 `workflows/multi_agent/assertion_gate.py`；回放与断点归类放 `scripts/benchmark/assertion_audit.py`。
**Rationale**："对给定文本+证据集做逐断言事实核查"是可复用能力（未来 manual_loop 接线不需要重写）；"带引用标记的断言句才校验"沿用 citation_check 既有口径，属业务决策。分层与 CLAUDE.md 纪律一致，且使树A（纯 core）与树B（workflows+scripts）文件级不相交。
**Alternatives considered**：全部放 workflows（能力无法复用、与 009 citation_check 重复叠加，否）；全部放 core（把业务筛选口径下沉通用层，违反分层纪律，否）。

## D2 LLM 输出的解析方式

**Decision**：抽取与校验均以 JSON 输出 + 既有解析工具解析；解析失败、字段缺失、超限按 fail-open 放行原文并记录事件。
**Rationale**：项目已有 JSON 边界约定与解析实现（core/json.py）；模型侧 structured output 绑定供应商能力，跨模型不可移植；fail-open 保证校验器故障永不阻断回答（spec FR-6）。
**Alternatives considered**：供应商 structured output（绑定模型能力，否）；自由文本再正则解析（脆弱，否）。

## D3 校验事件的组织

**Decision**：单事件 `assertion.audit` 承载一次校验的全部产物（判定清单、处置、fail_open、矛盾分歧详情）；模型调用自身走既有 `model.request`/`model.response` 通道不重复发。
**Rationale**：一个事件 = 一次校验 = 一条可审计单元，报表与回放报告共用同一结构（AssertionAudit 契约）；既有事件通道已证明可承载新事件类型（009 `tool.outage`/`turn.progress` 先例）。
**Alternatives considered**：逐断言一事件（事件量膨胀、聚合口径难算，否）；复用 message 事件附加字段（改既有事件形状，违反兼容纪律，否）。

## D4 全轮片段的封顶默认值

**Decision**：按 score 排序截取，默认 8000 字符，配置项实施时定名，冒烟实测校准。
**Rationale**：PassageSet 上限 40000 字符吃满会稀释判定且费用高；R96B 每轮实际命中通常远小于上限；8000 ≈ 15–20 条片段，覆盖"绑定引用 + 高分反证"足够。
**Alternatives considered**：不封顶（费用+稀释，否）；只给绑定引用（漏反证，违背 spec 核心诉求，否）；按相似度阈值筛选（阈值难定，排序截取更稳，否）。

## D5 收紧替换的句级匹配

**Decision**：校验输出携带"原句全文 + 收紧句"；处置按既有句级切分（`split_sentences` 先例）逐句等值匹配替换，命中多处取第一处；匹配失败该断言降级不替换。替换句过数字/编号子集校验（数字与编号必须为原句 ∪ 绑定证据的子集）。
**Rationale**：等值匹配防错位替换（比偏移量稳健，不受 citation_check 改写影响——不匹配即降级，安全侧）；子集校验防改写引入未经校验的新事实（spec 防线②）。
**Alternatives considered**：按字符偏移替换（校验链改写后偏移漂移，错位风险，否）；LLM 直接输出整段改写文本（无法逐句归因、误伤面大，否）。

## D6 回放的草稿恢复与同构性

**Decision**：回放从验收臂事件库恢复**原始草稿**（synthesize 决策调用的 model.response 载荷），重放确定性校验链（编造核对 + 引用标记校验）后执行校验器；不直接拿发布文本校验。
**Rationale**：发布文本已含降级改写，直接校验会把"已降级句"混入判定，结论不可迁移到在线档；model.response 载含完整 completion 正文（调查已核实），草稿恢复零模型成本。
**Alternatives considered**：直接用发布文本（同构性破坏，否）；重跑 agent 循环（费用爆炸且草稿随机，否）。

## D7 断点归类的期望证据来源

**Decision**：复用 `scripts/benchmark/evidence.py` 的证据 ID 对齐器——把本轮实际取到的片段/记录翻译成题库证据 ID，期望证据 ∉ 本轮片段集 → 没召回；∈ 集但断言未绑定 → 绑错。
**Rationale**：对齐器已是评测侧既有事实（009 SC-6/SC-7 口径），"没召回"必须对照题库标准证据才判得准（grill 澄清⑥已拍板回放侧归类）。
**Alternatives considered**：不对照题库的启发式归类（"真没有"与"没绑"分不开，否）；生产侧归类（无标准答案，否）。

## D8 冒烟样本的选取

**Decision**：12 题 = 三类断点嫌疑各 3（按 009 attribution 主因映射：evidence_miss→没召回、conclusion_wrong→表述越界、其余失败→绑错嫌疑）+ 通过题 3（误伤基线）；B3 集成时定题单。
**Rationale**：按主因抽保证三类断点在冒烟里各有实例（SC-4 要求 ≥1 例/类）；通过题是误伤基线的最小样本。全量回放（96 题）才是断点分布的正式出数，冒烟只验证机制跑通。
**Alternatives considered**：随机抽（可能抽不到某类断点，SC-4 无法达成，否）；全部失败题（96 题量级运行，属 B4 待批准范围，否）。

## D9 配置面与开关命名

**Decision**：`assertion_audit_enabled`（默认 false）+ 片段封顶预算字段进 Settings 与 local.toml；回放脚本参数走 CLI 不进配置面。
**Rationale**：项目惯例（面向运营可调参数进 Settings 与 local.toml，密钥无关）；开关默认关保证 B0–B2 期间生产路径零变化（SC-1）；回放是评测工具不入运营配置。
**Alternatives considered**：回放参数进配置面（评测参数混入运营面，否）；开关默认开（上线即改变回答路径，违背"旁路为主"拍板，否）。

## D10 矛盾分歧详情的载荷形态

**Decision**：`assertion.audit` 事件载荷中矛盾断言额外携带 `conflict`（冲突双方出处 + 冲突点描述）；回答文本统一降级"未能核实"不呈现分歧；回放报告单列矛盾档占比。
**Rationale**：clarify 会话拍板"降级+详情落事件"——评测口径稳优先，诊断信号不丢；占比数据决定是否立项升级为"呈现分歧"（spec FR-5 升级路径）。
**Alternatives considered**：回答中呈现分歧（双表述触碰禁止结论风险，clarify 已否）；矛盾与证据不足合并不区分（丢诊断信号，否）。
