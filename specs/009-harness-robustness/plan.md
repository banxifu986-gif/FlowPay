# 实施计划：009 harness 鲁棒性

需求唯一来源见 spec.md（含 clarify 会话）；本文件记录落地路径、分层接线、批次与验收口径。只写计划不动代码；实施另起新分支。

## Technical Context

- 失效分类：`core/execution/failures.py` 的 `FailureInfo`（现有 `retryable: bool`）扩展确定性档位。**审查修正（2026-11-23）**：现状模型类 400 已归 other 档（`retryable=False`，不烧重试预算）——档位的职责不是修重试浪费，而是**驱动 FR-2 的链路禁用判定并进入事件载荷**（`tool.outage` 携带 `failure_class`）；`classify_failure` 的白名单判定服务于工具链路侧。
- 工具链路禁用：run 生命周期的 `ToolOutage` 注册表挂 `SharedTurn`（天然单例），登记点在各工具包装层（`run_sql`/`run_logs`/`search_documents`/`read_document` 四处闭包捕获 shared）：包装内捕获自身链路异常→分类（FR-1 白名单）→deterministic 则登记 outage + 发 `tool.outage` 事件→重抛，由既有 `investigate()` catch-all 走 error_report 收束该任务（**禁用即收束，Ban 拍板 2026-11-23；不做"以其余工具继续"的 catch-all 拆分**）。后续任务对已禁用链路的调用在包装层立即抛失败终止任务，不再发起请求。
- 降级作答：`investigation.py` synthesize 的 `resolve_response` 增加未证实标注路径；"未能核实"为统一口径（含预算收束与禁用收束），收束原因只写事件日志。**新增（FR-12）**：synthesize 无决策/空正文（`ModelOutputError`）→ 确定性重试一次（复用 003 编造核对 DISPUTE 先例）→ 仍空则诚实占位文本正常完成，不 `run.failed`。
- 节点级断点：worker 子任务收尾处把增量进度（已查询键、发现、证据引用）写 `turn.progress` 事件随事件流落盘；**读取归属 manager 层（审查 C2）**：恢复时由 manager 读最近 progress 事件、按幂等键 merge（并行 worker 落库顺序无保证，已 finished 条目不被未 finished 同名条目覆盖）后注入 `TurnInput.saved_state`，workflow 不碰事件库；`dispatch` 按 task_progress 过滤已完成子任务。
- 配额豁免：**审查修正（B1）——`execute_turn` 不存在**，真实入口是 `_submit_run(..., *, resume)`（manager.py:173）与既有 `resume_run`（resume=True 通道已在）；`TurnInput.resumed` 字段已在。三个必改点：①`_persist_run` 对 resumed 轮照样写 `run.started`（只跳过 message.user），而日计量 `daily_run_count`（`core/event_log/store.py:197`）按 run.started 计数——"同轮不重复计数"落地为 `daily_run_count` 过滤 `resumed=true` 的 run.started（不新增事件类型，FR-11 优先）；②resume 提交仍走 `_reserve_quota` 预留并发（仅日回合检查跳过），否则 `_execute` finally 的 `_release_quota` 会净减一个并发名额（漏还）；③禁止 `run.failed` 语义变化，硬失败终态不动。
- 引用校验：`workflows/multi_agent/citation_check.py`（新增）——synthesize 输出前解析结论句引用标记（记录编号、片段引用的存在性），无标记断言确定性降级为未证实表述或触发至多一次定向重写；只看标记存在性，编号真伪仍归既有编造核对机制。
- plan 校验：`investigation.py` 分发处按能力映射核对任务目标与 worker 类型；不匹配改派，改派不成重计划至多一次，仍不匹配丢弃并记 `task.dropped`。
- worker 预算：单 worker 单任务工具调用计数挂 `SharedTurn`，上限配置项 `investigation_tool_budget`（建议默认 10，实施时随测试定档），超限收束并携带已查发现返回。
- 多跳两段式：新增 `query_with_findings`（与 `query_with_facts` 并列）——从本轮发现提取记录编号/关键值拼进第二跳查询；**拼接值取有序集合并排序**（审查 B4：保证同一 findings 集合产出同一查询文本，恢复续跑时不因拼接顺序漂移触发重查）；无第一跳结论时不改变现有行为。
- 事件兼容：新信号（`turn.progress`、`tool.outage`、`task.reassigned`/`task.dropped`、`task.budget_exhausted`）走既有事件通道；前端与报表对未知事件按既有纪律不阻断。

