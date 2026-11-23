# 010 端到端实测报告：逐断言校验在线路径

日期：2026-11-23 · 分支 `feature/010-assertion-verification`（ad0af60）· 交互实测，代码零修改

## 环境形态

- 全栈容器（compose）：backend :8000、frontend :5173、postgres :55432、ollama。非 `make run` 宿主机形态（本机 Windows 无 make，且 `scripts/setup.py` 依赖 `fcntl` 无法在 Windows 宿主机执行）。
- 后端容器启动需 shell 环境注入：`STAGE=multi-agent FLOWPAY_AUTH_TOKEN_SECRET=<secret> docker compose up -d --force-recreate backend`。原因见问题 E-1/E-2。
- 开关切换：编辑 `backend/configs/local.toml` 的 `assertion_audit_enabled` 后 `--force-recreate` 重启 backend 容器（bind mount 代码与配置，容器内已确认加载，`run.started` 的 config 快照显示 `assertion_audit_enabled=true`）。
- 登录：merchant_demo / internal_demo（accounts.toml 实测期间重 seed，口令临时值，商户绑定 acct_merchant_demo 未变）。
- 事件观察：`/api/sessions/{id}/export` 导出 JSONL + 后端 docker logs；截图与事件归档在 `e2e-assets/`。

## 三阶段结果

### A. 基线（开关关闭）— 通过

- 会话 `session_dc91541616084218bcc7387c7c4b940a` 两轮（FP-4012 排查、Webhook 重试策略），完整导出 1322 条事件：**`assertion.audit` = 0**，事件类型集合与 009 口径一致（SC-1 在线确认）。
- 第二问回答正常带引用（`[doc:KB-API-032#c002]` 等），前端渲染正常。截图 `A-baseline-webhook-answer.png`。
- 第一问走了 `task.budget_exhausted` 降级占位回答（既有行为，非本轮引入）。

### B. 开启态 — 部分通过（校验生效但有结构性缺口）

- **synthesize 路径（troubleshoot 意图 → plan → workers → synthesize）校验生效**：会话 `session_aea93fcd5a43421499653d973929dd49`（问 pay_bg_001_0003 投递状态）产出真实判定：
  - `assertion.audit` 事件：verdicts=1（`insufficient`）、actions=1（`downgrade`，revision=「未能核实：支付 `pay_bg_001_0003`…retrying。」）、fail_open=false、text=发布文本（与 `message.assistant` 逐字一致，P2-1 口径成立）。归档 `B5-audit-event.json`。
  - 发布文本带「未能核实：」前缀，前端正常渲染，不崩。截图 `B-enabled-audit-downgrade.png`。
  - 抽取调用出现在 model.request 事件流（第 8 次调用），轨迹用量显示「8 次模型调用」已计入。
- **docs 意图直答路径完全绕过校验**：问「Webhook 的重试策略是什么？」（会话 `session_1850f4e126b749a3be86d58b751aca09`）与开启态追问「那这条投递接下来还会重试吗？」（run_f9c4daa2）均路由 `intent=docs` → `doc_qa`，2 次模型调用直接发布，**零 assertion.audit 事件**（见问题 P1-1）。
- **完整排查轮的 audit 全部 fail-open**：FP-4012 复问与「有笔支付成功但没收到通知」完整排查（run_57a018f9）audit 事件 fail_open=true、0 verdicts；时间线证明校验器未被调用（gate 走预算耗尽分支，见问题 P1-2）。
- clarification 轮（mode=clarify）按设计零事件、零校验调用，会话不崩。

### C. 多轮与追问 — 通过

- 开启态会话追问一轮（run_f9c4daa2）：会话不崩、前端事件投影正常（新事件类型 assertion.audit 到达时前端未阻断，B5 轮已在线验证）。
- 追问轮（waiting，mode=clarify）不发 `assertion.audit`：多轮实测一致（B2/B3/B4 的 clarify 轮均为 0 audit），与 spec「澄清追问不校验」一致。
- 注意：追问被路由成 docs 意图时是**作答轮**却零校验（归入 P1-1 的缺口，见下）。

## 问题清单

### P0（阻断）— 无

### P1（应修）

