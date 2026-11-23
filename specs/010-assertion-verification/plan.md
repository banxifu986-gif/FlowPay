# 实施计划：010 逐断言校验

需求唯一来源见 spec.md（含 grill + clarify 会话）；本文件记录落地路径、分层接线、双工作树分工与批次。只写计划不动代码；实施在新分支（`feature/010-assertion-verification`）双工作树并行。

## Technical Context

- **接线点（唯一）**：`workflows/multi_agent/investigation.py:490` 的 `audited_response`（编造核对 + 引用标记校验的统一出口）之后、发布之前追加校验器调用；输入为经既有引用校验后的文本，已带"未能核实"口径的句子跳过（复用 `citation_check.py` 的 `UNVERIFIED_PHRASES` 豁免先例）。
- **筛选口径**：逐断言校验的对象 = 带引用标记（`citation_check.PASSAGE_REF` / `RECORD_ID`）的断言句；筛选复用 `citation_check.py` 的 `has_citation` / `is_assertion` / `split_sentences`（句级切分、标记不跨句、幂等先例）。记录编号真伪仍归既有编造核对，本特性只补**片段引用 ID 真伪**缺口。
- **证据来源**：片段 = 本轮 `PassageSet`（上限 200 条/40000 字符，有稳定 id、locator、sha256）；记录摘录 = 事件与 `shared.seen` 中记录编号对应的数据；用户问题 = 本轮 `user_message`。装配零新增检索调用。
- **模型调用**：复用主模型配置（`ChatModel` 既有调用通道，`model.request`/`model.response` 事件自动落库——回放依据即此）；每作答 +2 次调用（抽取、批量校验）；输出 JSON 经既有解析工具，解析失败按 fail-open。
- **配置面**：`assertion_audit_enabled`（默认 false）与片段封顶预算进 Settings 与 local.toml（密钥无关）；回放脚本走 CLI 参数不进配置面。
- **回放数据源**：009 验收臂（R96B2，`var/benchmark/20261122-200707-6010b8`）事件库；草稿自 `model.response` 载荷恢复（完整 completion 正文已落库），期望证据映射复用 `scripts/benchmark/evidence.py` 的证据 ID 对齐器。回放重放确定性校验链（编造核对 + 引用标记校验）得到与在线同构的输入。
- **评测判分零改动**：`scripts/benchmark/run.py` 的子串判分口径不动；降级格式沿用"未能核实：原句"（保留原句，不影响子串命中）；收紧替换是唯一影响 task_pass 的动作，其风险由误伤基线管控。

## 章程检查

`.specify/memory/constitution.md` 不存在；按 CLAUDE.md / AGENTS.md 自查：

- 校验器能力（类型、装配、LLM 交互、处置映射、fail-open）放 `core/verify/` 通用能力层，不依赖 workflows/router/bootstrap/scripts ✓；业务接线与筛选放 workflows ✓；回放与报告放 scripts ✓
- 不新增第三方依赖 ✓
- 无 DB schema 变更（新事件为 JSON 事件，复用既有事件日志）✓
- 测试断言外部可见行为（事件序列、判定 JSON、作答文本），不锁内部调用次数 ✓
- 失败基线为空：新增用例并入既有 pytest，不建豁免清单 ✓
- 对外契约兼容：新增信号走既有事件通道；不改既有事件类型、检查点形状、用量统计口径 ✓
- 面向运营可调参数进 Settings 与 local.toml ✓

无违反项。

## 双工作树分工（契约先行，能力侧/消费侧）

**切出点（B0，Ban 批准 spec 后执行）**：新分支先提交 spec 套件 + `contracts/verdict-schema.json` + `core/verify/` 类型骨架（纯类型与常量，无实现），随后切两树。

| | 树A（能力侧） | 树B（消费侧） |
| --- | --- | --- |
| 文件面 | `backend/src/flowpay/core/verify/`（新包）+ `backend/src/flowpay/config`（Settings 两字段）+ `backend/tests` 校验器测试 | `workflows/multi_agent/assertion_gate.py`（新）+ `workflows/multi_agent/investigation.py`（**唯一触碰的既有文件**：`audited_response` 单点接线）+ `scripts/benchmark/assertion_audit.py`（新）+ 报表扩展 + 对应测试 |
| 不碰 | workflows、scripts、investigation.py | core（对桩/fixture 开发）、config |
| 合并 | 先合入 integration | rebase 后合入 |

纪律沿用 009 parallel-worktrees.md：副树禁 `make up`、各树各自 `uv sync`、各自过 quality、合并后主仓终跑；`.specify/feature.json` 无多会话 speckit 写入（spec 阶段已收口）。测试环境事实沿用 009 实测结论（fcntl 容器-only 模块、宿主 pytest 子集快跑、postgres 55432 并发互踩先串行重跑）。

## 批次