## 章程检查

`.specify/memory/constitution.md` 不存在；按 CLAUDE.md / AGENTS.md 自查：

- 失效分类、禁用注册表、配额豁免放 `core/execution/`（通用能力层）；引用校验规则、能力映射、预算上限属业务决策放 `workflows/` ✓
- 不新增第三方依赖 ✓
- 无 DB schema 变更（新事件为 JSON 事件，复用既有事件日志）✓
- 测试断言外部可见行为（status、messages、事件序列），不锁内部调用次数 ✓
- 失败基线为空：新增用例并入既有 pytest，不建豁免清单 ✓

无违反项。

## 批次

| 批次 | 内容 | 门禁 |
|---|---|---|
| B1 | 失效分类确定性档 + 工具链路禁用 + 降级作答 + synthesize 空输出重试/占位（FR-1~3、FR-12） | 新增单测全过；全量 pytest 基线保持；ruff/basedpyright 全绿 |
| B2 | 节点级断点 + resume 计量修正 + 恢复路径（FR-4/5） | 单测 + 恢复集成测试（零重查断言 + 恢复轮日计量为 0） |
| B3 | 引用校验 + 未证实口径（FR-6/7） | 单测（含误判边界） |
| B4 | plan 校验 + worker 预算上限（FR-8/9） | 单测（改派/重计划/收束三路径） |
| B5 | 多跳两段式（FR-10）+ 离线机制抽查 | 单测 + retrieval.py 诊断 |
| B6 | 冒烟 6 题 → 96 题全臂 → 机制/attribution/逐题对照报告 | SC-1~SC-8 全部出数 |
| 收尾 | make quality + 交付报告 | 全绿；失败基线为空 |

## 分层接线

### core/execution/failures.py

- `FailureInfo` 增 `deterministic: bool = False`；`classify_failure` 白名单判定：
  - 嵌入/检索链路的结构性 4xx（400/422，响应体含"输入不合法"语义）→ deterministic。
  - 请求参数被供应商确定性拒绝（如推理参数不兼容的 4xx）→ deterministic。
  - 429/超时/5xx/连接错误 → 仍 transient（现状重试语义不变）。
- **消费方是工具包装层与 `tool.outage` 事件载荷**，不是 manager 重试点——现状模型类 400 已归 other（不可重试），manager 的重试语义零改动；白名单以错误形态枚举 + 测试固化，不做宽泛状态码归类。

### core/execution/manager.py

- `_submit_run`/`resume_run`（既有 resume=True 通道）三处改动：
  1. 日回合计数：`daily_run_count`（`core/event_log/store.py`）过滤 `resumed=true` 的 run.started——恢复轮的 run.started 载荷已带 resumed 标记，不新增事件类型；
  2. 并发对称：resume 提交仍走 `_reserve_quota` 预留并发名额（只跳过日回合检查）——否则 `_execute` finally 的 `_release_quota` 会净减一个名额；
  3. 恢复数据注入：resume 路径读最近 `turn.progress` 事件、按幂等键 merge 后并入 `TurnInput.saved_state`（workflow 不碰事件库，审查 C2）。
- 终态判定零改动：`run.failed` 仅硬失败；工具链路失败经 error_report 收束不再是整轮失败理由（既有行为，非本特性新引入）。

### workflows/multi_agent/state.py（SharedTurn 扩展）

- `tool_outages: set[str]`——run 内禁用链路登记。
- `task_progress: dict[str, SubtaskProgress]`——子任务完成状态（目标、worker 类型、已查询键、发现、证据引用）。
- `task_tool_calls: dict[str, int]`——单任务工具调用计数。

### workflows/multi_agent/investigation.py

