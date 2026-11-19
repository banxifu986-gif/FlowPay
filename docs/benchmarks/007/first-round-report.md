# Benchmark 对照报表

题目 96 道，模型 deepseek-flash。单次运行，阶段之间几道题的差距不足以区分策略差异与模型波动；方向性结论（某一级在哪类题上更强）才可读。
检索路径：混合；治理配置：local.toml 默认值


两条独立的判据，不能互相替代：

- **任务通过**：必需结论全部被支持，且没有说出禁止结论。与用了哪些文档无关。
- **证据全命中**：检索是否走到 benchmark 指定的那几块原文。用别的文档答对时不通过。

不报 strict 路由：benchmark 按实体登记工具，运行时按关系登记，两套词汇对不上，这是实测得出的设计而非缺陷。

## 分层总览

| 阶段 | 单轮任务通过 | 多轮任务通过 | 合计 | 证据全命中 |
| --- | ---: | ---: | ---: | ---: |
| multi-agent | 21/32 | 26/64 | 47/96 | 53/96 |

## multi-agent

| 题型 | 题数 | 任务通过 | 证据全命中 | 断言覆盖 | 违反禁止 | 其中多轮 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| simple_retrieval | 16 | 14 | 9 | 14/16 | 0 | 0 |
| multi_document | 16 | 7 | 6 | 22/32 | 0 | 0 |
| multi_hop | 16 | 8 | 10 | 21/32 | 0 | 16 |
| temporal_versioned | 16 | 6 | 10 | 19/32 | 0 | 16 |
| multi_source | 16 | 6 | 9 | 18/32 | 0 | 16 |
| tool_use_diagnostic | 16 | 6 | 9 | 18/32 | 0 | 16 |

澄清信号：waiting_user 87、text 1、ask_user_tool 25
多轮偏离：repeated_category 20、unexpected_category 16、max_clarifications_exceeded 15
