# 研究记录：004

## 参考设计要点（learn-claude-code）

- **s08 四步压缩管线**：转存→归档→微压缩→LLM 摘要；配对保护（tool_use/tool_result）；reactive_compact（prompt_too_long → 存 transcript、摘要旧历史、保留最近 5 条、限补救 1 次）。
- **s09 记忆四子系统**：存储（一记忆一文件+frontmatter+索引）/ 召回（轻量 LLM 选 ≤5 条，失败降级关键词，注入仅作背景、冲突以当前为准）/ 提取（回合结束自动，scope 过滤 + 临时标记 + 去重）/ 整理（阈值触发，快照-替换-回滚）。
- 借鉴取舍：召回选择改结构化键+语义（FlowPay 键空间确定性高，省每轮 LLM 调用）；整理改 SQLite 事务（快照-回滚是给无事务 JSON 文件准备的）；提取改确定性纯函数（findings/case/回答已结构化，003 R4「先纯结构化」先例）。

## 现状事实核对（2026-11-15）

- ContextPolicy 六字段（projection.py:36-49）；`_drop_oldest_rounds` 可删组只含 assistant+tool——夹在轮次间的 system briefing 消息永不删除（manual_loop.py:360-361 每轮 add_system_message 进检查点）。
- manual_loop.py:449 checkpoint actions 无消费方（restore 只读 case/queried）。
- multi-agent 调查员 remember=False（investigation.py:329/421），跨轮靠 turn_briefing（:182）注入 case_note+findings；seen 由 fold_finding_record_ids（graph.py:62）并入 findings 编号，synthesize 核对防编造（investigation.py:528-537）。
- EventStore append-only 触发器按表定义（store.py:45-52）；memory.sqlite 独立文件不受约束，也不宜共用（可变/可淘汰语义与事件溯源相纠缠）。
- PostgresVectorStore 集合就绪后不可变（store.py:147）；test_knowledge.py 默认 skipif 无 FLOWPAY_TEST_POSTGRES → 记忆存储选 SQLite，验收不退化为 skip 口径。
- openai_chat.py:141-150 APIStatusError → RuntimeError("Model HTTP {status}")，body 不进异常 → reactive compact 需小改。
- 事件命名风格「域.动作」（context.compacted / run.finished / tool.result）→ memory.* / context.reactive_compacted 对齐。
- 容器基线实测：13 failed（11×test_agent_loop[manual-loop-*] + 2×test_multi_agent）/ 238 passed / 3 skipped。

## 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 记忆存储 | memory.sqlite 单表 + JSON 向量列 + 内存余弦 | ≤200 条无需向量库；测试零外部依赖；pgvector 集合不可变 |
| 抽取方式 | 确定性纯函数零 LLM | 003 R4 先例；蒸馏等真机数据 |
| seen 处置 | 不淘汰，封顶查询键 | 淘汰编号会误判编造，削弱 DISPUTE |
| 快照-回滚 | 不需要 | SQLite 单事务原子性已保证 |
| 记忆注入 | 并入既有 briefing 段落 | 不新增消息流条目，恶化 briefing 封顶问题 |
