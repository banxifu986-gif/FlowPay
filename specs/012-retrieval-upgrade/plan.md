# 实施计划：FlowPay 检索链路优化（变体改写 + 精排对照 + ID 关键词路）

**输入**：[spec.md](spec.md) ｜ **状态**：计划已编写，未实施
**设计工件**：[研究与取舍](research.md) · [数据模型](data-model.md) · [接口与验收契约](contracts/retrieval-variants.md) · [快速验收](quickstart.md)

## 摘要

检索变体在检索客户端层统一生效：`AgentRuntime.search_knowledge` 编排变体生成，`KnowledgeClient` 新增多文本批嵌入 + 逐路检索 + RRF 融合；变体失败回退原句单路（现状行为）。离线评测扩展变体口径与 top20 存档，精排对照脚本消费存档；ID 对照实验决定逐 ID 关键词路是否实施。端到端验收落在 011 同 60 题复跑对比。

## 技术上下文与项目门禁

- 沿用 Python、pytest、现有 ChatModel/EmbeddingClient/PostgreSQL 知识存储；新依赖仅限精排对照阶段与 embedding 同栈的 rerank API 组件（达标进实施决策时才入依赖清单）。
- 检索行为改动只落在 `core/execution/runtime.py`、`core/knowledge/client.py`、`core/knowledge/store.py`；工作流（`workflows/`）与 `core/verify` 零改动——所有出口经 `search_knowledge` 通道透明获得变体多路。
- 评测扩展落在 `scripts/benchmark/retrieval.py`（变体口径 + 存档）与新脚本 `scripts/benchmark/rerank_offline.py`（存档重排）；评测标注永远只是评测材料。
- 先离线后实施：①④实验达标线（变体 ≥+10pp；精排 ≥+5pp 且 P50 ≤500ms）不达标则不实施对应部分；实施部分按 TDD 先写可证伪失败用例（回退、去重、条数一致、融合保护）。
- 编码前记录全量测试基线（当前容器基线 755 passed/5 skipped，失败基线为空）；011 的 FR 条款、题库与判分逻辑零改动。

## 分阶段实施及验收

| 阶段 | 实施 | 验收 |
| --- | --- | --- |
| ① 离线变体实验（US1） | `retrieval.py` 增变体口径（调用现有模型配置生成 2-3 变体并存档）与 top20 存档 JSONL；旧 96 题跑 initial vs 变体对照。 | 报告含差值曲线、逐题型分解、判定线；存档可独立复跑；变体 ≥+10pp 且单轮不降才进②。 |
| ② 变体实施（US2） | `KnowledgeClient.search_variants`（批嵌入 + 原句 hybrid + 变体向量路 + RRF）；`AgentRuntime.search_knowledge` 编排变体生成与回退；`workflows` 装配注入。 | TDD 门禁：回退行为、去重、条数一致、融合保护、模型故障零错误输出；全量测试与原套件回归通过。 |
| ③ ID 对照实验与实施（US3） | 评测脚本增 ID 拼接口径与逐 ID 匹配对照路；达标（≥+10pp）则在 `store.py` 关键词路实施逐 ID ILIKE（不引入分词器）。 | 报告证明或证伪现有关键词路对 ID 查询的命中；实施有测试门禁；不达标留触发条件。 |
| ④ 精排对照（US4） | 新脚本 `rerank_offline.py`：消费变体口径存档、调 rerank API 重排、输出前后 recall@k 与延迟分布。 | ≥+5pp 且 P50 ≤500ms 才进实施决策，否则报告留档；API 形状先用最小请求验证。 |
| ⑤ FAQ 补测（US5） | FAQ dev 40 题就绪后三组对照重测。 | 新旧题库结论并列，版本标注清晰。 |
| ⑥ 端到端对比（US5） | 011 正式基线（60 题 test）就绪后同题库复跑对比。 | 对比报告区分检索增益与模型波动；异常题单列不计成绩。 |

## 拟修改范围与非目标

- 修改切片：`backend/src/flowpay/core/knowledge/client.py`、`core/knowledge/store.py`、`core/execution/runtime.py`、`scripts/benchmark/retrieval.py`、新 `scripts/benchmark/rerank_offline.py`，及对应测试；装配注入点在 `flowpay/bootstrap.py` 或其既有装配链。
- 非 012 范围：`workflows/` 节点逻辑、`core/verify`、提示词正文、011 题库与判分、jieba/zhparser 迁移、BM25 全量替换、生产上线验收。
- 实验报告与存档写 `var/benchmark/`，不提交 SQLite、向量或运行日志。

## 设计复核

变体多路在客户端层统一挂载使四个检索出口与 FAQ 模式零改动生效；原句必参与 + 失败回退保证最坏情况等于现状；融合保护沿用现有 RRF 名额约束，杜绝关键词噪声挤掉向量命中。实验先行的顺序避免变体与精排抢同一块排序空间导致上限虚估；所有实施决策以离线数据为准，未达标项留可检验触发条件。
