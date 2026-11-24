# 研究与取舍：检索链路优化（012）

## D1 变体多路的挂载层

**决策**：编排挂 `AgentRuntime.search_knowledge`，多路检索实现在 `KnowledgeClient.search_variants`；工作流节点零改动。
**理由**：`search_knowledge` 是全部检索出口（排查轮 `search_documents`、doc_qa、单 agent、manual_loop、FAQ 模式）的唯一通道，改一处全透明生效；`KnowledgeClient` 已封装嵌入与混合检索，批嵌入与多路融合同层最内聚。
**备选**：出口层各自接（排查轮+doc_qa 两处重复实现，易漂移）；只挂排查轮（doc_qa/FAQ 模式受益不到）。

## D2 多路检索形态

**决策**：原句走现有完整混合检索（现状行为不变），每个变体只走纯向量检索；原句 + 全部变体候选进同一个 RRF 池融合后截 `limit`。
**理由**：变体源自同一问题域，关键词路（整句 ILIKE）对自然语言查询结构性无效（specs/007 实测 vector/hybrid 差值恒 0），变体叠加关键词查询只增加成本；纯向量路是新查询表达的收益所在。RRF 复用 `reciprocal_rank_fusion`，沿用"向量路已命中的片段不被挤出名额"保护——融合前先按现状两路（原句 hybrid + 变体向量）汇总，每路名额约束在融合层生效。
**备选**：所有路都走 hybrid（N 路关键词查询成本，无增益证据）；变体结果简单并集去重（丢失排名信息）。

## D3 变体生成协议

**决策**：复用现有对话模型配置；一次调用输出 JSON 数组（≤3 条改写：口语化/关键词化/同义补全，语义不变）；解析失败、空数组、超时一律回退原句单路。变体与原句空白归一化后相同则剔除。
**理由**：与 007/009 的"事实拼接"改写兼容——变体改写作用在拼接后查询文本上（`query_with_facts`/`query_with_findings` 的产出作为变体生成输入），两层改写叠加而非互斥；封闭 JSON 输出让解析失败可判定，回退路径简单。
**备选**：条件触发（零命中才变体，两段式状态复杂且坏 case 延迟最敏感）；规则变体（同质化，预期增益低）。

## D4 逐 ID 关键词路

**决策**：实验口径 = 评测脚本把真实业务记录编号（`record_ids()` 已有）拼进查询后测两条路——现有关键词路（整句 ILIKE + trigram 兜底）与逐 ID 对照路（每个编号一个 ILIKE 模式取并集）。ID 口径 ≥+10pp 才在 `store.py` 实施逐 ID ILIKE；实施形态为在现有关键词 SQL 内增加编号子模式并集，保持 `KEYWORD_SIMILARITY_THRESHOLD` 兜底不变。
**理由**：归因修正——007 混合检索报告里 vector/hybrid 差值恒 0 的结构性原因是整句 ILIKE 几乎不可能命中；带精确编号的查询对关键词路有真实匹配条件，但需逐 ID 才能命中。不引入 jieba/zhparser（编号是 ASCII 精确子串，ILIKE 即可）。
**备选**：直接迁移 BM25（重依赖 + 迁移成本，无数据支撑）；只留文字触发条件（现有路径永远测不出信号，死条款）。

## D5 精排（rerank）对照

**决策**：离线对照脚本消费变体口径 top20 存档，调与 embedding 同供应商的 rerank API（bge-reranker 系，`/rerank` 端点形状：query + documents + top_n → 结果按 relevance_score 重排）；实施时先用最小请求验证 API 形状与延迟。达标线 ≥+5pp 且单次延迟 P50 ≤500ms 才进实施决策；实施形态 = 融合结果前 20 候选重排取 top-limit，挂在 `search_variants` 尾部。
**理由**：变体吃掉 initial 口径 top20→top10 的 16.2pp 大头后，精排剩余空间必须基于变体口径测量，否则上限虚估；同栈 API 零本地推理依赖（无 torch）。
**备选**：本地 reranker（数百 MB torch 依赖，与无本地推理架构取向冲突）；两线独立对照 initial（增益重叠，选型上限虚高）。

## D6 检索存档格式

**决策**：JSONL，每行一题一口径一路径：`{question_id, question_type, dialect, path, query, variants, ranked: [{passage_id, doc_id, chunk_no, score}]}`，`ranked` 按检索排名保序至 top-N（默认 20）。
**理由**：行式结构支持流式消费与部分重跑；`passage_id/doc_id/chunk_no` 足够离线重排（重排只改排名，不重取正文——rerank 需要 passage 正文，存档附 `text` 字段截断存储）。
**备选**：JSON 整档（部分重跑要整体读写）；只存 id（rerank 需正文，还得回查）。

## D7 评测脚本组织

**决策**：`retrieval.py` 原地扩展（变体口径 + 存档输出），精排对照独立新脚本 `rerank_offline.py`。
**理由**：retrieval.py 已有 DIALECTS×PATHS×recall@k 框架与口径差值渲染，变体口径是同框架增量；rerank 重排的输入输出链（存档→API→重排报告）独立性强，与检索诊断无共享状态。
**备选**：全并进 retrieval.py（单文件多职责膨胀）；rerank 也塞 retrieval.py（脚本耦合）。

## D8 变体数量与成本口径

**决策**：变体上限 3（模型可少给），每次 `search_knowledge` 调用都生成（澄清拍板）；变体文本批嵌入一次调用。延迟增量 ≈ 1 次短 LLM 调用（变体 prompt 输出 ≤3 行）+ 批嵌入。
**理由**：排查轮一次 turn 通常 2-4 次检索，上限 3 条×每变体一次向量检索的开销可控；批嵌入消除 N 次嵌入请求的固定开销（与 007 评测预取同理由）。
