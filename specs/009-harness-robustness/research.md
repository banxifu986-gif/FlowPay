# 研究：009 harness 鲁棒性

覆盖 spec 澄清后仍属实施选型的决策点。每项给出决策、依据与被否选项。

## D1 确定性错误的判定方式

**Decision**：白名单枚举（结构性 4xx：400/422 的输入类拒绝；供应商参数确定性拒绝），测试固化，扩展须新增用例。
**Rationale**：实测样本只有 R96B 嵌入 400 一例（对特定查询文本确定性拒绝）；宽泛归类会误伤 429 限流（瞬时可重试）与 5xx（通常瞬时）。clarify 会话已拍板最小白名单。
**Alternatives considered**：全部 4xx 入档（误伤 429，否）；4xx+5xx 入档（5xx 重试有意义，否）；按错误消息文本模糊匹配（脆弱、不可测，否）。

## D2 节点级断点的落盘通道

**Decision**：新增 `turn.progress` 事件随既有事件流落盘，恢复时从最近 progress 重建 SharedTurn 预填；不新建存储、不改检查点形状。
**Rationale**：事件日志已是唯一持久化事实源（006 起单写者设计）；检查点形状变化会破坏 FR-11 下游兼容；事件天然带序号可审计。
**Alternatives considered**：扩展检查点 saved_state 增 progress 字段（改检查点形状，前端/恢复逻辑连带改，否）；LangGraph 原生 checkpointer（引入新持久化组件，违反"复用既有机制不新增存储形态"，否）。

## D3 恢复重建方式

**Decision**：恢复=重建 SharedTurn 闭包 + 预填 done 键/findings/evidence/clarification（既有恢复逻辑不动），dispatch 按 `task_progress` 过滤已完成子任务；节点幂等以"任务目标+worker 类型"为幂等键。
**Rationale**：SharedTurn 本就每轮重建（state.py 设计注释），预填是最小改动路径；Send 并行分发下幂等键防重复收尾。
**Alternatives considered**：恢复时整轮重放但工具层去重（实现更简单但模型调用照付，违背"不重查已完成子任务"的 SC-4，否）；图状态持久化+LangGraph 断点续跑（同 D2 否项）。

## D4 引用校验的实现位置与判定口径

**Decision**：放 `workflows/multi_agent/citation_check.py`（业务决策层），只解析引用标记存在性（记录编号、片段引用两种模式），无标记断言降级为未证实表述或至多一次定向重写；编号真伪仍归既有编造引用核对机制。
**Rationale**：什么算"结论句需要引用"是业务口径不是通用能力；标记存在性是确定性可测的最低门槛（宽松判定：宁可漏判不误伤）。
**Alternatives considered**：放 core/llm 层做通用校验（越层，业务规则下沉，否）；语义级校验（需模型调用，违反"零额外调用"与费控假设，否）；只靠提示词（008 已证明到边际，否）。

## D5 worker 预算上限的默认值

**Decision**：配置项 `investigation_tool_budget`，默认 10 次/worker/任务；实施批次内用 007 机制报表的既有轮次分布核对后定档（若 96 题中单任务工具调用 P95 显著低于 10 则下调）。
**Rationale**：需要上限防跑飞，但 008 数据没有单任务调用次数分布的现成统计；10 是"耐心约束生效后正常调查所需调用量"的保守上界，实施时以数据校准。
**Alternatives considered**：全局轮级预算（已有 Budget 机制管整体，重复，否）；不设上限（跑飞无闸门，违背 FR-9，否）。

## D6 能力映射的表达

**Decision**：静态规则表（任务目标/工具诉求 → sql_worker | doc_worker），分发前核对；改派优先，重计划一次兜底。
**Rationale**：worker 与工具的绑定在代码里已是静态事实（sql_worker 拿不到知识检索、doc_worker 拿不到 SQL），映射表只是把这个事实显式化给 plan 校验用；题型矩阵无失败样本支撑（grill 已排除）。
**Alternatives considered**：LLM 判断任务-worker 匹配（多一次调用且不可测，否）；完整能力声明矩阵（防御过度，grill 已否）。

## D7 多跳第二跳结论值的提取来源

**Decision**：从本轮 `SharedTurn` 发现（findings 中的记录编号、实体值）提取，`query_with_findings` 与既有 `query_with_facts`（澄清答案）并列叠加。
**Rationale**：R96B 多轮证据未中的第二跳断链是"第一跳查到的记录值没进第二跳查询"；澄清答案是跨轮输入，findings 是本轮第一跳产出，两者来源不同、互补。
**Alternatives considered**：只扩 query_with_facts 把 findings 混入（口径混淆，否）；LLM 生成第二跳查询（收益上界 3.3pp 封顶，grill 已否）。

## D8 配额豁免的实现点

**Decision**：`execute_turn` 增 resume 通道——跳过日回合计数、保留商户并发检查；不新增配置。
**Rationale**：日计量来自事件日志的轮次计数，恢复是同一轮次的继续执行；clarify 拍板"同轮不重复计数"。并发检查保留防恢复期间重复执行。
**Alternatives considered**：恢复也计一次（惩罚用户为系统故障买单，否）；恢复完全免检（绕过日上限，失控无闸门，否）。

## D9 降级作答与 waiting 的边界

**Decision**：证据不足走"未能核实"作答口径，不转 `waiting_user`；收束（预算超限）与查不到并入同一未证实口径，原因只在事件日志。
**Rationale**：基准中停在 waiting 是失分形态（008 逐题对照），且 waiting 语义是"条件不足需用户输入"，与"系统查不到"语义不同；统一口径让报表可识别（SC-5）。
**Alternatives considered**：证据不足转 ask_user（语义混淆 + 机制门"重复问"风险，否）；单独"调查超限"表述（多一路报表识别规则，clarify 已否）。
