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

- [ ] T010 [US2] 冒烟：容器内 `FLOWPAY_MODEL_PROTOCOL=responses` + `reasoning_effort=medium` 跑 `--per-type 1`（6 题）；验 run.failed=0、链路事件正常；实测单题耗时/token，外推 96 题预算，报告给 Ban（超外推 2 倍停手条款生效前置）
- [ ] T011 [US2] 推理臂 96 题：`--per-type 16 --governance --hybrid` + protocol/reasoning 环境变量；产出目录记录
- [ ] T012 [US2] 机制门核对（SC-1）：`clarification.py --arm R96=<新目录>`；覆盖 ≥90%、重复问 0、等待轮带结论 100%——任一不达标排查后停手
- [ ] T013 [US2] A/B 对照报告（SC-2/SC-4）：四项指标 vs 基线子集；逐题翻盘/回退清单（基线 110 题证据齐断言零命中里的翻盘数是核心数字）；用量与耗时倍数；写 `specs/008-responses-reasoning/ab-report.md`
- [ ] T014 [US2] 验收 US2：向 Ban 提交 A/B 报告与验收结论

## Phase 4：US3（P1）混合检索收口

- [ ] T015 [P] [US3] `backend/configs/local.toml`：`knowledge_hybrid = false` + 注释引用两份全量对照报告路径与复开方法
- [ ] T016 [US3] 验收 US3（SC-5）：默认配置下 `retrieval.py --per-type 16` 抽查，与 bge-m3 基线纯向量口径一致（混合关闭后行为不变）；记录抽查结果

## Phase 5：US4（P1）数据集缺口清单

- [ ] T017 [US4] `specs/008-responses-reasoning/dataset-gap-kb-api-017.md`：从 FULL300 逐题 `required_documents` 反查引用 KB-API-017 的题目清单；缺失编号、建议知识域与要素、验证步骤（重建后检索诊断复查）（SC-6）
- [ ] T018 [US4] 验收 US4：清单评审（Ban 过目），确认可执行

## Phase 6：Polish & 收尾

- [ ] T019 质量门禁：容器 pytest 失败集合为空；ruff check/format、basedpyright 全绿
- [ ] T020 交付报告：`specs/008-responses-reasoning/delivery-report.md`——SC-1~SC-7 逐项、A/B 结论、文件清单、遗留风险（含 encrypted_content 回退行为观察、Responses SDK 事件面实测备注）；quickstart.md 补实际命令与结果；不 commit/push

## 依赖与并行

- T003/T004/T005/T007 可并行（不同文件）；T006 依赖 T005；T008 依赖 T003–T007。
- US2（T010–T014）依赖 US1 全部完成；T010 冒烟通过后才进 T011（费控门）。
- US3/US4（T015–T018）独立，可与 US2 并行。
- MVP 范围：T001–T009（US1 适配器可独立交付）。
