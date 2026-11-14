# Implementation Plan: 上下文管理——LLM 摘要式压缩、直连节点护栏与会话内记忆治理

**Branch**: `feat/003-context-management`（implement 开始前创建）
**Spec**: [spec.md](./spec.md)
**Date**: 2026-11-14

> 说明：项目未初始化 spec-kit CLI（无 setup 脚本与模板），本计划按 002 先例手动生成。
> 批准计划见 `C:\Users\99394\.claude\plans\langgraph-pi-d-coding-pi-main-steady-abelson.md`（2026-11-14 独立审核修正与 clarify 决议均已回填），本目录是它的设计工件沉淀。

## Summary

在 core 层为长排查会话补齐上下文治理的三层能力（全部 opt-in，缺省关闭=现行为）：

1. **P0 LLM 摘要式压缩**：`AgentTurn.think()` 入口检测原生消息流超触发线时，把切点前的历史轮次压缩为一条排查域五段摘要，以中段 system 消息形态持久化进检查点消息流（写路径瘦身）；002 投影继续做请求前兜底（读路径不变）。信封 record_ids 由代码确定性提取；迭代合并防信息丢失。
2. **P0 直连节点护栏**：multi-agent 五个直连调用点（route/plan/synthesize/doc_qa/chitchat）的变量段经 `clip_text` 体积裁剪，格式模板留在调用点，policy 未配置时零改动。
3. **P1 子 agent 输出预算与失败证据**：worker 报告产出端预算截断；故障/预算耗尽产出说明性报告。
4. **P2 会话内记忆治理**：跨轮 findings 合并封顶、evidence 淘汰。

推迟项（范围外）：检查点静态摘要封顶（待 P0 真机体积数据）、recall 回查工具（待 P0 冒烟压伤证据）。

## Technical Context

| 项 | 值 |
|---|---|
| 语言/运行时 | Python 3.12+，uv 管理依赖（backend/） |
| 新增依赖 | **无**（字符数近似延续 002 FR-9；压缩用当前会话同模型） |
| 压缩接入点 | `AgentTurn.think()`（turn.py:132，两阶段唯一带投影的模型请求入口）；仅 remember=True 生效（当前即 manual-loop，worker remember=False 由投影覆盖） |
| 摘要消息形态 | 中段 system 消息 + JSON 信封（投影可删组只含 assistant+tool，中段 system 天然存活；`MessageLog.restore` 零改动；briefing 先例 manual_loop.py:360） |
| 直连接入点 | graph.py:100（route）、investigation.py:245/423（plan/synthesize）、simple.py:42/72（doc_qa/chitchat）——格式各异已逐一核实 |
| 检查点语义修订 | supersedes 002 FR-8：检查点存压缩后消息流；事件日志 append-only 不变 |
| 测试 | pytest + 自研 fixtures（completion()/Model）；权威口径 Linux 容器跑全量；纯函数按 test_projection.py 风格、编排按 test_projection_optin.py 的 FIFO 受控 Model 风格 |
| 质量门禁 | `make quality`（ruff + basedpyright + pytest + 前端构建） |

## Constitution Check（依据 AGENTS.md 约定）

| 约定 | 结论 |
|---|---|
| workflows 只写本阶段编排/提示词/业务决策 | ✅ 直连节点只透传 policy、裁剪变量段；压缩纯函数与编排全在 core |
| workflows 不实现底层 | ✅ 不新增 SDK/存储操作 |
| core 不依赖 workflows/router/bootstrap/scripts | ✅ compaction/clip_text 是 core/chat 纯函数；test_boundaries 强制 |
| 公共方法类型声明完整，不退回 dict[str, Any] | ✅ 摘要信封/ContextPolicy/治理事件均强类型，供应商边界保留 JSON |
| 公共导出只在 __init__.py | ✅ 新公共名经 core.chat __init__ 导出 |
| 全局准则「最少代码」 | ✅ 审核砍除 token 触发线；slim/recall 推迟；不引 tokenizer |

无违规。

## Design Summary

（数据模型与接口细节见 [data-model.md](./data-model.md) 与 [contracts/interfaces.md](./contracts/interfaces.md)；决策记录见 [research.md](./research.md)）

### 压缩链（P0-1，两层结构）

**纯函数层**（`core/chat/compaction.py`，按 test_projection.py 风格单测）：
- `is_summary_message(message)`：判定 JSON 信封（`kind=="session_summary"`）；解析失败按普通 system 处理
- `select_compaction_range(messages, trigger_chars)`：返回待压缩整组下标（可删组定义复用投影：assistant+尾随 tool；保留最近 2 个完整交换 + 全部 user + 首条 system + 既有摘要 + 最后一段交换）
- `summary_message(...)`：组装信封（record_ids 由代码从被删消息提取）+ 合并 prompt 构造（既有摘要作 `<previous-summary>`）
- `compact_messages(messages, summary)`：替换而非追加——新摘要插回旧摘要位置（或压缩区间起点，取更靠前），流内至多一条

