# 阶段报告：009 US4+US5（T020–T025）

日期：2026-11-23。分支：`feature/009-us45`（基点 2ab7485，两个提交：1f7ee0c US4、e9b115c US5）。工作区干净，未 push、未切分支。

## 交付内容

### US4（T020–T022）plan 分发前能力校验

- `investigation.py` 新增 `CAPABILITY_KEYWORDS` 静态表 + `expected_kind`（记录编号为最强信号直接判 sql；文本关键词按域枚举，两类同时命中或不命中返回 None 不质疑模型——research D6，sql 词表不放"投递"这类动作词，避免"投递失败的处理规则"类文档目标被误改派）。
- `validate_tasks(tasks, final=False)`：改派（final=False）/丢弃（final=True）两口径。
- `enforce_capabilities`：首轮校验改派发 `task.reassigned` → 存在改派时带纠错提示重计划一次 → 重计划产出合法列表则替换并 final 复核，仍不匹配丢弃发 `task.dropped`；重计划失败/空产出沿用改派列表兜底，不失败整轮。事件走既有 tracer 通道（FR-11 下游零改动）。

### US5（T023–T025）多跳两段式检索

- `clarification.py` 新增 `query_with_findings(findings, query, passages=())`：从 findings 的 target/digest 与已取回片段正文提取记录编号拼进第二跳查询；findings 按鸭子类型只读 target/digest（避免 clarification→state 静态 import cycle）。与 `query_with_facts` 在 `search_documents` 里叠加（只追加一行，不动既有行）。
- `retrieval.py` 新增 `second_hop_audit(arm)`（T028 用）：从臂事件库 `source.request`/`knowledge.request` 核对第二跳检索是否携带第一跳业务查询里出现过的记录编号；第一跳无编号的会话不参与计数。

## 三路径事件断言清单（T021，test_multi_agent.py）

| 路径 | 场景 | 事件断言 | 行为断言 |
|---|---|---|---|
| 改派成功 | plan_1 给 docs kind + 业务记录目标，重计划响应空任务 | `task.reassigned` 恰 1 条（objective/from=docs_worker/to=sql_worker），`task.dropped` 为空 | status=completed，改派列表兜底执行 |
| 重计划后成功 | plan_2 给出正确 kind（sql+docs 双任务） | `task.reassigned` 1 条、`task.dropped` 为空 | 纠错提示带原指派与正确方向；重计划列表被采用，docs 任务检索执行 |
| 丢弃且其余正常 | plan_2 仍给错误 kind | `task.reassigned` 1 条 + `task.dropped` 1 条（objective/reason） | 其余任务正常完成（检索执行），整轮 completed |

函数级单测（test_plan_capability.py）：expected_kind 三态（编号/关键词/双命中与都不命中）+ validate_tasks 改派与 final 丢弃两口径。

## query_with_findings 确定性拼接证明（审查 B4）

- `test_second_hop_is_deterministic_over_finding_order`：同一 findings 集合正序/倒序/含重复条目，产出同一查询文本——提取值进 `set` 后 `sorted` 拼接，顺序无关。
- 集成测试证明第二跳查询构造可观察：`test_doc_search_second_hop_carries_findings` 从检索夹具的调用记录断言第二跳查询携带 `pay_bg_001_0003`（编号来自第一轮 read_document 片段正文，经片段池提取）。
- 超长清洗：`test_second_hop_value_capped_at_500_chars` 断言拼接后 ≤500 字符。
- 无第一跳结论：`test_second_hop_without_findings_is_noop` 断言原样返回（无 findings 或 findings 无编号）。

## 测试与质量

- US4/US5 相关 111 用例全过；宿主子集全量 403 过 + 8 失败——失败全部为预存 Windows 环境问题（GBK 解码、symlink 权限 WinError 1314、文件权限位、fcntl 容器-only），与改动前基线一致，失败基线未滚动。
- ruff 全绿；basedpyright 对改动文件 0 errors。
- 适配：test_projection_optin 3 处 docs+支付错派措辞（新校验下会被改派，与用例本意的投影/用量机制无关）。

## Rebase 状态（停下报告）

`feature/009-harness-robustness`（integration）已推进到 T005–T008（US1/US2 代码，含 failures.py/state.py/settings.py + investigation.py 四处工具包装）。merge-tree 预检：**唯一冲突文件 `investigation.py`，唯一冲突块**在 `search_documents` 的 `rewritten` 与 `claim` 之间——对方插入 `check_chain(shared, "embedding")`（US1 链路禁用检查），我插入 `query_with_findings(...)` 拼接（US5），两边插入同一锚点。

**建议解法**（未执行，等 Ban 确认）：两段都保留，顺序为「拼接 → check_chain」或「check_chain → 拼接」均可——check_chain 只查禁用登记不改文本，拼接只改文本不发请求，语义无耦合；拼接在后可省一次对已禁用链路的无效文本加工。解除冲突后需重跑 US1 的 outage 测试 + US4/US5 测试确认两边契约同时成立。

另：宿主机 stash 栈里有一条残留 `stash@{0} WIP on feature/009-us45`（本会话中途误建、内容已完整恢复到工作区并提交，可直接 drop，不与其它 worktree 共享内容）。

## 遗留观察

- `enforce_capabilities` 在首轮改派后总是触发一次重计划调用（命令语义"改派 → 重计划 → 丢弃"串行的保守实现）；若 Ban 认为改派成功即应跳过重计划（省一次模型调用），可把重计划条件收紧为"改派复核不可行"场景——需要拍板，未擅改。
- `second_hop_audit` 只做了 source.request（sql 第一跳）编号审计；docs 第一跳编号在片段正文里，臂数据没有对应事件可读，抽查覆盖面以 sql 域为准。
