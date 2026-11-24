# 任务：FlowPay 大众版 FAQ 基准与本地演示

**输入**：[规格](spec.md)、[计划](plan.md)、[数据模型](data-model.md)、[套件契约](contracts/faq-suite.md)、[快速验收](quickstart.md)  
**执行规则**：先记录基线；每条实现切片先写可证伪的失败测试，再实现并验证。不要修改原数据集，不覆盖现有发布物，不在实现前动 010 的未提交工作。

## Phase 1 — 实施前基线

- [ ] T001 在独立实现工作区按 `Makefile` 执行现有测试／校验，记录 `backend/tests/scripts/test_benchmark_harness.py`、`backend/tests/core/test_data_boundary.py` 及现有摄入测试的结果；核对原发布与 collection 不会被写入。基线有失败先厘清，不作滚动放行。
- [ ] T002 在 `backend/tests/fixtures/faq_suite/` 定义隔离的最小夹具：两名付款人、相似订单及退款、一个主题两版本、六主类题的代表样例。用临时目录构建快照，不依赖旧数据、凭据或真实模型。夹具不作为官方题库。

## Phase 2 — 共用本人取证能力

- [ ] T003 在 `backend/tests/core/test_faq_data_boundary.py` 先写红测试：两个身份均可查本人；他人和不存在的交易／退款同为空；缺范围或传入覆盖参数不会取得数据；同订单号和错误退款归属的样例可证伪测试。
- [ ] T004 实现 `flowpay-faq-dataset/seed/` 中的付款人与付款／退款合成源形状；在 `scripts/faq.py` 生成只读 canonical SQLite。付款编号唯一、时间带时区、金额用最小货币单位；退款必须关联同快照付款，归属继承付款人，`settled_at` 可空。先生成夹具，再补正式记录。
- [ ] T005 在 `flowpay-faq-dataset/seed/` 定义最小只读来源策略，复用 `backend/src/flowpay/core/data_sources/` 的登记查询；付款和退款均从服务端注入范围，持有人字段不属于可选过滤项。验证 T003 通过，旧来源策略测试通过。

## Phase 3 — US1：独立发布与加载（P1）

**独立验收**：通过夹具测试冻结、校验与新 collection 构建，不套用正式题量配额；错误 release 不能启动，运行视图不含题库。

- [X] T006 [P] [US1] 在 `backend/tests/scripts/test_faq_release.py` 写 `freeze` / `verify` 红测试：新目录写入、四分组边界、规范化路径、字节数与 SHA-256、篡改或缺文件拒绝、拒绝覆盖旧目录和同 ID 冻结漂移。
- [X] T007 [P] [US1] 在 `backend/tests/scripts/test_faq_corpus.py` 写 FAQ `build` 红测试：只构建 FAQ collection；不接受内部或未登记文档、原 collection、缺 canonical 业务清单及向量空间错配；文档编号唯一、同篇问答标题唯一、历史关系无环且 `[起, 止)` 无重叠。
- [X] T008 [US1] 在 `scripts/faq.py` 和 `scripts/faq_release.py` 实现同版发布冻结、显式根加载与运行视图；固定 release 摘要与 canonical 业务摘要分别核对，运行视图不拷贝 dev/test 文件。通过 T006。
- [X] T009 [US1] 在 `scripts/ingestion/runner.py` 与 `scripts/faq.py` 新增 FAQ `build` 的显式根和 FAQ 验收配置；保留默认知识准备方式，沿用现有 ingest builder 和来源哈希，只有 FAQ 组分块、嵌入，派生物仅写新目录及目标 collection。通过 T007 及旧摄入回归。

## Phase 4 — US2：真实链路与逐断言评分（P1）

**独立验收**：六类夹具按真实会话契约取证，已发布回答的正例通过，漏证据、错绑、错版本、禁止断言和行为违例均被判出，计分异常不通过。

