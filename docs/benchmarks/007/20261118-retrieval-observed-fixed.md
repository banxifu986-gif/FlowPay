# 离线检索诊断报告（specs/007 US5）

生成时间：2026-11-18 09:53 UTC。
样本：每类前 16 题（0=全量），计 96 题，其中有必需文档标签的 96 题；单次检索 limit=20。
observed 口径：真实臂会话匹配 96 题、跳过 0 个会话（首条消息匹配不到题目的会话不计）。
方法：三种查询口径（initial=首条消息；observed=首条消息+该会话后续用户回复；full=首条消息+全部可披露事实，仅作上界对照）× 两条检索路径（vector=纯向量；hybrid=关键词+向量 RRF），各检索一次后按前缀计算 recall@k（片段 source.doc_id 命中即算召回）。混合检索已开启；全程不调用聊天模型。

## 全样本平均 recall@k

| 路径 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| vector | initial | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| vector | observed | 13.5% | 36.0% | 51.3% | 64.7% | 65.0% |
| vector | full | 21.1% | 49.7% | 65.6% | 77.9% | 78.8% |
| hybrid | initial | 15.4% | 35.1% | 46.9% | 62.1% | 63.1% |
| hybrid | observed | 13.5% | 36.0% | 51.3% | 64.7% | 65.0% |
| hybrid | full | 21.1% | 49.7% | 65.6% | 77.9% | 78.8% |

## 分题型 recall@k

### vector

| 题型 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| multi_document | initial | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | observed | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | full | 18.8% | 37.5% | 56.2% | 68.8% | 68.8% |
| multi_hop | initial | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | observed | 12.5% | 43.8% | 53.1% | 62.5% | 62.5% |
| multi_hop | full | 18.8% | 50.0% | 62.5% | 75.0% | 78.1% |
| multi_source | initial | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | observed | 9.3% | 32.3% | 47.7% | 56.5% | 58.0% |
| multi_source | full | 17.3% | 46.8% | 62.5% | 75.5% | 78.0% |
| simple_retrieval | initial | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | observed | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | full | 18.8% | 50.0% | 62.5% | 68.8% | 68.8% |
| temporal_versioned | initial | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | observed | 7.7% | 36.1% | 58.2% | 74.5% | 74.5% |
| temporal_versioned | full | 19.7% | 50.2% | 68.9% | 83.3% | 83.3% |
| tool_use_diagnostic | initial | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | observed | 17.2% | 28.6% | 42.7% | 57.3% | 57.3% |
| tool_use_diagnostic | full | 33.3% | 63.5% | 80.7% | 95.8% | 95.8% |

### hybrid

| 题型 | 口径 | @1 | @3 | @5 | @10 | @20 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| multi_document | initial | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | observed | 15.6% | 37.5% | 56.2% | 75.0% | 75.0% |
| multi_document | full | 18.8% | 37.5% | 56.2% | 68.8% | 68.8% |
| multi_hop | initial | 12.5% | 37.5% | 43.8% | 56.2% | 59.4% |
| multi_hop | observed | 12.5% | 43.8% | 53.1% | 62.5% | 62.5% |
| multi_hop | full | 18.8% | 50.0% | 62.5% | 75.0% | 78.1% |
| multi_source | initial | 12.7% | 33.1% | 41.1% | 51.0% | 54.2% |
| multi_source | observed | 9.3% | 32.3% | 47.7% | 56.5% | 58.0% |
| multi_source | full | 17.3% | 46.8% | 62.5% | 75.5% | 78.0% |
| simple_retrieval | initial | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | observed | 18.8% | 37.5% | 50.0% | 62.5% | 62.5% |
| simple_retrieval | full | 18.8% | 50.0% | 62.5% | 68.8% | 68.8% |
| temporal_versioned | initial | 17.8% | 36.5% | 55.1% | 76.0% | 76.0% |
| temporal_versioned | observed | 7.7% | 36.1% | 58.2% | 74.5% | 74.5% |
| temporal_versioned | full | 19.7% | 50.2% | 68.9% | 83.3% | 83.3% |
| tool_use_diagnostic | initial | 15.1% | 28.6% | 35.4% | 51.6% | 51.6% |
| tool_use_diagnostic | observed | 17.2% | 28.6% | 42.7% | 57.3% | 57.3% |
| tool_use_diagnostic | full | 33.3% | 63.5% | 80.7% | 95.8% | 95.8% |

## 口径差值（observed-initial）

| 路径 | @1 | @3 | @5 | @10 | @20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector | -1.9pp | +0.9pp | +4.4pp | +2.6pp | +1.9pp |
| hybrid | -1.9pp | +0.9pp | +4.4pp | +2.6pp | +1.9pp |

## 口径差值（full-initial）

| 路径 | @1 | @3 | @5 | @10 | @20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| vector | +5.7pp | +14.5pp | +18.6pp | +15.8pp | +15.7pp |
| hybrid | +5.7pp | +14.5pp | +18.6pp | +15.8pp | +15.7pp |

## 结论

未通过：澄清覆盖不足（observed +2.6pp，full 上界 +15.8pp）——真实对话没拿到这些事实，不能宣称 +10pp

判据：observed-initial 的 recall@10 提升 ≥ 10pp 才算改造达标；full 只是上界对照，不单独作为效果。

## 不可召回文档（任何口径与路径的前 20 个片段里都没有出现）

- KB-API-017

这些文档单列为不可召回，不归因于查询改写或混合参数。
