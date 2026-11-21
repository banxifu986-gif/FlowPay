# 研究：008 推理能力验证与检索收尾

plan 阶段的关键取舍记录；未决项均已在 spec Clarifications 定案。

## R1 协议共存的分流机制

- **Decision**：`ModelSettings.protocol` 配置项（chat_completions / responses），bootstrap 一行分流。
- **Rationale**：显式配置最可排查；回滚零代码。按模型名隐式判断是隐藏规则，出问题难定位。
- **Alternatives**：按模型名自动（隐式、难排查）；全量替换现有适配器（deepseek 等兼容商不支持 responses）。

## R2 多轮上下文的完整重放

- **Decision**：每次请求携带全部 input items；推理项以 encrypted_content 原样回插。
- **Rationale**：与现有 messages 口径一致，追踪事件里请求体完整可读；服务端续话（previous_response_id）会让追踪丢失全量上下文，且回放/审计失效——与 007 以来"事件全量落库"的可观测纪律冲突。
- **Alternatives**：previous_response_id（省 token 但追踪不可读、依赖服务端存续期）。

## R3 聚合产物形状

- **Decision**：Responses 流聚合成 ChatCompletion 同形状（choices/message/tool_calls/usage/finish_reason），推理项原文存 response_fields。
- **Rationale**：FR-5 的下游零改动要求；编造引用核对、用量统计、失败分类全部按现有字段消费。推理内容不进展示文本——与 chat 适配器对 reasoning_content 的既有纪律一致。
- **Alternatives**：新响应类型（下游分叉，违背 FR-5）。

## R4 失败与重试

- **Decision**：适配器只上抛异常；分类与重试走 006 的 classify_failure。
- **Rationale**：两协议共享同一失败语义（限流/超时/配额/5xx），重复实现会漂移。
- **Alternatives**：适配器自带重试（与既有臂行为不一致）。

## R5 推理档位与配置通道

- **Decision**：reasoning_effort 走既有 request_options 通道；白名单 low/medium/high，非法值启动即败。
- **Rationale**：不新增配置机制；Responses 端由适配器消费该参数，chat 端维持 reasoning_effort="none" 的现状口径。
- **Alternatives**：独立枚举字段（配置面变大）。

## R6 encrypted_content 回退

- **Decision**：供应商拒绝带推理项的请求时，丢弃推理项重试一次（记 model.retry），并在报告注明。
- **Rationale**：这是供应商行为不确定性的最小降级；不静默丢失——有事件可查。
- **Alternatives**：直接失败（一次供应商怪癖毁掉整臂）；永久记忆该模型不支持（过度设计）。

## R7 基线可比性

- **Decision**：推理臂与 FULL300 同 96 题子集对照；同模型同密钥同嵌入集合，唯一变量是推理开关。
- **Rationale**：排除模型差异污染；DeepSeek 43/96 只作参考刻度（不同模型）。
- **Alternatives**：双臂重跑（多烧一臂）；直接全量推理臂（12h+，96 题先验证更稳）。

## R8 费控口径

- **Decision**：6 题冒烟实测 → 线性外推 96 题预算 → 实际超 2 倍停手；单题 token 超冒烟中位 5 倍记异常。
- **Rationale**：推理 token 量不可理论推算（依赖题目复杂度），实测外推是唯一可靠口径。
- **Alternatives**：不设预算门（费用敞口不可控）。

## R9 混合检索收口

- **Decision**：默认 false，注释记录证据（qwen3 与 bge-m3 两空间全量逐格相同），开关保留。
- **Rationale**：两次独立全量评测已构成充分证据；显式关闭避免下轮基准继续携带无效变量。
- **Alternatives**：彻底删除混合代码（证据只覆盖本数据集，其他语料可能受益，保留开关）。

## R10 KB-API-017 缺口

- **Decision**：出补缺清单文档，不改数据集文件。
- **Rationale**：数据集作者是数据侧职责；产品代码不兜底（引用缺失文档按现有失败口径）。
- **Alternatives**：产品侧兜底（编造降级路径，复杂度高收益低）。
