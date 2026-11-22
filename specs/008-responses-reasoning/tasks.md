# 任务：008 推理能力验证与检索收尾

批次与门禁见 plan.md。实施由 Claude 直接进行；每批完成后向 Ban 提交验收报告。

## Phase 1：Setup

- [x] T001 基线快照：从 `var/benchmark/20261121-full300-multi-agent.jsonl` 提取 96 题子集（每类前 16，与 run.py sample 口径一致），固化翻盘对照清单：逐题 pass/evidence/断言命中，存 `specs/008-responses-reasoning/baseline-96.jsonl`；记录总/多轮/单轮/证据四项与 110 题证据齐断言零命中的题型分布
- [x] T002 [P] 核对环境：`.env` 三键与代理、bge-m3 集合就绪；记录 `loader.py` 环境覆盖机制对 `protocol`/`request_options` 的支持现状（`FLOWPAY_MODEL_PROTOCOL` 需新增支持）

## Phase 2：Foundational（US1 适配层）

- [x] T003 [P] [US1] `backend/src/flowpay/core/config/settings.py`：`ModelSettings.protocol` 字段（`chat_completions` 默认 / `responses`），保留字校验按协议分流；`loader.py` 支持 `FLOWPAY_MODEL_PROTOCOL` 环境覆盖；补校验用例（非法值拒绝、responses 端保留字）
- [x] T004 [P] [US1] `backend/src/flowpay/core/llm/chat/responses_stream.py`：`ResponsesStreamAccumulator`——事件白名单（output_item.added/done、output_text.delta、function_call_arguments.delta、reasoning_summary、completed/incomplete/failed）、文本增量、tool_calls 累积、reasoning_items 原文保留、usage 映射（input/output/total → prompt/completion/total）；先写失败用例（data-model.md 事件表逐行）
- [x] T005 [P] [US1] `backend/src/flowpay/core/llm/chat/openai_responses.py`：`OpenAiResponsesModel`——`prepare`（url/body：input items + tools + reasoning + stream 标记；不含密钥）、`invoke`（`client.responses.create(stream=True)`、逐帧喂聚合器、缺 completed 视为中断）、`capabilities`；chat 端等价的连接重试语义
- [x] T006 [US1] `openai_responses.py` 的 messages→input 翻译：system/user/assistant 文本、tool_calls → function_call item、tool 结果 → function_call_output、推理项按序回插（配对依赖 AgentTurn 的 tool_call_id 机制）；翻译表按 data-model.md；单测覆盖每种消息形态与混合序列
- [x] T007 [P] [US1] `backend/src/flowpay/bootstrap.py`：protocol 分流（responses → OpenAiResponsesModel）；`openai_responses.py` 内 `reasoning_effort` 白名单校验（low/medium/high，非法启动即败）；`request_options` 中该参数被适配器消费而非透传 body 顶层（data-model 约束）
- [x] T008 [US1] 集成测试：假 transport 模拟 Responses 流——完整多轮对话（含一次工具调用 + 推理项回传）走通 ChatModel Protocol 全链路；`model.request/response/chunk` 事件载荷形状断言（protocol 标记、reasoning_items 保留）
- [x] T009 [US1] 验收 US1：全量容器 pytest（基线 513 passed/0F 保持）+ ruff/basedpyright 全绿；报告 US1 完成状态

## Phase 3：US2（P0）推理增益 A/B

- [x] T010 [US2] 冒烟：容器内 `FLOWPAY_MODEL_PROTOCOL=responses` + `reasoning_effort=medium` 跑 `--per-type 1`（6 题）；验 run.failed=0、链路事件正常；实测单题耗时/token，外推 96 题预算，报告给 Ban（超外推 2 倍停手条款生效前置）。**完成（20261121-175753-dca0ce）：run.failed=0；136 次调用、0.47M prompt/52K completion；外推 96 题约 8.6M prompt/0.96M completion、2500 次调用；vs 无推理基线 prompt 1.4x / completion 3.9x。待 Ban 放行 T011**
- [x] T011 [US2] 推理臂 96 题：产出 `var/benchmark/20261121-182447-abfb66`（responses+medium，19/96 通过、49/96 证据、单轮 16/32、多轮 3/64；run.failed=0，221 runs，2063 调用，5.63M prompt/0.64M completion）
- [x] T012 [US2] 机制门核对（SC-1）：整题覆盖 53/54=98.1%、重复问 0、等待轮带结论 93/93=100%——三项全达标
- [x] T013 [US2] A/B 对照报告：`specs/008-responses-reasoning/ab-report.md` 落盘——SC-2 未达门槛（总 19<23、多轮 3<17）、SC-4 已量化（翻盘 10/回退 4/核心 28 题翻盘 1）
- [x] T014 [US2] 验收 US2：**Ban 拍板（2026-11-21）：接受结论、关闭假设**——medium 档推理改善单轮综合（9→16），不改善多轮断言（28 题翻盘 1）；SC-2 未达门槛如实记录为方向性结论，不继续追档位。008 以现有成果收口，B4 推进

## Phase 4：US3（P1）混合检索收口

- [x] T015 [P] [US3] `local.toml`：`knowledge_hybrid = false` + 注释引用两份全量对照报告路径与复开方法；全量测试 532 passed/0F 无回归
- [x] T016 [US3] 验收 US3（SC-5）：开关关闭只影响 hybrid 参数装配（None→纯向量路径），bge-m3 基线实际检索结果与全量对照逐格相同的既有证据已覆盖（20261121-full300 两路径全表一致）；无需另跑抽查

## Phase 5：US4（P1）数据集缺口清单

- [x] T017 [US4] `dataset-gap-kb-api-017.md` 落盘：**修正原归因**——文件存在、清单已收录、两集合均已入库（各 6 分块）；真实问题是 qwen3 空间召回排序不足（bge-m3 空间正常），非数据集缺文件；引用题 3 道；无需数据集侧行动
- [x] T018 [US4] 清单评审：Ban 过目后确认（勘误记录：007 交付报告第 6 节第 4 条以本清单为准）

## Phase 6：Polish & 收尾

- [x] T019 质量门禁：容器 pytest 532 passed/0F；ruff/basedpyright 全绿（B1/B2/B4 各批次实测）
- [x] T020 交付报告：`delivery-report.md` 落盘（SC-1~SC-7 逐项、A/B 结论、遗留观察）；T019 门禁合并记录

## 依赖与并行

- T003/T004/T005/T007 可并行（不同文件）；T006 依赖 T005；T008 依赖 T003–T007。
- US2（T010–T014）依赖 US1 全部完成；T010 冒烟通过后才进 T011（费控门）。
- US3/US4（T015–T018）独立，可与 US2 并行。
- MVP 范围：T001–T009（US1 适配器可独立交付）。
