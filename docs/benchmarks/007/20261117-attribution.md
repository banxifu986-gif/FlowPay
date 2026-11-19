# 基准失败题归因报告

生成时间：2026-11-17 10:42 UTC。单次运行，分类基于逐题信号，模型随机性导致的差异仍存在，结论留误差余量。

## 各臂模型

- A：deepseek-v4.1-flash-1017（`D:\coding\project\Flow\var\benchmark\20261117-071249-49fd18`）
- C：deepseek-v4.1-flash-1017（`D:\coding\project\Flow\var\benchmark\20261117-071251-67e7d4`）

## 分类总表

| 臂 | 题数 | 任务通过 | 证据全命中 | waiting_user | simulated_user | waiting_for_evidence | over_clarify | evidence_miss | conclusion_wrong |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 96 | 18 | 35 | 60 | 9 | 26 | 13 | 21 | 9 |
| C | 96 | 23 | 32 | 58 | 11 | 21 | 12 | 19 | 10 |


### A 失败明细

| 题号 | 题型 | 多轮 | 证据(文档/记录/断言) | 主因 | 信号 |
| --- | --- | --- | --- | --- | --- |
| FP-BM-SR-0001 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0005 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0013 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0017 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0021 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0025 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0029 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0037 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0041 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0053 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0057 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0061 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-MD-0002 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0006 | multi_document | 否 | 1/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0010 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0014 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0018 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0026 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0030 | multi_document | 否 | 0/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0034 | multi_document | 否 | 1/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0038 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0042 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0046 | multi_document | 否 | 0/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0054 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0058 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0062 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MH-0003 | multi_hop | 是 | 1/2 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0011 | multi_hop | 是 | 2/2 / 1/1 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category |
| FP-BM-MH-0015 | multi_hop | 是 | 1/2 / 0/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category |
| FP-BM-MH-0019 | multi_hop | 是 | 2/2 / 3/3 / 0/2 | over_clarify | waiting_user |
| FP-BM-MH-0023 | multi_hop | 是 | 1/2 / 0/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0027 | multi_hop | 是 | 2/2 / 2/2 / 1/2 | over_clarify | waiting_user |
| FP-BM-MH-0031 | multi_hop | 是 | 2/2 / 2/2 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:unexpected_category,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0039 | multi_hop | 是 | 2/2 / 0/1 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:unexpected_category,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0043 | multi_hop | 是 | 1/2 / 1/1 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0059 | multi_hop | 是 | 1/2 / 2/4 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0063 | multi_hop | 是 | 2/2 / 1/3 / 1/2 | over_clarify | waiting_user |
| FP-BM-TV-0104 | temporal_versioned | 是 | 2/5 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0112 | temporal_versioned | 是 | 0/3 / 0/2 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-TV-0116 | temporal_versioned | 是 | 1/5 / 2/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0120 | temporal_versioned | 是 | 2/3 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0124 | temporal_versioned | 是 | 2/3 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0128 | temporal_versioned | 是 | 3/3 / 2/2 / 0/2 | over_clarify | waiting_user |
| FP-BM-TV-0132 | temporal_versioned | 是 | 1/4 / 1/5 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0144 | temporal_versioned | 是 | 3/3 / 2/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category |
| FP-BM-TV-0148 | temporal_versioned | 是 | 1/3 / 1/1 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0152 | temporal_versioned | 是 | 3/3 / 1/1 / 1/2 | over_clarify | waiting_user |
| FP-BM-TV-0156 | temporal_versioned | 是 | 3/4 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0160 | temporal_versioned | 是 | 2/2 / 2/2 / 0/2 | over_clarify | waiting_user |
| FP-BM-TV-0164 | temporal_versioned | 是 | 3/4 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0004 | multi_source | 是 | 1/3 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0008 | multi_source | 是 | 2/5 / 1/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0012 | multi_source | 是 | 1/3 / 0/1 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0016 | multi_source | 是 | 4/5 / 1/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0020 | multi_source | 是 | 1/2 / 2/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0024 | multi_source | 是 | 3/4 / 0/2 / 0/2 | waiting_for_evidence | waiting_user,waiting_user,dev:max_clarifications_exceeded |
| FP-BM-MS-0032 | multi_source | 是 | 2/2 / 1/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0036 | multi_source | 是 | 2/2 / 1/1 / 0/2 | over_clarify | waiting_user |
| FP-BM-MS-0040 | multi_source | 是 | 3/3 / 0/1 / 1/2 | over_clarify | waiting_user |
| FP-BM-MS-0044 | multi_source | 是 | 2/4 / 1/1 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0048 | multi_source | 是 | 4/5 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0056 | multi_source | 是 | 6/6 / 2/2 / 1/2 | over_clarify | waiting_user |
| FP-BM-MS-0060 | multi_source | 是 | 1/5 / 2/4 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0064 | multi_source | 是 | 0/2 / 0/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0204 | tool_use_diagnostic | 是 | 2/2 / 1/1 / 0/2 | over_clarify | waiting_user |
| FP-BM-DG-0208 | tool_use_diagnostic | 是 | 2/2 / 0/1 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-DG-0212 | tool_use_diagnostic | 是 | 2/3 / 4/4 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0216 | tool_use_diagnostic | 是 | 3/3 / 5/5 / 1/2 | over_clarify | waiting_user |
| FP-BM-DG-0220 | tool_use_diagnostic | 是 | 2/3 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0224 | tool_use_diagnostic | 是 | 2/4 / 3/3 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0232 | tool_use_diagnostic | 是 | 1/3 / 0/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0240 | tool_use_diagnostic | 是 | 3/3 / 2/3 / 1/2 | over_clarify | waiting_user |
| FP-BM-DG-0244 | tool_use_diagnostic | 是 | 2/3 / 2/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0248 | tool_use_diagnostic | 是 | 2/3 / 3/4 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0252 | tool_use_diagnostic | 是 | 2/2 / 1/8 / 1/2 | over_clarify | waiting_user |
| FP-BM-DG-0256 | tool_use_diagnostic | 是 | 0/3 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0260 | tool_use_diagnostic | 是 | 0/2 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0264 | tool_use_diagnostic | 是 | 0/4 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |

