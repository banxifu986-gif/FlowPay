# 任务：010 逐断言校验

批次与门禁见 plan.md，决策依据见 research.md，实体形状见 data-model.md，判定契约见 contracts/verdict-schema.json，验证步骤见 quickstart.md。实施在新分支（`feature/010-assertion-verification`）双工作树并行；每批完成后向 Ban 提交验收报告；**T021/T022 属 96 题量级运行，Ban 批准后才执行**。

## Phase 1：Setup（B0 契约先行 + 切树）

- [ ] T000 提交门：将 `specs/010-assertion-verification/`（spec/plan/research/data-model/contracts/quickstart/checklists）、`backend/src/flowpay/core/verify/__init__.py` + `contracts.py`（纯类型骨架：Assertion/AssertionVerdict/AssertionAudit，与 verdict-schema.json 同构，零实现）、`.specify/feature.json` 提交入库——双工作树建立的前置门（worktree 不共享未跟踪文件；**需 Ban 复核工件后批准 commit**）
- [ ] T001 核对环境：`.env` 密钥就绪；确认 R96B2 验收臂事件库可读（`var/benchmark/20261122-200707-6010b8`：`multi-agent.jsonl` + `sessions.sqlite`）；确认 `scripts/benchmark/evidence.py` 证据 ID 对齐器与 `scripts/benchmark/attribution.py` 五类主因输出可复用；确认 `workflows/multi_agent/citation_check.py` 的 `has_citation`/`is_assertion`/`split_sentences`/`UNVERIFIED_PHRASES` 可直接复用
- [ ] T002 切双工作树：`Flow-010-capability`（树A，能力侧）+ `Flow-010-consumer`（树B，消费侧），切出点=T000 提交；各自 `uv sync` 建 venv（009 实测：Windows Scripts 布局，宿主 pytest 子集快跑，fcntl 容器-only 模块不在改动面）；副树禁 `make up`、基准臂只在主仓

## Phase 2：US1（P0）逐断言校验与处置——树A 能力侧

故事目标：给定"文本+本轮证据+用户问题"，产出四档判定与处置映射，任一环节失败 fail-open。独立测试判据：`tests -k verify` 全过（四档 fixture、ID 校验、子集校验、幂等、fail-open）。

- [ ] T003 [P] [US1] `backend/src/flowpay/core/verify/assembly.py`：证据装配——待校验文本 + 本轮用户问题 + 绑定引用片段原文 + 全轮片段（按 score 排序、字符封顶，默认 8000 由入参传入不读配置）+ 记录编号摘录；摘录缺失按证据缺失参与判定不阻塞；零新增检索调用；单测（装配输入逐项 + 摘录缺失不阻塞）
- [ ] T004 [P] [US1] `backend/src/flowpay/core/verify/extractor.py`：抽取调用——LLM 一次调用（JSON 输出 + 既有解析工具）产出原子断言清单（text/refs/record_ids）；无断言返回空清单；解析失败上抛由 T008 兜底；单测（清单解析、空清单、坏输出上抛）
- [ ] T005 [US1] `backend/src/flowpay/core/verify/verifier.py`：批量校验调用——LLM 一次调用逐断言判定四档（证据集=T004 装配结果）；**partial 必须附 overreach（dimension ∈ 对象/时间/数字/条件 + note）与 revision，缺失视同 insufficient**（spec FR-4 默认假设）；**contradicted 附 conflict（双方出处+冲突点）**；fixture 覆盖三类越界样例（适用对象扩大/"通常"→保证/"工作日"丢失）与矛盾样例；单测
- [ ] T006 [US1] `backend/src/flowpay/core/verify/actions.py`：处置映射纯函数——supported→keep；partial→replace（校验输出原句全文+revision 句级等值匹配替换，命中多处取第一处，匹配失败降级；**替换句数字/编号必须为原句 ∪ 绑定证据的子集，越界降级**）；insufficient→downgrade（"未能核实：原句"，幂等）；contradicted→downgrade+conflict 随事件携带；零新增模型调用；单测（替换成功/子集越界降级/匹配失败降级/幂等）
- [ ] T007 [P] [US1] `backend/src/flowpay/core/verify/errors.py` + fail-open 包装：调用失败/解析失败/装配异常/ID 校验异常 → 放行原文 + `fail_open=True` + actions 空，不抛出；单测（每类异常注入）
- [ ] T008 [US1] 验收 US1（树A）：`backend/.venv` 内 `pytest -k verify` 全过 + ruff/basedpyright 全绿；报告 US1 能力侧完成状态（不碰 workflows/scripts/config，文件面纪律自查）

