# 数据集补缺清单：KB-API-017「Payment 列表、过滤与分页」

生成时间：2026-11-21（specs/008 T017）。产品代码不改动；本清单供数据集侧作者定位与补录。

## 一、问题陈述

题库 3 道题把 `KB-API-017` 列为必需文档，但**所有历次检索诊断中它都没有出现在任何查询口径与检索路径的前 20 个片段里**：

| 诊断报告 | 日期 | 结论 |
|---|---|---|
| `var/benchmark/20261117-retrieval-diagnosis.md` | 2026-11-17 | 不可召回（initial / full、qwen3 集合） |
| `var/benchmark/20261118-retrieval-observed-fixed.md` | 2026-11-18 | 不可召回（observed / full、qwen3 集合） |
| `var/benchmark/20261119-retrieval-actual.md` | 2026-11-19 | 不可召回（actual、qwen3 集合） |
| `var/benchmark/20261121-retrieval-full300.md` | 2026-11-21 | **无**不可召回（bge-m3 集合）——`20261121-retrieval-full300.md` 最后一节写"所有必需文档都在至少一条路径的前 20 个片段里" |

## 二、修正原结论：不是数据集缺文件

核查结果（2026-11-21）：

- **文档存在**：`flowpay-dataset/flowpay-dataset/data/knowledge/api_docs/KB-API-017_list_and_filter_payments.md`，front matter `indexing: include`
- **发布清单已收录**：`release_manifest.json` 第 153 行含该文件
- **两个知识集合都已入库**：qwen3 集合 6 分块、bge-m3 集合 6 分块（chunks 表可查）
- bge-m3 空间的全量检索（full300）确认能召回——此前"不可召回"只发生在 **qwen3 空间**

**真实问题**：qwen3-embedding 空间下，该文档的向量与"列表、过滤、分页"类查询的相关度排序始终落在 top-20 之外；bge-m3 空间正常。这是**嵌入空间的检索质量差异**，不是文件缺失。007 交付报告第 6 节"KB-API-017 不在知识集合"的表述不准确，以本清单为准。

## 三、引用该文档的题目（3 道）

| 题号 | 题型 | required_documents | planned_evidence_ids |
|---|---|---|---|
| FP-BM-MD-0102 | multi_document | 含 KB-API-017 | doc:KB-API-017 |
| FP-BM-TV-0104 | temporal_versioned | 5 篇含 017 | doc:KB-API-017 |
| FP-BM-TV-0116 | temporal_versioned | 5 篇含 017 | doc:KB-API-017 |

这 3 题在 qwen3 空间的失败里，017 缺失是证据未全的直接原因之一。

## 四、建议与验证步骤

1. **数据集侧**：无需补录文件（文件已在）；如需提高 qwen3 空间的召回，可考虑在该文档 front matter 增强关键词（如"列表""分页""filter"的明确标题词），属于内容优化而非补缺。
2. **产品侧**：无动作。bge-m3 集合（现用）召回正常。
3. **验证步骤**：若数据集侧做了内容调整 → 重建知识集合（换 collection_id）→ 跑 `scripts/benchmark/retrieval.py --per-type 16` → 确认这 3 题的必需文档全部命中。
4. **交付报告勘误**：specs/007 交付报告第 6 节第 4 条应更正为"KB-API-017 在 qwen3 空间召回不足（嵌入空间差异），bge-m3 空间正常；非数据集缺文件"。

## 五、状态

- 数据集文件：✅ 已存在且已入库（无需补录）
- qwen3 空间召回：❌ 不足（已停产该空间，不再影响现行评测）
- bge-m3 空间召回：✅ 正常
- 结论：**无需数据集侧行动**；本清单用于修正归因记录，防止后续误判。