**编排层**（`turn.py` 内 `_compact_if_needed()`，think() 入口、投影之前）：
- 触发：`compaction_trigger_chars > 0` 且原生流超线且存在可压缩组；压缩目标 ≈ 触发线 60%（headroom 防多轮抖动）；correction 重试循环内不触发
- 摘要调用经 `chat.call(..., output="internal")`：不 spend_model_calls、usage 照计入账；超限正文代码截断（clarify 决议 B）
- 全函数：网络/输出不合规/摘要为空 → 跳过 + trace `context.compacted`/`context.compaction_failed`，投影兜底，绝不阻断
- 门禁：`self.remember` 显式判断
- `known_record_ids` 扩扫：`is_summary_message` 判真的 system 消息纳入编号来源（只读信封结构化字段）；disputed 判定规则不变

### 直连护栏链（P0-2）

- `clip_text(text, budget, marker)`：超限尾部截断 + 显式标记（含截断字符数与「可按记录编号重查」提示）
- 格式 f-string 留在五节点调用点；policy 存在时仅对变量段（plan 简报、synthesize 简报+每份报告、doc_qa 检索片段）调 `clip_text`；question 永不截断；policy=None 零改动=逐字一致天然成立
- synthesize 另加每份报告读取端上限（4096，P1 起迁入 policy）

### P1 子 agent 输出

- `report_char_budget`（policy 字段，0=不截断）：`investigate()` 返回前对报告文本 `clip_text`
- except Exception（CancelledError 穿透）转错误报告（error_type/message/任务目标）；无报告退出按 findings 切片产出预算说明报告
- claim 拒绝话术细化为「换更精确条件重查」

### P2 记忆治理

- findings 合并（`FINDINGS_CAP=20` 常量）：超限最旧合并为一条（target 拼接保 discard_stale 子串匹配、rows 求和、digest 拼接截断）；checkpoint 组装前调用
- `PassageSet.prune(max_passages, max_chars)`：恢复时按登记序保最新；当轮 evidence（前端展示）不淘汰

### 002 修订（supersedes）

检查点从「存未压缩原生消息」修订为「存压缩后消息流」。波及 002 工件 10 处（清单见 spec「对 specs/002 的修订」节），实现时逐处加 supersedes 标注；修订理由预写：「002 以确定性重算为由拒绝持久化投影输出；003 改写的是检查点消息流而非投影输出，投影对给定输入仍确定——理由不是被推翻而是边界移动」。tasks.md T024 已完成仅加注。

## Project Structure

```
backend/src/flowpay/core/
├── chat/
│   ├── compaction.py        # 新增：纯函数层（is_summary_message/select_compaction_range/
│   │                        #   summary_message/compact_messages）+ 摘要与合并 prompt 常量
│   ├── projection.py        # ContextPolicy 扩展字段；clip_text 纯函数
│   └── __init__.py          # 公共导出更新
├── agent/
│   └── turn.py              # _compact_if_needed 编排接入；known_record_ids 扩扫摘要信封
├── knowledge/
│   └── passages.py          # prune(max_passages, max_chars)（P2）
├── config/
│   └── settings.py          # 治理 opt-in 键（缺省 0/关闭 = 现行为）
└── （bootstrap 装配 policy 透传，router 不变）
backend/src/flowpay/workflows/
├── manual_loop.py           # 无需改动（policy 透传既有；compaction 对 remember=True 自动生效）
└── multi_agent/
    ├── graph.py             # 五节点 context_policy 透传；P2 findings 合并调用
    ├── investigation.py     # 直连变量段裁剪；P1 报告预算与失败证据报告
    ├── simple.py            # doc_qa/chitchat 变量段裁剪
    └── state.py             # P2 findings 合并纯函数
backend/tests/
├── chat/test_compaction.py          # 新增：信封/切点/存活/合并/失败/编号六类
├── chat/test_projection.py          # 扩：clip_text 用例
├── workflows/test_agent_compaction.py  # 新增：关闭逐字一致/开启压缩/checkpoint 跨轮兼容
├── workflows/test_projection_optin.py  # 扩：直连节点 opt-in
└── workflows/test_multi_agent.py    # 扩：P1 失败证据、P2 findings 合并
scripts/
└── smoke_003_context.py     # 新增：六轮长会话冒烟 + 压缩体积统计
specs/003-context-management/quickstart.md  # 冒烟校准数据回填处
specs/002-usage-projection/                 # supersedes 标注 10 处
```

## Implementation Phases

1. **Phase A（P0 压缩纯函数）**：compaction.py + test_compaction.py 六类用例先行（全红→全绿）
2. **Phase B（P0 编排接入）**：think() `_compact_if_needed` + known_record_ids 扩扫 + settings/bootstrap 配置链；`test_agent_compaction.py`（关闭逐字一致/开启压缩/跨轮兼容）；缺省全量回归逐字一致
3. **Phase C（P0 直连护栏）**：clip_text + 五节点变量段接入 + 报告读取端上限；optin 用例扩
4. **Phase D（P0 验收）**：`smoke_003_context.py` 真机冒烟（必做，校准触发线/摘要上限）+ analyze_smoke 数据回填 quickstart + `make quality` + 批次提交
5. **Phase E（P1）**：报告产出端预算 + 失败/耗尽证据报告 + claim 话术；测试先行；容器回归 + 冒烟尽力
6. **Phase F（P2）**：findings 合并 + PassageSet.prune；测试先行；容器回归 + 冒烟尽力
7. **Phase G（收尾）**：002 supersedes 标注 10 处落盘 + `make quality` 全绿 + 容器基线口径不扩大

任务分解见 [tasks.md](./tasks.md)（待 `/speckit-tasks` 生成）。