## Phase 3：US2（P0）旁路回放与断点归因——树B 消费侧（对 fixture/桩开发）

故事目标：不重跑 agent，从 R96B2 事件库恢复草稿与证据，产出逐题判定 JSONL 与四类断点归类 + 误伤基线。独立测试判据：fixture 回放冒烟零 API 费用全过（SC-4 的机制预演）。

- [ ] T009 [P] [US2] `scripts/benchmark/assertion_audit.py`（新）回放驱动：从验收臂事件库恢复 synthesize 决策调用的**原始草稿**（model.response 载荷，retrieval.py 按 seq 读事件先例）+ 本轮证据片段（knowledge.response 溯源字段）+ 记录摘录 + 用户问题 → 重放确定性校验链（编造核对 + 引用标记校验，与在线同构）→ 调用 core 校验器（对桩：fixture 判定注入）→ 逐题逐断言判定 JSONL；CLI：`--arm/--sample/--all/--report`
- [ ] T010 [US2] `scripts/benchmark/assertion_audit.py` 断点归类：复用 `evidence.py` 对齐器翻译期望证据；规则——partial→overreach；insufficient ∧ 期望证据∉本轮片段集→not_recalled；insufficient ∧ ∈集 ∧ 未绑定→mis_bound；contradicted→独立档；汇总报告（四类分布+逐条样例）；单测（归类规则逐条）
- [ ] T011 [P] [US2] `scripts/benchmark/assertion_audit.py` 误伤基线：task_pass=true 通过题中必需断言被误判 partial/contradicted 的比例 + 逐条样例清单（SC-6 go/no-go 数据源）；单测（fixture 通过题零误伤形态）
- [ ] T012 [US2] 验收 US2（树B）：fixture 回放冒烟全过 + 报告（真事件库回放属 B3/T017，此处只验机制）

## Phase 4：US3（P1）在线接线与开关——树A 配置 + 树B 接线

故事目标：开关默认关零变化；开启时单点接线生效并落事件。独立测试判据：可证伪形态集成测试（关闭=零新增调用零事件；开启=事件载荷+处置生效；故障注入=fail-open）。

- [ ] T013 [P] [US3] `backend/src/flowpay/core/config/settings.py` + `backend/configs/local.toml`（树A 文件面）：`assertion_audit_enabled`（默认 false）+ `assertion_audit_evidence_budget`（默认 8000，注释记校准依据=冒烟实测）；密钥无关字段不入环境覆盖通道
- [ ] T014 [US3] `backend/src/flowpay/workflows/multi_agent/assertion_gate.py`（新，树B）：`audited_response` 后文本 → 句级筛选（复用 `citation_check.has_citation`/`is_assertion`/`split_sentences`；已带"未能核实"口径句豁免）→ 读 Settings 开关——关闭直接返回原文（零调用零事件）；开启调 core 校验器 → 执行处置 → 发 `assertion.audit`（载荷=AssertionAudit + conflict 详情）；预算耗尽跳过校验记录事件（等效 fail-open）；不挤占 worker 预算
- [ ] T015 [US3] `backend/src/flowpay/workflows/multi_agent/investigation.py` `audited_response`（investigation.py:490，**树B 唯一既有文件改动**）：返回前追加 assertion_gate 单点调用；树A 合并前对桩开发，rebase 后真联调
- [ ] T016 [US3] 集成测试（可证伪形态，防既有路径"空跑通过"）：①开关关闭 → 断言零新增模型调用、零新增事件、作答文本与事件序列与 009 一致（SC-1）；②开启 → 断言 `assertion.audit` 载含 verdicts/actions/fail_open，部分支持句被替换为收紧表述、缺证据句降级"未能核实"；③注入校验器故障 → 放行原文 + `fail_open=true` + 无 `run.failed`
- [ ] T017 [US3] 验收 US3（树B）：`pytest -k "assertion_gate or audit"` 全过 + ruff/basedpyright 全绿 + 报告

