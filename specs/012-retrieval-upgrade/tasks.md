# 任务：FlowPay 检索链路优化（变体改写 + 精排对照 + ID 关键词路）

**输入**：[规格](spec.md)、[计划](plan.md)、[数据模型](data-model.md)、[接口契约](contracts/retrieval-variants.md)、[快速验收](quickstart.md)
**执行规则**：先记录基线；每条实现切片先写可证伪的失败测试，再实现并验证；实验达标才实施，未达标的出口是「报告 + 触发条件留档」，不是跳过或凑指标。

## Phase 1 — 实施前基线

- [ ] T001 按 `Makefile` 记录当前全量测试基线与 ruff/basedpyright 状态（当前容器快照 755 passed/5 skipped、失败基线为空，以当次记录为准）；确认 `core/knowledge`、`core/execution`、`scripts/benchmark` 现有测试全绿。基线有失败先厘清，不作滚动放行。

## Phase 2 — US1：离线变体实验与 top20 存档（P1）

**独立验收**：同一命令复跑产出同结构报告；变体口径差值与判定线可见；存档 JSONL 可独立消费，复跑不重复调模型。

- [ ] T002 [US1] 在 `backend/tests/scripts/test_retrieval_variants.py` 写红测试：`--variants` 口径构造（原句 + 变体文本多路检索的离线镜像）、变体存档行 schema（`question_id/question_type/dialect/path/query/variants/ranked`，`ranked` 含 `passage_id/doc_id/chunk_no/score/text`）、`--top-n` 深度、复跑读存档零模型调用、无 `--variants` 时现有 CLI 行为逐字不变。
- [ ] T003 [US1] 在 `scripts/benchmark/retrieval.py` 实现变体口径：按 [research.md](research.md) D3 协议调现有模型配置生成 ≤3 条变体（JSON 输出、与原句空白归一化去重、解析失败该题回退 initial）、变体路纯向量检索、`--archive`/`--top-n` 落盘；报告沿用现有口径差值框架并输出判定行（≥+10pp 且单轮不降）。通过 T002。
- [ ] T004 [US1] 用旧 96 题库实跑①实验，报告与存档写 `var/benchmark/`；**出口判据**：变体 ≥+10pp 且单轮不降 → 进 Phase 3；未达标 → 结论与触发条件留档进 spec，Phase 3/5 中产品实施部分不启动（评测工件已完成）。

## Phase 3 — US2：变体多路检索上线（P1，条件：①达标）

**独立验收**：无变体生成器时行为与现状逐字一致；变体失败回退零错误输出；融合条数与单路一致且向量路名额保护不破。

- [ ] T005 [US2] 在 `backend/tests/core/test_knowledge_variants.py` 写 `search_variants` 红测试：批嵌入一次调用覆盖原句+全部变体；原句走完整混合检索、变体走纯向量检索；RRF 融合去重后条数与单路 `limit` 一致；向量路已命中片段不被挤出名额；`filters` 每路一致；变体数为 0 时等价现有 `search`。
- [ ] T006 [US2] 在 `backend/src/flowpay/core/knowledge/client.py` 实现 `search_variants(texts, *, limit, filters)`（批嵌入 + 逐路检索 + `reciprocal_rank_fusion`，按 [contracts](contracts/retrieval-variants.md) §1）。通过 T005。
- [ ] T007 [US2] 在 `backend/tests/core/test_runtime_variants.py` 写 runtime 编排红测试：未注入生成器时 `search_knowledge` 逐字现状（含调用参数）；注入后生成成功走 `search_variants`；生成失败/超时/空数组/不可解析回退 `search` 且零额外错误输出；回退落 tracer 说明事件（非 error 级）。
- [ ] T008 [US2] 在 `backend/src/flowpay/core/execution/runtime.py` 实现变体生成器可选注入与编排（contracts §2）；在 `backend/src/flowpay/core/llm/`（或既有装配链最内聚处）实现 `VariantRewriter`：复用现有 ChatModel 配置、JSON 数组解析、失败交调用方回退（contracts §3）。通过 T007。
- [ ] T009 [US2] 在 `backend/src/flowpay/bootstrap.py` 装配链注入生成器（配置开关缺省关闭，开启即全出口生效）；跑全量测试与原套件回归（011 SC-005 口径）。任何出口行为劣化即回退本切片。

## Phase 4 — US3：ID 对照实验与条件实施（P1）

**独立验收**：报告证明或证伪现有关键词路对含编号查询的命中；实施或触发条件二选一落档。