- **P1-1 docs 直答路径绕过断言校验门**。`graph.py` 中 `doc_qa_node`（`simple.py:50`）与 `chitchat_node` 不接收 `assertion_gate`，docs 意图的作答不经 gate 直接发布，开启态零校验零事件。spec FR-7/US1 要求「每轮作答类回复都校验」，澄清口径只豁免追问/worker 报告/单 agent。影响面：所有知识问答类流量（本实测 5 轮中 3 轮命中）。
  - 复现：开关开启 → 新会话问「Webhook 的重试策略是什么？」→ 导出事件，`assertion.audit`=0。
  - 佐证：同口径的 troubleshoot 路径同会话形态下 audit 事件正常出现（B5）。
- **P1-2 在线判定链路对完整排查轮不可达（预算耗尽 fail-open 常态化）**。gate 接线点在 synthesize 决策调用之后，`budget_exhausted=not shared.budget.may_think()`（`investigation.py:1306`）。`SharedTurn.budget = Budget(16, 12)`（`state.py:141`，model_calls=16），worker 循环消耗同一预算；实测完整排查轮 worker 恰好用满 16 次模型调用 → gate 必走预算分支 fail-open（run_57a018f9：audit fail_open=true、0 verdicts，最后一个 model.request 是 set_response 决策调用，抽取/校验调用未发生）。spec 字面上允许「预算耗尽跳过校验」，但现状使**正常判定只在轻量问题可达**（B5 之类 worker 只花 5-8 次的轮），重型排查（最需要校验的场景）实际零校验。
  - 复现：开启态 → 问 FP-4012 排查 → 走完追问轮 → 最终作答的 audit 事件 fail_open=true 且事件流里没有校验器的 model.request。
- **P1-3 记录摘录维度在线为空导致记录支撑类断言必然降级（在线误伤）**。`online.py` 装配 `record_excerpts={}`（文件头披露的已知局限），`verifier.py` 的 FR-3 代码级核对对绑定业务记录编号的断言一律 `excerpt-missing` → 强制 `insufficient`，不经语义校验。实测 B5：SQL 查询结果直接支撑的句子「生成 1 条投递 del_bg_001_0003，状态 retrying」被判 insufficient 并整句降级「未能核实」，追问轮同句再次降级。已披露为已知债，但在线后果是**凡是引用业务记录编号的回答几乎必然被降级**，开启态评测臂前需确认这是否可接受（SC-6 go/no-go 依据）。
  - 复现：开启态 → 问 B5 同题 → 发布文本出现两处以上「未能核实：」，audit verdict=insufficient 且无语义校验调用（tools=[] 的 model.request 只出现抽取一次）。

### P2（建议）

- **P2-a mode=clarify 且缺项为空时发布的正文是完整结论，却按追问轮豁免校验**。run（B4 会话第 3 轮）：`clarification.decided {"mode":"clarify","topics":[],"conditions":[]}`，发布正文是「## 结论 事件已生成 evt_bg_001_0003…」全文。用户拿到结论性作答但未经校验，且轮次停在等待态。建议：空缺项 clarify 收敛为 answer 或对正文过 gate。
- **P2-b gate 预算分支 fail-open 事件无原因字段**。`gate_response` 的预算耗尽/auditor 缺失分支发的 audit 载荷只有 fail_open=true，无 `reason`；`audit_response` 异常路径有 `logger.exception` 但预算分支无日志。回放侧无法区分「预算耗尽」与「装配缺口」。建议载荷加 reason 字段。
- **P2-c 演示商户账号与数据集不匹配（环境问题）**。merchant_demo 绑定 acct_merchant_demo，canonical 数据集无该账户（accounts 全为 acct_bg_*/acct_bm_*），商户态排查类问题业务查询必然空转，只能靠文档回答。验收商户态排查需换有数据的账号或补 demo 数据。
- **P2-d 后端容器形态的配置残留**（与 010 无关，环境债）：`.env` 中 `STAGE=s4` 残留导致 backend 容器启动即崩（`Unknown stage: s4`）；`FLOWPAY_AUTH_TOKEN_SECRET` 不在 `.env`，容器启动需手工注入。建议 .env 清理与文档对齐 006 quickstart。

## 测试后恢复

- `local.toml` 的 `assertion_audit_enabled` 已改回 `false` 并提交（注明实测后恢复）。代码零修改。
- accounts.toml 为实测期重 seed（口令临时），如需恢复原口令由 Ban 用 `scripts/accounts.py seed --force` 重设。

## 结论

开关机制、事件通道、发布处置与前端投影在线路径可用；但按实测流量形态，**开启态下多数作答（docs 直答、完整排查、记录支撑类断言）实际未经语义校验**。P1-1/P1-2 修复前，SC-6 开启态评测臂的结论将主要由「校验器基本不参与」的路径构成，建议先清偿再跑评测臂。
