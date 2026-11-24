# 数据模型：FlowPay 大众版 FAQ

## 发布物与知识

| 实体 | 必需身份与数据 | 校验 |
| --- | --- | --- |
| FAQ release | `release_id`、`status=frozen`、`artifact_groups`；各文件路径、字节数及 SHA-256；固定 release 清单摘要 | 组、路径及文档 ID 唯一；只认根下登记的相对路径；新冻结内容换 release ID，不原地更新 |
| Canonical 业务清单 | 按现有来源契约记录版本和 SQLite 文件摘要 | 与 collection 的 `dataset_release_id`、`dataset_manifest_sha256` 配对；发行清单另有自己的摘要 |
| FAQ 文档 | MD + YAML；`doc_id`、`title`、`doc_type=faq`、`audience=external`、`indexing=include`；有历史关系时 `effective_from`、可缺省的 `effective_to`、可缺省的 `supersedes` | `KB-FAQ-<主题>-<版本>` 唯一；同主题内只指向直接前版，禁止自指、循环及时间重叠；时间区间 `[起, 止)`，正文写清适用时段 |
| Q/A 小节 | `##` 标题和问答正文；金标使用 `doc_id`＋精确 `section_title` | 同篇小节标题唯一、证据可由实际切块结果回溯；相同主题新旧稿可沿用标题，不共享 doc_id；派生 chunk 和 embedding 由构建生成，不手写、不提交 Git |

## 只读合成记录

| 实体 | 字段与关系 | 校验 |
| --- | --- | --- |
| 模拟付款人 | `payer_id`，绑定到专用模拟登录主体；映射到现有受限账户范围 | 至少两人；身份映射唯一，运行时不可由输入覆盖；不存真实个人数据或口令 |
| 付款 | `payment_id`、`payer_id`、`order_ref`、`status`、`amount_minor`、`currency`、`occurred_at` | 编号符合既有记录引用规则（如 `pay_faq_0001`）；允许多个付款尝试关联同一订单；金额用最小货币单位，时间带明确时区 |
| 退款 | `refund_id`、`payment_id`、`status`、`amount_minor`、`currency`、`requested_at`、可空 `settled_at` | 只能关联同一快照存在的付款，归属继承付款人；退款进度与实际到账不能互推，空字段不补造；仅暴露支持题面的字段 |

快照只读。业务查询通过已注册关系和白名单条件进行，不暴露任意 SQL；`payer_id` 可以映射到既有 `account_id` 范围键，但不是可提供的过滤参数。公开诊断只引用业务记录号与必要字段，不引用合成身份范围值或鉴权信息。其他状态和值域由 FAQ 口径定义，并在构建时核查一致性。

## 评测套件

- **Question**：`question_id`、`question_type`（`simple_faq` / `ambiguous_clarification` / `personalized_cross_source` / `temporal_conflict` / `multi_hop` / `multi_turn`）、`difficulty`（`easy` / `medium` / `hard`）、`group_id`、`split`、首轮用户文本、受限模拟身份引用、可选渐进披露脚本和固定判定时点。题目 ID 全包唯一。题单是 runner 输入，不直接作为模型提示；逐轮只披露用户可见文本和会话本身已知的条件。
- **Rubric**：同 `question_id` 一对一。每项必需及禁止断言有稳定项 ID 与语义描述；需要证明的断言绑定一个或多项证据，分别指向文档小节或记录类型／编号／必要字段与期望值。`rule_ids` 可选且仅评测端使用。时效题标明何种日期决定口径；多跳题标明前后证据依赖；澄清／多轮题标明关键缺失类别和每轮必要行为，并声明停止条件。纯澄清轮可无最终业务断言，但必须有追问要求及禁止猜测项。
- **结果**：以 `run_id`、`question_id`、split、release 和 collection 身份定位；带实际回合结果、逐断言及证据／行为状态、失败原因、耗时和可审计的证据位置。题目终态区分 `pass`、`fail`、`execution_error`、`scoring_error`；错误不可标为通过。用于调试的细节留本地，不记录凭据。

划分按共享记录、同一语义改写和脚本家族的等价组进行；若数种关系相连，整个连通组同 split。公开 FAQ 小节和模拟身份不是分组键，两个 split 可以合法共享它们。官方包各题型都有三个难度档，整体 `30/50/20`，dev 为 `12/20/8`，test 为 `18/30/12`；题型拆分按 [规格表](spec.md#题型及拆分)。