| 批次 | 内容 | 门禁 | 执行树 |
|---|---|---|---|
| B0 | 契约先行提交（spec 套件 + 判定 schema + core 类型骨架）→ 切两树 | Ban 确认 spec | 主仓 |
| B1 | `core/verify/` 能力包：类型、证据装配、抽取调用、批量校验调用、处置映射、fail-open + Settings 两字段 | 四档判定 fixture、ID 存在性、句级替换子集校验、降级幂等、fail-open 单测全过；ruff/basedpyright 全绿 | 树A |
| B2 | `assertion_gate.py` 筛选+接线（对桩开发）+ `assertion.audit` 事件 + 回放驱动 + 断点归类报告（对 fixture 开发） | 单测 + fixture 回放冒烟 | 树B |
| B3 | 合并 A → B rebase → 主仓集成：真联调 + 冒烟 12 题（三类断点各 ≥1 + 通过题误伤样例） | `make quality` 全绿；SC-1 开关关闭零回归（专项测试）；SC-4 冒烟报告出数 | 主仓 |
| B4 | 全量 96 题回放报告（SC-5）→ Ban 审 → 开启态评测臂（SC-6） | **Ban 批准后才执行**；go/no-go 以误伤基线为依据 | 主仓 |
| 收尾 | 交付报告 + Code Review 等待 | SC-1~SC-4 出数；SC-5/SC-6 标记待批准 | 主仓 |

## 分层接线

### core/verify/（树A，新包）

- `contracts.py`：`Assertion`（文本 + refs + record_ids）、`AssertionVerdict`（四档 + overreach 定位 + revision 收紧表述）、`AssertionAudit`（判定清单 + 处置动作 + fail_open 标记）——与 `contracts/verdict-schema.json` 同构。
- `assembly.py`：证据装配（绑定引用原文 + 全轮片段按 score 排序、字符封顶 + 记录摘录 + 用户问题）；摘录缺失按证据缺失参与判定，不阻塞。
- `extractor.py`：抽取调用——LLM 一次调用输出原子断言清单（JSON）；无断言返回空清单。
- `verifier.py`：批量校验调用——LLM 一次调用逐断言判定四档；部分支持必须附 overreach 定位与 revision，缺定位视同证据不足（spec FR-4 默认假设）。
- `actions.py`：处置映射（纯函数）：支持→放行；部分支持→句级替换 revision（替换句过数字/编号子集校验，越界降级）；证据不足/矛盾→降级"未能核实"（矛盾额外携带分歧详情）；幂等。
- `errors.py` + fail-open 包装：任一环节异常 → 返回放行原文 + fail_open 标记，不抛出。

### workflows/multi_agent/assertion_gate.py（树B，新文件）

- 筛选：`audited_response` 后文本 → 句级切分 → 带引用标记且非豁免口径的断言句集合。
- 开关：`assertion_audit_enabled` 关闭时直接返回原文（零调用零事件）；开启时调 core 校验器 → 执行处置 → 发 `assertion.audit` 事件（判定清单、处置、fail_open、矛盾分歧详情）。
- 预算耗尽：跳过校验并记录事件（等效 fail-open）；不挤占 worker 预算。

### workflows/multi_agent/investigation.py（树B，唯一既有文件改动）

- `audited_response` 返回前追加 `assertion_gate` 单点调用（开关关闭时零行为变化）。

### scripts/benchmark/assertion_audit.py（树B，新文件）

- 回放：从验收臂事件库恢复草稿与证据 → 重放确定性校验链 → 执行校验器 → 逐题逐断言判定 JSONL。
- 断点归类：复用 `evidence.py` 期望证据映射——部分支持→表述越界；证据不足+期望证据不在本轮片段集→没召回；在集未绑定→绑错；矛盾→独立档。
- 报告：四类断点分布 + 逐条样例 + 通过题误伤基线（比例 + 样例清单）。

### 事件（JSON，复用既有事件日志通道）

| 事件 | 载荷要点 | 时机 |
| --- | --- | --- |
| `assertion.audit` | 判定清单（四档+overreach+revision）、处置动作、`fail_open`、矛盾分歧详情 | 开关开启的每次最终作答校验后 |

兼容性：前端与报表对未知事件不阻断（既有纪律）；不改检查点形状与用量统计口径。

## 假设与研究依赖

- 冒烟 12 题题单：从 R96B2 失败题按 attribution 主因抽（evidence_miss→没召回嫌疑、conclusion_wrong→越界嫌疑）各 3 + 通过题 3；实施 B3 时定题单。
- 片段封顶默认 8000 字符（配置项），冒烟实测校准（PassageSet 上限 40000 字符的 20%，防校验 prompt 吃满稀释判定）。
- 全量回放 96 题 ≈ 192 次校验调用、开启态臂 ≈ 每作答 +2 次调用——均属 96 题量级运行，Ban 批准后才执行（费控延续 009 纪律）。
- FR-4 越界定位门槛维持 spec 默认假设；B1 实现按契约实现，推翻成本限于校验 prompt 与处置映射一处。
