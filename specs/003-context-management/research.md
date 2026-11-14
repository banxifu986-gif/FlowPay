# Research: 003-context-management

> 全部决策已在批准计划与独立审核（2026-11-14）中定案，本文档是决策记录沉淀，无遗留 NEEDS CLARIFICATION。

## R-1 摘要消息的形态选择

**Decision**: 中段 system 消息 + JSON 信封（`kind/text/record_ids/rounds`）。
**Rationale**: 投影的可删组只含 assistant+尾随 tool（projection.py:170-189），中段 system 天然存活，摘要永不被投影删除；`MessageLog.restore` 的 strict 校验接受 system 消息，零改动；manual-loop 已有中段 system 持久化先例（briefing：manual_loop.py:360-361 + turn.py:248-249；DISPUTE：turn.py:197）。
**Alternatives considered**: user 消息——违反「任何 user 永不删」且污染界面聊天投影；assistant 消息——恢复校验与 tool_calls 配对语义复杂，无先例。

## R-2 压缩触发口径

**Decision**: 单一字符口径 `compaction_trigger_chars`（缺省 0=关闭），度量沿用 json.dumps 字符数。
**Rationale**: 002 FR-9 已定字符近似（拒绝 tokenizer 依赖）；provider usage token 兜底线经审核砍除——无人要求 token 级触发，双口径属「未被要求的可配置化」（全局准则二）；压缩触发与投影目标语义衔接：原生流超过投影 char_budget 意味着投影即将整组删证据，此时压缩才划算。
**Alternatives considered**: chars + provider usage 双口径独立 opt-in——审核否决。

## R-3 摘要信封编号的来源

**Decision**: record_ids 由代码从被删轮次的 tool/user 消息内容经 `record_ids()` 确定性提取；模型仅生成叙事正文。`known_record_ids` 扩扫只读信封结构化字段，不扫描摘要全文。
**Rationale**: 独立审核指出「100% 进摘要」若靠模型填写则只是愿望，且模型在信封里新增的编号会经扩扫渗入 seen，反向弱化编造防线（违背 turn.py:38-39「assistant 转述不计入」既定口径）。代码提取使编号完整迁移成为机械保证。
**Alternatives considered**: 模型填写信封 + 提示词硬约束——不可验证；对摘要全文跑 record_ids()——模型正文转述不可靠，且可能引入模型杜撰的编号。

## R-4 防抖动与压缩目标

**Decision**: 触发线与压缩目标分离；压缩后目标 ≈ 触发线 60%（保留最近 2 个完整交换 + 摘要正文上限 4000 字符）。
**Rationale**: 若只压到触发线附近，下一轮立即再触发，多轮间抖动且每轮多付一次摘要调用；40% headroom 使正常增长下多轮内不重复触发。触发判定每次进入 think() 都执行（correction 重试循环内不触发），满足才压。
**Alternatives considered**: 压到触发线即停——抖动；固定轮数一刀切——不感知体积。

## R-5 直连护栏的实现形态

**Decision**: 格式 f-string 留在五节点调用点；core 只提供 `clip_text` 纯函数；policy 存在时节点自行对变量段调用。
**Rationale**: 独立审核逐一核实五节点格式各异（route/chitchat 裸问题、plan 条件拼简报、synthesize 简报+编号报告+空兜底、doc_qa 片段前缀）；core 函数内置五种文案无法保证「policy=None 逐字一致」（002 SC-1 式回归测试会红）；格式留节点使默认路径零改动天然成立。
**Alternatives considered**: `bounded_user_content(question, attachments, policy)` 统一形状——五节点默认路径必然改变，否决。

## R-6 摘要正文超上限的处理

**Decision**: 代码截断到上限后落盘（clarify 2026-11-14 决议 B）；摘要 prompt 写明上限作软控制，截断为罕见兜底。
**Rationale**: 压缩失败重试已划出范围，若「超限视为失败」则习惯性超长的模型会导致压缩永不落地（每轮触发、每次失败、白付调用）；截断只影响叙事正文，编号信封与 Case 重建（每轮由检查点字段驱动）均不受影响；与 002「预算未达成不算失败，存活不变量优先」哲学一致。摘要请求的 max_tokens 给到与上限匹配的输出余量，降低截断触发率。
**Alternatives considered**: 超限视为失败跳过——无重试时压缩永不落地；不设上限——检查点体积无硬上界。

