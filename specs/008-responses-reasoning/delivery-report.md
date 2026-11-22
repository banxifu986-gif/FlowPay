# 交付报告：008 推理能力验证与检索收尾

状态：2026-11-21 完成。需求与验收口径唯一来源见同目录 spec.md；A/B 数据详见 ab-report.md。

## 1. 验收总表（SC-1 ~ SC-7）

| 项 | 门槛 | 实测 | 结论 |
|---|---|---|---|
| SC-1 机制门防回归 | 覆盖 ≥90%、重复问 0、带结论 100% | **53/54=98.1%、0、93/93=100%** | ✅ |
| SC-2 推理臂端到端 | 总 ≥23/96、多轮 ≥17/64 | 总 19/96（+6）、多轮 3/64（-1） | ❌ 未达门槛（Ban 拍板接受结论） |
| SC-3 适配层一致性 | 两协议行为 100% 兼容 | 19 专项用例；下游（编造核对/用量/失败分类/澄清事件）零改动 | ✅ |
| SC-4 A/B 翻盘量化 | 翻盘数/回退数/新增证据 | 翻盘 10、回退 4、**核心 28 题证据齐断言零命中翻盘 1** | ✅ 已量化 |
| SC-5 混合检索收口 | 默认关闭后行为一致 | 默认 false；纯向量路径与既有基线一致（532 tests 无回归） | ✅ |
| SC-6 KB-API-017 清单 | 清单完成含验证步骤 | **已落盘并修正归因**（见下） | ✅ |
| SC-7 质量门禁 | make quality 全绿 | 532 passed / 0F；ruff / basedpyright 全绿 | ✅ |

**SC-2 未达门槛的处置**：Ban 拍板（2026-11-21）"接受结论、关闭假设"——SC-2 作为方向性结论记录，不继续追档位。核心发现：**多轮"最后一跳"瓶颈不在有无推理步骤**（medium 档 28 题只翻 1），推理的边际收益集中在单轮综合（9→16，multi_document 0→6）。

## 2. 各 US 实现摘要

**US1 推理模型可用**（T003–T009，提交 `d0de4ac`/`0d7b77c`）
- `core/llm/chat/openai_responses.py`（新增）：`OpenAiResponsesModel`——input items 完整重放翻译（tool_calls→function_call、tool 结果→function_call_output、reasoning_items 按序回插）、reasoning_effort 白名单（low/medium/high，走 request_options 通道）、连接失败一次重试、HTTP 错误转 ModelHTTPError。
- `core/llm/chat/responses_stream.py`（新增）：事件白名单聚合器——文本增量、tool_calls 按 item 累积、reasoning 原文保留（供下一轮重放）、usage 映射（input/output/total→prompt/completion/total）、completed/incomplete 终态、未知事件不阻断、无完成事件拒绝成功。
- `settings.py`/`loader.py`：`ModelSettings.protocol`（chat_completions 默认 / responses），`FLOWPAY_MODEL_PROTOCOL` 环境覆盖，非法值启动即败。
- `bootstrap.py`：按 protocol 一行分流，**workflows 层零改动**（FR-4）。
- 附带收口：`KnowledgeWriter` 协议（继承 VectorStore）补齐，Pipeline 读写参数类型收口（清偿 97d6c27 遗留类型债）。

**US2 推理增益 A/B**（T010–T014，提交 `490f1f7`/`79d43ff`/`e278fd1`/`04b2b72`）
- 冒烟（6 题）：run.failed=0，链路全绿；外推预算 8.6M prompt/0.96M completion，prompt 1.4x / completion 3.9x vs 无推理。
- R96 推理臂（96 题）：总 19/96（+6）、单轮 16/32（+7）、多轮 3/64（-1）；用量 5.63M/0.64M 低于外推；耗时 ~2.5h。
- 逐题对照：翻盘 10（单轮 8+多轮 2）/回退 4（模型随机波动）；**28 题证据齐断言零命中翻盘 1**——多轮假设证伪。
- 报表配置行新增 `协议=...；推理=...` 标注，臂间对照可读。

**US3 混合检索收口**（T015–T016，提交 `ae8b990`）
- `local.toml`：`knowledge_hybrid = false`，注释记录 qwen3/bge-m3 两空间全量评测逐格相同的证据与复开方法；532 tests 无回归。

**US4 数据集缺口**（T017–T018，提交 `ae8b990`）
- `dataset-gap-kb-api-017.md` 落盘，**修正 007 交付报告的归因**：文件存在、发布清单已收录、qwen3/bge-m3 两集合均已入库（各 6 分块）；真实问题是 qwen3 空间召回排序不足（bge-m3 正常），非数据集缺文件；引用题 3 道；**无需数据集侧行动**。

## 3. 运行与用量

| 臂 | 用途 | 调用 | prompt | completion | run.failed | 耗时 |
|---|---|---|---|---|---|---|
| 冒烟 6 题（20261121-175753） | 链路验证 | 136 | 0.47M | 52K | 0 | ~20min |
| R96 推理臂（20261121-182447） | A/B 验收 | 2063 | 5.63M | 0.64M | 0 | ~2.5h |

费控全程无超支：冒烟外推 → 实际低于外推（实际轮次更少）。

## 4. 遗留与观察项

1. **Responses SDK 流式事件面**：适配器按事件类型白名单处理，未知事件落 wire 帧不阻断；供应商新增事件类型时无需改代码，但聚合完整性依赖官方事件契约稳定。
2. **encrypted_content 跨轮重放**：本轮回退路径（供应商拒绝时丢弃推理项重试一次）未在真实 API 上触发（gpt-6-luna 接受回插），该分支由单测覆盖。
3. **多轮断言表述瓶颈的根因**：medium 档推理未改善；下一步若重启该方向，候选是 high 档单题型试点或换模型家族对照——本次范围内已关闭。
4. **混合检索开关**：默认已关；更换语料或分块策略后可手动开启复评。
5. **007 交付报告勘误**：第 6 节"KB-API-017 不在知识集合"表述不准确，以 `dataset-gap-kb-api-017.md` 为准（qwen3 空间召回不足，非缺文件）。

## 5. 归档

- R96 臂：`var/benchmark/20261121-182447-abfb66/`（事件库在命名卷，轻量 jsonl+报表在宿主目录）
- 报告：`var/benchmark/20261121-clarification-r96.md`（机制门）；`specs/008-responses-reasoning/ab-report.md`（A/B）
- 冒烟臂：`20261121-175753-dca0ce`（卷内已清理，数据已入交付记录）
- git 提交链：`d0de4ac` → `0d7b77c` → `490f1f7` → `79d43ff` → `e278fd1` → `04b2b72` → `ae8b990`（未 push）
