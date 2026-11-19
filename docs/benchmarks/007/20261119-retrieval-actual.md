# 离线检索诊断报告（specs/007 US5）

生成时间：2026-11-19 16:08 UTC。
样本：每类前 16 题（0=全量），计 96 题，其中有必需文档标签的 96 题；单次检索 limit=20。
actual 口径：新臂会话匹配 96 题、跳过 0 个会话；查询 = 首条消息 + 最终检查点 clarification.answers 的主题值（产品 query_with_facts 的离线镜像）。
方法：四种查询口径（initial=首条消息；observed=首条消息+该会话后续用户回复；actual=首条消息+检查点实际收集到的主题值；full=首条消息+全部可披露事实，仅作上界对照）× 两条检索路径（vector=纯向量；hybrid=关键词+向量 RRF），各检索一次后按前缀计算 recall@k（片段 source.doc_id 命中即算召回）。混合检索已开启；全程不调用聊天模型。

## 全样本平均 recall@k

| 路径 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| vector | initial | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| vector | observed | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| vector | actual | 20.4% | 44.1% | 61.6% | 74.6% | 75.4% |
| vector | full | 21.1% | 49.7% | 65.6% | 77.9% | 78.8% |
| hybrid | initial | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| hybrid | observed | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| hybrid | actual | 20.4% | 44.1% | 61.6% | 74.6% | 75.4% |
| hybrid | full | 21.1% | 49.7% | 65.6% | 77.9% | 78.8% |

## 分题型 recall@k

### vector

| 题型 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| multi_document | initial | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | observed | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | actual | 18.8% | 40.6% | 65.6% | 78.1% | 78.1% |
| multi_document | full | 18.8% | 37.5% | 56.2% | 68.8% | 68.8% |
| multi_hop | initial | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | observed | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | actual | 21.9% | 46.9% | 59.4% | 68.8% | 71.9% |
| multi_hop | full | 18.8% | 50.0% | 62.5% | 75.0% | 78.1% |
| multi_source | initial | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | observed | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | actual | 15.7% | 45.1% | 56.1% | 73.0% | 74.3% |
| multi_source | full | 17.3% | 46.8% | 62.5% | 75.5% | 78.0% |
| simple_retrieval | initial | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | observed | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | actual | 18.8% | 37.5% | 62.5% | 68.8% | 68.8% |
| simple_retrieval | full | 18.8% | 50.0% | 62.5% | 68.8% | 68.8% |
| temporal_versioned | initial | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | observed | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | actual | 27.5% | 53.1% | 70.2% | 82.1% | 82.1% |
| temporal_versioned | full | 19.7% | 50.2% | 68.9% | 83.3% | 83.3% |
| tool_use_diagnostic | initial | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | observed | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | actual | 19.8% | 41.7% | 55.7% | 77.1% | 77.1% |
| tool_use_diagnostic | full | 33.3% | 63.5% | 80.7% | 95.8% | 95.8% |

### hybrid

| 题型 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| multi_document | initial | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | observed | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | actual | 18.8% | 40.6% | 65.6% | 78.1% | 78.1% |
| multi_document | full | 18.8% | 37.5% | 56.2% | 68.8% | 68.8% |
| multi_hop | initial | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | observed | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | actual | 21.9% | 46.9% | 59.4% | 68.8% | 71.9% |
| multi_hop | full | 18.8% | 50.0% | 62.5% | 75.0% | 78.1% |
| multi_source | initial | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | observed | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | actual | 15.7% | 45.1% | 56.1% | 73.0% | 74.3% |
| multi_source | full | 17.3% | 46.8% | 62.5% | 75.5% | 78.0% |
| simple_retrieval | initial | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | observed | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | actual | 18.8% | 37.5% | 62.5% | 68.8% | 68.8% |
| simple_retrieval | full | 18.8% | 50.0% | 62.5% | 68.8% | 68.8% |
| temporal_versioned | initial | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | observed | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | actual | 27.5% | 53.1% | 70.2% | 82.1% | 82.1% |
| temporal_versioned | full | 19.7% | 50.2% | 68.9% | 83.3% | 83.3% |
| tool_use_diagnostic | initial | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | observed | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | actual | 19.8% | 41.7% | 55.7% | 77.1% | 77.1% |
| tool_use_diagnostic | full | 33.3% | 63.5% | 80.7% | 95.8% | 95.8% |

## 口径差值（observed-initial）

| 路径 | @1 | @3 | @5 | @10 | @20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector | +0.0pp | +0.0pp | +0.0pp | +0.0pp | +0.0pp |
| hybrid | +0.0pp | +0.0pp | +0.0pp | +0.0pp | +0.0pp |

## 口径差值（actual-initial）

| 路径 | @1 | @3 | @5 | @10 | @20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector | +5.0pp | +9.0pp | +14.7pp | +12.6pp | +12.3pp |
| hybrid | +5.0pp | +9.0pp | +14.7pp | +12.6pp | +12.3pp |

## 口径差值（full-initial）

| 路径 | @1 | @3 | @5 | @10 | @20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector | +5.7pp | +14.5pp | +18.6pp | +15.8pp | +15.7pp |
| hybrid | +5.7pp | +14.5pp | +18.6pp | +15.8pp | +15.7pp |

## 结论

通过：actual-initial 的 recall@10 提升 +12.6pp（阈值 10pp）

判据：observed-initial 的 recall@10 提升 ≥ 10pp 才算改造达标；full 只是上界对照，不单独作为效果。

## 不可召回文档（任何口径与路径的前 20 个片段里都没有出现）

- KB-API-017

这些文档单列为不可召回，不归因于查询改写或混合参数。