## R-7 压缩调用的计费与追踪口径

**Decision**: 摘要调用经 `chat.call(..., output="internal")`（client.py:35 既有参数）；不计入 `Budget.spent_model_calls`；`usage_entries.append(from_completion(...))` 照常入账；trace 事件 `context.compacted`（before_chars/after_chars/rounds）与 `context.compaction_failed`。
**Rationale**: 压缩是基础设施调用，不挤占调查预算；002 先例「被拒调用也计 usage」（turn.py:81-82）确立费用诚实入账口径；审核核实 UsageSummary.model_calls 与 Budget 口径无对账代码，检查点 model_calls 存 budget 口径且从不恢复，internal 调用不进回答流——无口径破坏。
**Alternatives considered**: 占用调查预算——压缩挤占调查额度，体验变差；不计 usage——费用不可对账。

## R-8 推迟项与条件

**Decision**: 检查点静态摘要封顶（slim_for_checkpoint）与 recall 回查工具推迟；token 触发线砍除。
**Rationale**: slim 复用投影整组定义删不掉每轮追加的 briefing system 条目（真实增长源），P2-SC「体积曲线平缓」不可达，边际收益只剩「压缩关、投影开」组合——待 P0 真机体积数据确认需要后另行设计；recall 有真实价值（claim 去重挡住同键重查，模型无其他取回途径）但存在读放大（`read(session_id)` 整会话逐行解码）与预算刷损（无去重可反复调用）两问题——待 P0 冒烟给出真实压伤证据后立项，届时须带轮内 record_id 去重与事件读取 kind 过滤两条对策；token 触发线见 R-2。
**Alternatives considered**: 全部进 P0/P2——违反「最少代码」，为未证实的场景付实现成本。

## R-9 002 修订波及面

**Decision**: supersedes 标注覆盖 10 处（清单见 spec「对 specs/002 的修订」节），并预写「边界移动」理由说明。
**Rationale**: 独立审核全量扫描发现波及远不止 FR-8 一行：US2、FR-3、FR-10、范围外声明、data-model、plan、research R-5 与被拒备选、interfaces §5 均引用「检查点存未压缩原生消息」或「确定性重算」理由；不逐处标注则 speckit-analyze 会报两 spec 冲突。
**Alternatives considered**: 只标 FR-8——analyze 仍会报冲突；改 002 原文——历史工件应保持原样加注而非改写。

## R-10 冒烟门禁分级

**Decision**: P0 批真机冒烟必做（校准触发线/摘要上限），P1/P2 批降级为容器回归 + 冒烟尽力/补做。
**Rationale**: 002 T027 即为补做先例（真机依赖模型 API 可用性）；硬门禁会被环境卡住批次。
**Alternatives considered**: 每批硬门禁——环境阻塞风险。

## R-11 压缩×投影组合的正确性

**Decision**: 组合不变量测试钉住：压缩后对消息流跑投影，配对完整/存活集合（含摘要）/畸形输入原样返回三不变量 100% 保持。
**Rationale**: 摘要是中段 system（投影不可删集合）；投影继续作用于摘要之后存活的消息，占位替换与整组删除语义不变；两机制职责分层（压缩=写路径、投影=读路径），互不感知对方实现。
**Alternatives considered**: 压缩后禁用投影——丢失兜底能力。

## R-12 并行与直连路径的安全性

**Decision**: compaction 仅对 remember=True 生效（显式 `self.remember` 判断，不依赖隐式状态）；直连节点不经 think()，其无界输入由护栏（R-5）覆盖。
**Rationale**: 已核实 worker 显式 remember=False（investigation.py:271/363），route/plan/synthesize/doc_qa/chitchat 绕过 AgentTurn 直调 ChatClient；manual-loop 循环先回填全部工具结果再回 think（manual_loop.py:454-459），pause 前强制回填，压缩插入点落在整组边界即不破配对。
**Alternatives considered**: worker 流压缩——remember=False 单轮生命周期，无写路径意义。