- [ ] T010 [P] [US2] 在 `backend/tests/workflows/test_faq_workflow.py` 编写注入式红测试：FAQ 只注册必要工具、查到本人付款与退款、缺记录关键信息则澄清、以已验证范围取证，作答能发布 FAQ 小节及相应业务记录编号。断言真实的工具事件与发布消息，不能借原客服兜底空跑通过。
- [ ] T011 [P] [US2] 在 `backend/tests/scripts/test_faq_scoring.py` 写判分反例：命中未引用、引用错误题面编号、证据错绑／字段错／过时版本、禁止结论、提前回答／复问、多跳未传递中间值和未完成多轮；并覆盖语义等价正例、可追溯前轮证据及评分故障。
- [ ] T012 [P] [US2] 在 `backend/tests/scripts/test_faq_benchmark.py` 写 runner 红测试：显式 FAQ split 与受限身份、不向助手泄露 gold 或用例名、test 禁抽样、异常逐题记账；无 FAQ 参数时原 CLI 与 score 不变。
- [ ] T013 [US2] 在 `backend/src/flowpay/bootstrap.py` 和 `backend/src/flowpay/workflows/multi_agent/` 将 FAQ 付款／退款工具及付款用户口径从依赖装配注入既有阶段；不要在工作流实现 SQL，不复制原阶段。按 T010 验证发布引用与历史口径可追溯。
- [ ] T014 [US2] 在 `scripts/benchmark/faq.py` 加载已校验 `Question` / `Rubric`，按 `data-model.md` 校验主类、难度、身份、每题唯一金标、来源与版本；评测器与模拟用户只给会话传入该轮用户可见的信息。
- [ ] T015 [US2] 在 `scripts/benchmark/faq_scoring.py` 实现文档／记录出处、字段与版本复核，独立验证澄清类别、依赖步骤及跨轮完成条件。用现有模型客户端做逐断言语义判定并校验回答原文位置；无结构化判定或调用失败记 `scoring_error`，不以旧词面评分兜底。通过 T011。
- [ ] T016 [US2] 在 `scripts/benchmark/run.py` 接入 FAQ 显式 split、受限主体、逐题独立会话和渐进披露；FAQ 独立报告，带 release、双摘要、collection、执行／评分配置及四类终态，保留原 suite 的默认入参与输出。通过 T012。
- [X] T017 [US2] 在 `backend/tests/scripts/test_faq_content.py` 先写官方内容校验：100 题、40／60、规格中的逐类计数、难度 `30/50/20`（dev `12/20/8`、test `18/30/12`）、各类皆有三档；共享记录、改写和脚本变体相连时同组；证据不得悬空或与快照、时间版本矛盾。
- [X] T018 [P] [US2] 写 `flowpay-faq-dataset/knowledge/` 的约 20 篇公开 Markdown，每篇目标 4–6 问答；主题覆盖付款失败／处理中、扣款不一致、重复扣款、退款和订单核对。`doc_id` 用 `KB-FAQ-<主题>-<版本>`；历史规则的生效边界写明，标注不放入文档。
- [X] T019 [P] [US2] 在 `flowpay-faq-dataset/seed/` 充实付款／退款场景与跨身份干扰记录；金额、状态、发生时间及退款到账时间与内容口径一致。只写合成源数据，不提交生成的数据库。
- [X] T020 [US2] 在 `flowpay-faq-dataset/benchmark/` 编写 100 道自然中文题及独立金标，覆盖现行与历史、真实依赖多跳、20 道渐进披露对话。dev/test 以组切分；人工复核题面没有泄题、语义等价可通过、记录字段确能支持断言。在 `scripts/faq.py` 接入 T017 的完整制包门禁，冻结初版并进行独立摄入。
  - `flowpay-faq-v1` 复冻摘要一致；正式包已通过 `freeze` / `verify` 的内容门禁。断网使用本地嵌入模型和独立 pgvector 构建 `faq_flowpay-faq-v1`：20 篇已登记公开 FAQ、120 个 1024 维向量，集合为 `ready`。只读复核了清单及双摘要，并实测重复扣款、余额退款新旧版本的检索和引用回查。构建报告在 `var/faq/build/run-ax4f1g1z/report.json`；此验收不等同于正式 Agent 评测。

## Phase 5 — US3：本地 Web 演示（P2）

**独立验收**：现有页面连 FAQ 本地后端，两名模拟付款人登录，在各自会话看到真实证据且查询他人得不到记录。原默认入口不变。

- [ ] T021 [US3] 在 `backend/tests/scripts/test_faq_demo.py` 先写运行和 HTTP/SSE 契约红测试：FAQ 拒绝内部或无绑定主体；只挂运行视图即可启动；A/B 会话和记录隔离；FAQ 不接入旧状态、旧 token 或原工具；默认启动和页面契约不变。
- [ ] T022 [US3] 在 `backend/src/flowpay/cli.py`、`backend/src/flowpay/bootstrap.py` 接入显式 FAQ 运行选项及启动校验：固定独立只读快照、collection、状态与本地端口，关闭跨会话记忆，绑定专用合成账号和密钥；运行时不挂载题库。通过 T021。
- [ ] T023 [US3] 在 `README.md` 补最短 FAQ 启动方式，以 `frontend/vite.config.ts` 已有代理指向独立回环端口；不新增页面或路由。按 `specs/011-faq-benchmark/quickstart.md` 用两个模拟账号做真实浏览器冒烟，记录引用、追问与边界结果。

## Phase 6 — 全链路交付校验

- [ ] T024 按 `Makefile` 运行既有项目质量门禁与新增测试；记录原套件回归、运行视图无评测内容及授权边界通过情况。任何失败或跳过注明证据，不算通过。
- [ ] T025 按 `specs/011-faq-benchmark/quickstart.md` 先跑真实 dev 抽样，复核评分结果，再冻结运行配置；对外部模型提交合成输入前确认许可。跑完 60 道 test，输出 `var/faq/` 本地逐题结果与分题型／难度汇总，在 `specs/011-faq-benchmark/delivery-report.md` 记录版本、完成数、通过数、异常与已知能力缺口。不按最低模型通过率放行，也不运行后再根据 test 调参。

## 依赖与并行

- 实施顺序：T001–T005 → US1 → US2 → US3 → 交付。US3 可在 US2 提供真实运行能力后与全量内容编制并行；浏览器实测与正式报告依赖 T020 冻结版。
- US1：T006／T007 可并行；两组测试先红，再分别做 T008／T009。
- US2：T010／T011／T012 可并行；T013 与 T014–T015 在不改同一文件时并行，T016 等待二者。T018／T019 可在夹具通过后并行，T020 等待 T017–T019。
- US3：T021 在真实运行能力就绪后先红，T022 再完成集成；T023 等待 T022 和发布版。T024、T025 收尾。
- MVP 先交 US1 的小样冻结、隔离及真实构建，作为实施检查点；完整交付仍须 US2、US3 和正式报告。

## 需求追踪

| 规格 | 任务 |
| --- | --- |
| FR-001、FR-002；SC-001 | T006–T009、T012、T021–T024 |
| FR-003、FR-004、FR-005；SC-002 | T007、T017–T020 |
| FR-006；SC-005 的归属边界 | T003–T005、T010、T021–T024 |
| FR-007、FR-008；SC-003 | T010–T020 |
| FR-009；SC-004 | T012、T016、T025 |
| FR-010；SC-005 的 Web 与旧入口 | T021–T024 |