### C 失败明细

| 题号 | 题型 | 多轮 | 证据(文档/记录/断言) | 主因 | 信号 |
| --- | --- | --- | --- | --- | --- |
| FP-BM-SR-0001 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0017 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0021 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0025 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0029 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0037 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0053 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-SR-0057 | simple_retrieval | 否 | 0/1 / 0/0 / 0/1 | evidence_miss | - |
| FP-BM-SR-0061 | simple_retrieval | 否 | 1/1 / 0/0 / 0/1 | conclusion_wrong | - |
| FP-BM-MD-0002 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0006 | multi_document | 否 | 1/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0010 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0014 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0018 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0022 | multi_document | 否 | 1/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0026 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0030 | multi_document | 否 | 0/2 / 0/0 / 1/2 | evidence_miss | - |
| FP-BM-MD-0034 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0042 | multi_document | 否 | 2/2 / 0/0 / 0/2 | conclusion_wrong | - |
| FP-BM-MD-0050 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0054 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0058 | multi_document | 否 | 0/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MD-0062 | multi_document | 否 | 1/2 / 0/0 / 0/2 | evidence_miss | - |
| FP-BM-MH-0003 | multi_hop | 是 | 0/2 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0011 | multi_hop | 是 | 1/2 / 0/1 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0015 | multi_hop | 是 | 1/2 / 1/2 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0019 | multi_hop | 是 | 2/2 / 2/3 / 0/2 | over_clarify | waiting_user |
| FP-BM-MH-0023 | multi_hop | 是 | 1/2 / 0/2 / 0/2 | simulated_user | waiting_user,waiting_user,waiting_user,dev:unexpected_category,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0031 | multi_hop | 是 | 2/2 / 1/2 / 0/2 | simulated_user | waiting_user,waiting_user,text,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0039 | multi_hop | 是 | 2/2 / 0/1 / 0/2 | simulated_user | waiting_user,waiting_user,text,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MH-0043 | multi_hop | 是 | 1/2 / 1/1 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0051 | multi_hop | 是 | 2/2 / 3/3 / 1/2 | over_clarify | waiting_user |
| FP-BM-MH-0055 | multi_hop | 是 | 1/2 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MH-0063 | multi_hop | 是 | 2/2 / 1/3 / 0/2 | simulated_user | waiting_user,text,waiting_user,dev:repeated_category,dev:unexpected_category |
| FP-BM-TV-0104 | temporal_versioned | 是 | 1/5 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0112 | temporal_versioned | 是 | 3/3 / 2/2 / 0/2 | over_clarify | waiting_user |
| FP-BM-TV-0116 | temporal_versioned | 是 | 2/5 / 1/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0120 | temporal_versioned | 是 | 2/3 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0124 | temporal_versioned | 是 | 3/3 / 2/2 / 0/2 | over_clarify | waiting_user |
| FP-BM-TV-0128 | temporal_versioned | 是 | 1/3 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0132 | temporal_versioned | 是 | 2/4 / 2/5 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0144 | temporal_versioned | 是 | 2/3 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0148 | temporal_versioned | 是 | 1/3 / 1/1 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0152 | temporal_versioned | 是 | 3/3 / 1/1 / 1/2 | over_clarify | waiting_user |
| FP-BM-TV-0156 | temporal_versioned | 是 | 1/4 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0160 | temporal_versioned | 是 | 1/2 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-TV-0164 | temporal_versioned | 是 | 3/4 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0004 | multi_source | 是 | 2/3 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0008 | multi_source | 是 | 1/5 / 1/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0012 | multi_source | 是 | 1/3 / 1/1 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0016 | multi_source | 是 | 2/5 / 0/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0020 | multi_source | 是 | 2/2 / 2/3 / 0/2 | over_clarify | waiting_user |
| FP-BM-MS-0024 | multi_source | 是 | 2/4 / 0/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0028 | multi_source | 是 | 2/3 / 2/2 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0032 | multi_source | 是 | 2/2 / 2/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:repeated_category,dev:unexpected_category,dev:max_clarifications_exceeded |
| FP-BM-MS-0036 | multi_source | 是 | 2/2 / 1/1 / 0/2 | over_clarify | waiting_user |
| FP-BM-MS-0040 | multi_source | 是 | 3/3 / 0/1 / 0/2 | over_clarify | waiting_user |
| FP-BM-MS-0044 | multi_source | 是 | 1/4 / 1/1 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0056 | multi_source | 是 | 4/6 / 2/2 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-MS-0064 | multi_source | 是 | 1/2 / 1/3 / 0/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0204 | tool_use_diagnostic | 是 | 2/2 / 1/1 / 0/2 | over_clarify | waiting_user |
| FP-BM-DG-0208 | tool_use_diagnostic | 是 | 0/2 / 0/1 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0212 | tool_use_diagnostic | 是 | 2/3 / 4/4 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0216 | tool_use_diagnostic | 是 | 3/3 / 0/5 / 0/2 | over_clarify | waiting_user |
| FP-BM-DG-0224 | tool_use_diagnostic | 是 | 2/4 / 3/3 / 1/2 | waiting_for_evidence | waiting_user |
| FP-BM-DG-0232 | tool_use_diagnostic | 是 | 2/3 / 1/2 / 0/2 | simulated_user | waiting_user,waiting_user,dev:unexpected_category |
| FP-BM-DG-0236 | tool_use_diagnostic | 是 | 2/2 / 1/2 / 0/2 | over_clarify | waiting_user |
| FP-BM-DG-0244 | tool_use_diagnostic | 是 | 3/3 / 2/3 / 0/2 | over_clarify | waiting_user |
| FP-BM-DG-0248 | tool_use_diagnostic | 是 | 0/3 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0252 | tool_use_diagnostic | 是 | 0/2 / 0/8 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0256 | tool_use_diagnostic | 是 | 0/3 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0260 | tool_use_diagnostic | 是 | 0/2 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |
| FP-BM-DG-0264 | tool_use_diagnostic | 是 | 0/4 / 0/4 / 0/2 | evidence_miss | dev:answered_without_clarification |

## 臂间差异

- C 相对 A 由失败转通过：FP-BM-SR-0005（simple_retrieval）; FP-BM-SR-0013（simple_retrieval）; FP-BM-SR-0041（simple_retrieval）; FP-BM-MD-0038（multi_document）; FP-BM-MD-0046（multi_document）; FP-BM-MH-0027（multi_hop）; FP-BM-MH-0059（multi_hop）; FP-BM-MS-0048（multi_source）; FP-BM-MS-0060（multi_source）; FP-BM-DG-0220（tool_use_diagnostic）; FP-BM-DG-0240（tool_use_diagnostic）
- C 相对 A 由通过转失败：FP-BM-MD-0022（multi_document）; FP-BM-MD-0050（multi_document）; FP-BM-MH-0051（multi_hop）; FP-BM-MH-0055（multi_hop）; FP-BM-MS-0028（multi_source）; FP-BM-DG-0236（tool_use_diagnostic）