## Phase 5：B3 集成冒烟（主仓串行，不可并行）

- [ ] T018 合并：树A 合入 `feature/010-assertion-verification` → 树B rebase → 主仓集成；合并纪律自查（树B 不碰 core/config；树A 不碰 workflows/scripts；investigation.py 仅树B 单点）；合并后全量宿主 pytest 基线保持
- [ ] T019 冒烟 12 题（SC-4）：题单从 R96B2 失败题按 attribution 主因抽——evidence_miss→没召回嫌疑 ×3、conclusion_wrong→表述越界嫌疑 ×3、其余失败（绑错嫌疑）×3、通过题 ×3；真事件库回放（≈24 次校验调用，冒烟量级）产出 `specs/010-assertion-verification/smoke-report.md`：四档判定样例 + 三类断点各 ≥1 例 + 矛盾档占比 + 通过题误伤样例
- [ ] T020 验收 B3：`make quality` 全绿（主仓形态）；SC-1/SC-3/SC-4 出数；失败基线为空（不滚动）

## Phase 6：B4 待批准项（[批准门]——Ban 批准后才执行）

- [ ] T021 [批准门] 全量 96 题回放（SC-5）：`assertion_audit.py --all`（≈192 次校验调用）产出断点分布报告 + 误伤基线数字与逐条样例；报告存 `specs/010-assertion-verification/`；**执行与放行由 Ban 批准**
- [ ] T022 [批准门] 开启态评测臂（SC-6）：`local.toml` 设 `assertion_audit_enabled=true` → `make benchmark`（R96B 同配置新臂）；go/no-go 以 T021 误伤基线为依据；通过线=总通过 ≥20/96、断言覆盖 >47、通过题转失败 ≤2；结果并入交付报告

## Phase 7：Polish

- [ ] T023 交付报告 `specs/010-assertion-verification/delivery-report.md`：SC-1~SC-4 实测数据、SC-5/SC-6 状态（待批准/已出数）、费用记录（冒烟+回放+评测臂分开记账）、FR-4 越界定位门槛假设的实测观察（误伤样例是否支持该门槛）
- [ ] T024 Code Review 等待与清偿：Ban 复核通过前不动 push/合并；复核意见清偿后更新 tasks 勾选与交付报告（009 Phase 8 先例）

## 依赖与并行

```text
T000（提交门，Ban 批准）→ T002 切树
  ├─ 树A：T003∥T004∥T007 → T005 → T006 → T008；T013（Settings）
  └─ 树B：T009∥T011 → T010 → T012；T014 → T015 → T016 → T017
T008+T012+T013+T017 完成 → T018 合并（主仓串行）→ T019 冒烟 → T020 验收
T020 + Ban 批准 → T021 → T022 → T023 → T024
```

- **不可并行项**：T000（提交门）、T018–T020（集成冒烟主仓串行）、T021/T022（批准门，基准臂绝不并行——009 费控纪律）、T023/T024
- **树B 对桩开发**：T009–T011、T014–T016 在树A 合并前以 fixture/桩联调，T018 rebase 后真联调——接口契约以 `contracts/verdict-schema.json` 为准，两树不得各自扩展字段
- **文件面纪律**：树A 只写 `core/verify/` + `core/config/settings.py` + `configs/local.toml` + 校验器测试；树B 只写 `workflows/multi_agent/assertion_gate.py` + `investigation.py` 单点 + `scripts/benchmark/assertion_audit.py` + 对应测试；互斥文件越界即报警
- **实施策略**：MVP=US1 能力侧（T003–T008）——校验器可独立测试；US2/US3 是同一能力的两个消费面（离线回放/在线接线），无先后依赖；SC-5/SC-6 出数全部后置到批准门
