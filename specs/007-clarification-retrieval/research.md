# 研究：007 多轮澄清覆盖与文档召回

本文件记录 plan 阶段的关键取舍；未决项均已在 spec 的 Clarifications 中定案，无 NEEDS CLARIFICATION。

## R1 澄清原语放在哪里

- **Decision**：新增 `workflows/multi_agent/clarification.py`，承载主题枚举、`ClarificationState`、
  `ClarificationRequest` 与 `clarify_tool`；`manual-loop` 的 `ask_user` 工具与行为保持不变。
- **Rationale**：项目约定阶段独占的编排、提示词与状态留在各阶段；`core` 不依赖工作流语义。
  新工具与旧工具同名 `ask_user` 但 schema 不同，由各阶段各自装配，避免旧阶段被迫接受新参数。
- **Alternatives**：扩展 `manual_loop.AskUserArguments`（改动最小，但把背景主题灌进旧阶段，
  且 `Case` 与澄清状态耦合）；放 `core/tools`（把业务追问语义下沉到通用层，违反分层约定）。

## R2 澄清状态如何更新而不增加模型调用

- **Decision**：扩展 plan 节点的 `set_tasks` schema，在拆任务的同时返回 `clarification`：
  `answers: [{topic, value}]` 与 `unavailable: [topic]`；plan 解析后先更新状态再拆任务。
- **Rationale**：plan 每轮排查都会调用一次模型，且已能看到用户最新消息与简报；复用同一次
  结构化输出可做到零新增请求。相比新增专用抽取调用，少一次失败点与用量。
- **Alternatives**：新增抽取调用（最准，但每轮多一次请求）；只用正则抽取编号/时间
  （`goal`/`symptom_details`/`api_version` 无法可靠抽取）；不记录值只记“已问”
  （后续 worker 无法使用已提供的背景信息）。

## R3 “证据是否足够”如何判定

- **Decision**：synthesize 的同一次模型调用增加 `set_response` 结构化工具：
  `mode: answer | clarify`；clarify 携带仍缺条件/主题与问题文本；解析失败回退为
  “有 pending 则 clarify，否则按正文作答”。
- **Rationale**：证据是否足够依赖报告语义，规则难以覆盖；结构化决策可测试、可统计，且不增加
  请求数。回退路径保证供应商不支持工具调用时整轮不失败。
- **Alternatives**：确定性规则（SQL 被阻塞或无证据 → 追问）；worker 自评证据充分性
  （工人容易在信息不全时自评过高）；只要有 pending 就问（现状，无法覆盖“查了但不足”）。

## R4 等待轮的回答形态

- **Decision**：clarify 模式必须由模型生成“已查到 + 仍需补充”的完整正文；无报告时明确说明
  尚未查到。pending 中的条件与主题只作为素材，不再原样转发 worker 问题。
- **Rationale**：现状等待轮直接回传 `pending[0].question`，丢弃全部调查报告；用户看不到已有
  进展，也让“先查后问”失去意义。
- **Alternatives**：只拼接报告原文与 worker 问题（机械、可能泄露内部措辞）；只发追问
  （现状）。

## R5 澄清状态的数据形状与持久化

- **Decision**：`ClarificationState` 存 `asked_topics`、`answers`、`unavailable_topics`，
  写入既有 `saved_state` 检查点 `"clarification"` 键；主题枚举封闭、单值 ≤500 字符。
- **Rationale**：无需迁移，沿用会话检查点；worker 每轮从零开始，必须靠简报恢复状态。
- **Alternatives**：新增数据库表（超出需求）；塞进 `Case.known`（条件与背景叙述混在一起，
  改口语义会被错误应用到主题）。

## R6 列出已问主题是否可重复询问

- **Decision**：已问主题不再重复；用户回答后写入 `answers`，明确拿不到写入
  `unavailable_topics`；后续轮次简报展示三类状态，工具处理函数拒绝重复索要。
- **Rationale**：对应 spec 的“不重复索要”与“拿不到不再问”；让模型无需自己记住。
- **Alternatives**：允许换措辞再问一次（多耗轮次，题库轮预算 2–4 不够）；只靠模型记忆
  （worker 无状态，必然重复）。

## R7 登录态绑定账户如何不索要

- **Decision**：`sql_tool` / `log_tool` 在构造时剔除 `scope_overrides` 已绑定的必填字段，
  并从 schema properties 中移除；描述追加“由系统绑定”。内部账号无覆盖值，schema 不变。
- **Rationale**：模型看不到必填项就不会先问；服务端 `scope_of` 仍强制覆盖值，安全语义不变。
- **Alternatives**：只改提示词（schema 仍标必填，模型大概率仍问）；运行时报错后再提示
  （浪费一次工具调用与一轮对话）。

## R8 离线检索评测怎么构造

- **Decision**：新增 `scripts/benchmark/retrieval.py`，直接构造 `PostgresVectorStore`、
  `EmbeddingClient` 与空事件 sink，计算 `required_documents` 的 recall@k；查询两种：
  首条消息、首条消息 + 全部 `available_user_facts`；同一查询分别走纯向量与混合。
- **Rationale**：题库没有参考检索词；两种口径能分离“查询缺信息”与“召回不到”。直接构造
  客户端不经过会话编排，不调用聊天模型，成本仅嵌入。
- **Alternatives**：用实际运行中的 `knowledge.request` 查询文本（受模型行为漂移影响，无法
  在改动前后稳定对比）；用题库文档标题查询（等于泄题，召回虚高）。

## R9 检索改造的选择规则

- **Decision**：诊断报告中按差值决策：`full - initial` 的 recall@10 ≥10pp → 做“把澄清事实
  拼入查询”的查询改写；两者都低且差 <10pp → 在混合参数、分块或关键词路中选一项；
  必需文档不在集合则单列不可召回。
- **Rationale**：对应 spec 的两条因果假设；先量化再改，避免盲目上手段。
- **Alternatives**：直接默认开混合检索（006 已证明端到端无稳定增益）；直接做查询改写
  （若主因是召回则无效）。

## R10 基准口径修复

- **Decision**：`run.py` 在 `turn == limit` 时先把最终轮 `Exchange` 记入 transcript，再记
  `max_clarifications_exceeded`；`conversation.py` 读取 `topics` 并映射到题库类别；
  新增 `scripts/benchmark/clarification.py` 计算覆盖与重复问。
- **Rationale**：现有报表的 waiting_user 计数与归因都漏掉最终轮（A 少 7、C 少 7/8），
  机制验收无法测量。
- **Alternatives**：只在报表另加一列 final_wait（归因仍漏）；人工逐题核对（不可重复）。

## R11 模型与基线一致性

- **Decision**：固定 `deepseek-v4-1-flash-economy-261017` 与今日 96 题样本；新版跑前先做一次
  基线复核，总分偏离超过 ±3 题则停手讨论。
- **Rationale**：Ban 确认新模型与今日中转模型参数/上下文一致；但供应商侧波动仍可能造成
  偏差，先复核可避免把模型差异记成机制效果。
- **Alternatives**：双模型各跑一遍（成本翻倍）；直接信任复用基线（风险不可见）。