- 工具包装四处（`run_sql`/`run_logs`/`search_documents`/`read_document`，均闭包捕获 shared）：调用前查 `tool_outages`——已禁用链路**立即抛失败**，由既有 `investigate()` catch-all 走 error_report 收束该任务（禁用即收束，不空转）；包装内捕获自身链路异常 → `classify_failure` → deterministic 则登记 `tool_outages` + 发 `tool.outage` 事件（载荷含 `failure_class` 与链路标识）→ 重抛走既有收束路径。**不做"以其余工具继续"的 catch-all 拆分（审查 B2，Ban 拍板禁用即收束后不再需要）**。
- `resolve_response`：子任务无证据时统一"未能核实"表述；**synthesize 无决策/空正文（ModelOutputError）→ 确定性重试一次 → 仍空则诚实占位完成（FR-12，参照 003 DISPUTE 重试先例）**。
- plan 分发校验：能力映射（需要业务记录 → sql_worker；规则/文档 → doc_worker）不匹配时改派；改派不成重计划一次（带纠错提示重跑 plan 节点）；仍不匹配丢弃 + `task.dropped`。
- `query_with_findings` 在第二跳查询拼接第一跳结论值（有序集合并排序，≤500 字符清洗）。
- 子任务收尾：写 `turn.progress`（增量、幂等键=任务目标+worker 类型）。

### workflows/multi_agent/graph.py

- `run` 恢复分支：预填数据来自 manager 注入的 `TurnInput.saved_state`（progress 已按幂等键 merge——并行 worker 落库顺序无保证，已 finished 条目不被未 finished 同名条目覆盖）；graph 本身不读事件库（审查 C2 分层归属）；`dispatch` 按 `task_progress` 过滤已完成子任务。

### workflows/multi_agent/citation_check.py（新增）

- 输入 synthesize 草稿文本 → 解析结论句引用标记（记录编号模式、片段引用模式）→ 无标记断言句降级为未证实表述或产出定向重写指令（至多一次）。
- 纯本地解析，零模型调用（重写除外）。

### core/config/settings.py + local.toml

- `investigation_tool_budget`（默认建议 10）；`execute_turn` resume 通道无新配置项。

### scripts/benchmark/run.py / clarification.py

- 报表增加未证实口径统计与 `turn.progress` 断点事件核对；协议/推理标注沿用 008 机制。

## 验收路径

1. B1–B5 单测与集成（容器 pytest）。测试形态纪律（审查 C1，防"空跑通过"）：注入确定性错误必须断言**新行为**——`tool.outage` 载荷 `failure_class=deterministic`、后续调用不再发起请求、无 `run.retry`；注入 synthesize 空输出断言"重试恰一次 + 占位完成 + 无 run.failed"；恢复测试断言零重查 + 恢复轮日计量为 0。
2. B6 冒烟：`--per-type 1` 6 题，R96B 同口径（gpt-6-luna + responses + medium + bge-m3），验 run.failed=0、事件链路正常。
3. B6 全臂：96 题一次出 SC-2/SC-5/SC-6/SC-7 数据；机制门（SC-1）与 attribution 同臂出。
4. 报表核对清单：SC-1 机制门三项；SC-2 run.failed=0 + 确定性错误不烧重试（事件序列）；SC-3 降级作答契约；SC-4 零重查（恢复前后查询键不交集）；SC-5 无裸奔断言；SC-6 证据 ≥50/96；SC-7 multi_hop ≥11/16 + 第二跳机制可验证；SC-8 make quality。

## 风险与备注

- **节点级断点与 Send 分发模型**：`SharedTurn` 是闭包资源，恢复=重建闭包+预填（数据由 manager 层读 progress 事件 merge 后注入）；"跳过已完成子任务"在 dispatch 层按 progress 过滤实现，需验证与并行 Send 的兼容（幂等键防重复收尾、并行落库顺序无关）。
- **引用校验误判**：只看标记存在性属宽松判定——宁可漏判（放行带标记但编号伪造的句子，由编造核对机制兜底）也不误伤正常作答。
- **确定性错误样本稀缺**：实测样本仅 R96B 嵌入 400 一例；白名单起步最小，测试用注入错误固化形态，不为未知错误形态预写判定。
- **全臂成本**：R96B 同口径 ~2.5h，预算档位不变；冒烟后照 008 纪律外推核对。
- **Windows 环境**：测试在容器内跑；脚本类可用宿主 venv。
- **下游兼容**：前端对未知事件不阻断（既有纪律），但报表侧未证实口径统计是新消费方， tasks 里单列。