- [ ] T010 [US3] 在 `backend/tests/scripts/test_retrieval_id_probe.py` 写红测试：`--id-probe` 口径（真实记录编号拼进查询）× 两条路（现有关键词路 / 逐 ID 对照路）的检索与 recall@k 计算；报告含两路对比与判定行（≥+10pp）；无 `--id-probe` 时现有 CLI 逐字不变。
- [ ] T011 [US3] 在 `scripts/benchmark/retrieval.py` 实现 `--id-probe`（编号抽取复用 `record_ids()`，逐 ID 对照路只做评测不进产品）；用旧 96 题库实跑③实验出报告。
- [ ] T012 [US3] **条件实施**：ID 口径 ≥+10pp → 在 `backend/tests/core/test_keyword_id_path.py` 先写红测试（含编号查询命中含该编号片段、编号不命中时不引入噪声候选、阈值与 limit 语义不变），再在 `backend/src/flowpay/core/knowledge/store.py` 实施逐 ID ILIKE 并集（contracts §4，不新增分词器依赖）；未达标 → 在 spec 澄清记录追加触发条件（复测信号 + 口径 + 判定线）。

## Phase 5 — US4：精排对照实验（P2）

**独立验收**：报告基于变体口径存档离线重排，不重跑检索；未达标结论为「维持现状 + 触发条件」，不虚报上限。

- [ ] T013 [US4] 在 `backend/tests/scripts/test_rerank_offline.py` 写红测试（fake reranker）：存档逐题消费与保序、重排前后 recall@k 计算、逐题型分解、延迟分布统计（P50）、判定行（≥+5pp 且 P50 ≤500ms）、存档缺失/畸形行报错不静默。
- [ ] T014 [US4] 新建 `scripts/benchmark/rerank_offline.py`：先以最小请求验证同栈 rerank API 形状（[research.md](research.md) D5），再实现存档消费 → 重排 → 报告（contracts §5）。通过 T013。
- [ ] T015 [US4] 用④阶段实跑精排对照出报告；**出口判据**：达标 → 写实施决策记录（组件、依赖清单、挂载点 `search_variants` 尾部），实施另立任务；未达标 → 报告留档 + 触发条件写进 spec。

## Phase 6 — US5：FAQ 补测与端到端对比（P2，依赖外部就绪）

**独立验收**：FAQ 与旧题库结论并列、版本标注清晰；011 复跑对比区分检索增益与模型波动，异常题单列不计成绩。

- [ ] T016 [US5] FAQ dev 40 题与 FAQ collection 就绪后：用 `--config` 指向 FAQ 集合复跑①③④三组对照，结论与旧题库并列写入报告（题库版本 + 检索配置版本标注）。
- [ ] T017 [US5] 011 正式基线（60 题 test）就绪且 Phase 3（及达标的 Phase 4/5 实施）合入后：用 011 runner 同题库复跑，产出对比报告标注两侧检索配置版本；未运行或异常题单列，不冒充成绩。

## Phase 7 — 交付校验

- [ ] T018 按 `Makefile` 跑全量测试与质量门禁；核对 011 的 FR 条款、题库与判分逻辑零改动（diff 审计）；核对所有未达标项均有「报告 + 触发条件」留档，无死条款（SC-003/SC-004）。
- [ ] T019 在 `specs/012-retrieval-upgrade/delivery-report.md` 记录：各实验判定结果、实施/未实施决策及依据、版本与配置标注、已知缺口。报告结论如实，不按达标率放行。

## 依赖与并行

- 主线：T001 → Phase 2（T002→T003→T004）→ Phase 3（T005→T006、T007→T008、T009）→ Phase 7。Phase 4 的 T010–T011 与 Phase 3 无共享文件，可在①实验完成后并行；T012 等待 T011 数据。
- Phase 5 的 T013–T014 可与 Phase 3 并行（只依赖①存档）；T015 等待④数据。
- Phase 6：T016 依赖 FAQ 题库与 collection 就绪（011 侧）；T017 依赖 011 正式基线与产品改造合入，两者可并行。
- 条件出口：T004/T012/T015 任一未达标，其下游实施任务不启动，评测与留档任务照常完成。

## 实施策略

- MVP = Phase 1 + Phase 2（离线实验先行，零产品代码风险）；US2 实施是第一个产品切片，T009 的全量回归是合入闸门。
- 每个条件任务以数据说话：达标实施、未达标留档，两个出口都算完成。
