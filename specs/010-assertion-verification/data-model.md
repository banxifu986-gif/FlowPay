# 数据模型：010 逐断言校验

复用既有实体（PassageSet、事件日志、ChatModel 调用通道）的扩展与新增实体。所有新事件为 JSON 事件，无 DB schema 变更。判定结构全文见 `contracts/verdict-schema.json`（两树共享契约）。

## 实体

### Assertion（新增，core/verify/contracts.py）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | str | 原子断言文本（取自待校验文本的句级切分或其子句）。 |
| `refs` | list[str] | 依据的片段引用（`doc:KB-XXX#cNNN` 形态），来自 LLM 抽取。 |
| `record_ids` | list[str] | 断言涉及的记录编号（既有 RECORD_ID 模式），用于装配记录摘录。 |

无跨轮身份要求（断言是单次校验内的产物，不持久化身份）；重放同一文本同一证据时判定可复现，但 LLM 抽取本身不承诺逐字稳定——回放报告按"题→断言文本"对齐，不依赖断言序号跨运行一致。

### AssertionVerdict（新增，core/verify/contracts.py）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `assertion` | Assertion | 被判定的断言。 |
| `verdict` | str | `supported` / `partial` / `contradicted` / `insufficient` 四档。 |
| `overreach` | object \| null | 越界定位：`dimension` ∈ {对象, 时间, 数字, 条件} + `note` 说明；仅 `partial` 必须携带，缺失视同 `insufficient`（spec FR-4 默认假设）。 |
| `revision` | str \| null | 收紧后表述（仅 `partial` 附带；由校验调用一次性给出，处置零新增调用）。 |
| `conflict` | object \| null | 矛盾详情：冲突双方出处 + 冲突点描述；仅 `contradicted` 携带（D10，clarify 拍板）。 |

### AssertionAudit（新增，一次校验的产物）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `verdicts` | list[AssertionVerdict] | 判定清单（可能为空 = 无断言放行）。 |
| `actions` | list[dict] | 处置动作：`{sentence, action: keep | replace | downgrade, revision?}`；replace 过子集校验后生效。 |
| `fail_open` | bool | 校验器任一环节失败时 True（放行原文，动作列表为空）。 |
| `text` | str | 处置后的最终文本（fail_open 或零断言时 = 输入原文）。 |

处置幂等：处置后文本再过校验器不二次改写（已带"未能核实"口径的句子在筛选层豁免，复用 `citation_check.UNVERIFIED_PHRASES` 先例）。

### 证据装配输入（assembly.py 组装，非持久化实体）

- 待校验文本 + 本轮用户问题。
- 绑定引用片段原文（按断言 refs 取自本轮 PassageSet）。
- 全轮片段：本轮 PassageSet 按 score 排序、字符封顶（默认 8000，配置项）。
- 记录摘录：断言 record_ids 对应的数据摘录（事件与 shared.seen）；摘录缺失按证据缺失参与判定，不阻塞。
- 全部来自本轮已登记证据，零新增检索调用。

### AssertionBreakpoint（新增，评测侧归类产物，scripts/benchmark/assertion_audit.py）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `question_id` | str | 题号。 |
| `breakpoint` | str | `overreach` / `not_recalled` / `mis_bound` / `contradicted` 四类。 |
| `rule` | str | 归类依据：partial→overreach；insufficient ∧ 期望证据∉本轮片段集→not_recalled；insufficient ∧ ∈集 ∧ 未绑定→mis_bound；contradicted→contradicted。 |
| `samples` | list | 逐条断言样例（判定 + 期望证据命中状态）。 |

误伤基线：task_pass=true 的通过题中，必需断言被误判为 partial/contradicted 的比例与逐条样例。

## 新增事件（JSON，复用既有事件日志通道）

| 事件 | 载荷要点 | 时机 |
| --- | --- | --- |
| `assertion.audit` | AssertionAudit（verdicts/actions/fail_open/text）+ 矛盾 `conflict` 详情 | 开关开启的每次最终作答校验后（含预算耗尽跳过与 fail-open 的记录形态） |

兼容性：前端与报表对未知事件不阻断（既有纪律）；不改检查点形状与用量统计口径；模型调用自身走既有 `model.request`/`model.response` 通道。

## 校验流程（状态转换）

1. 最终作答经既有统一出口（编造核对 + 引用标记校验）产出文本。
2. 开关关闭 → 原文发布，零新增调用零事件（SC-1）。
3. 开关开启 → 筛选带引用断言句 → 装配证据 → 抽取调用（断言清单+绑定）→ 代码验 ID 存在性（不存在→insufficient，不经语义校验）→ 批量校验调用（四档判定）→ 处置映射 → 发布处置后文本 + `assertion.audit` 事件。
4. 任一环节失败（调用失败/解析失败/装配异常/预算耗尽）→ fail-open 放行原文 + 事件记录 `fail_open=true`；不阻断回答、不产生 `run.failed`。
