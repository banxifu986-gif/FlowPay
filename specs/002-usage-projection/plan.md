# Implementation Plan: 模型用量聚合与请求前上下文投影（pi 思想借鉴）

**Branch**: `feat/002-usage-projection`（已创建）
**Spec**: [spec.md](./spec.md)
**Date**: 2026-11-14

> 说明：项目未初始化 spec-kit CLI（无 setup 脚本与模板），本计划按标准模板手动生成。
> 已定稿的分析计划见 `C:\Users\99394\.claude\plans\pi-agent-snuggly-floyd.md`（2026-11-14 四项决议已回填），本目录是它的设计工件沉淀。

## Summary

在 core 层落地两个纯函数能力并接线：①**用量聚合**——从模型响应提取 usage（容错缺失），按轮聚合成 UsageSummary，随 TurnResult 与 `message.assistant` 事件交给前端；②**请求前上下文投影**——在 `AgentTurn.think()` 唯一模型请求入口，把原生消息按字符数近似预算投影为实际发送的消息（旧工具结果占位替换 + 最老完整轮次整组删除兜底），四条硬性不变量保证配对完整、关键消息存活、编号可核查、全函数不抛异常。顺带修复 seen 重建缺口：恢复检查点时把历史轮工具真实返回过的编号并入 seen。默认策略 None = 现行为，manual-loop 与 multi-agent 同轮 opt-in。

## Technical Context

| 项 | 值 |
|---|---|
| 语言/运行时 | Python 3.12+，uv 管理依赖（backend/） |
| 新增依赖 | **无**（字符数近似，不引 tokenizer） |
| 用量数据链 | `openai_chat.py` 已请求 `stream_options.include_usage`；`stream.py` 聚合 usage（缺失 → completion.usage 为 None）；`client.py` 把完整 completion dump 进 `model.response` 事件 payload |
| 前端数据链 | `manager.py` 原子提交 `message.assistant`（content+evidence）+ `run.finished`（status+state）；`session_view.py` 投影为 ChatMessage |
| 投影接入点 | `AgentTurn.think()`（turn.py:124，两阶段唯一模型请求入口） |
| Worker 并行 | multi-agent 经 LangGraph Send 并行，每 worker 独立 AgentTurn/MessageLog，无共享消息状态 |
| 测试 | pytest + 自研 fixtures（completion()/Model）；权威口径 Linux 容器跑全量（Windows 宿主机 fcntl 限制） |
| 质量门禁 | `make quality`（ruff + basedpyright + pytest + 前端构建） |

## Constitution Check（依据 AGENTS.md 约定）

| 约定 | 结论 |
|---|---|
| workflows 只写本阶段编排/提示词/业务决策 | ✅ 两阶段只透传策略、汇总 usage，不实现聚合/投影逻辑 |
| workflows 不实现 HTTP/SSE/SQL 等底层 | ✅ 不涉及 |
| core 不依赖 workflows/router/bootstrap/scripts | ✅ usage_view/projection 是 core/chat 纯函数；test_boundaries 强制 |
| 公共方法声明输入返回类型，不退回 dict[str, Any] | ✅ UsageSummary/ContextPolicy/占位摘要结构均强类型 |
| 公共导出只在 __init__.py | ✅ 新公共名经 core.chat / core.agent 的 __init__ 导出 |

无违规。

## Design Summary

（数据模型与接口细节见 [data-model.md](./data-model.md) 与 [contracts/interfaces.md](./contracts/interfaces.md)）

- **用量链**：`from_completion(completion)` 提取单次 UsageEntry（缺失字段容错为 None，不按 0 补齐），`sum_entries` 聚合；AgentTurn 在 think() 每次拿到 completion 后累计（含被纠正循环拒绝的调用——费用已发生），accept/pause 写入 TurnResult.usage；multi-agent 由 SharedTurn 收集各 agent 的 entries，graph.run 汇总；manager 提交时写进 `message.assistant` payload 的 `usage` 键；session_view 的 ChatMessage 增加 usage 字段（缺键宽容）。
- **投影链**：`ContextPolicy`（字符预算等；None = 现行为）从 settings 读入，两阶段透传给 `AgentTurn.start`；think() 发请求前组装为 `[*project_messages(messages, policy), *correction, *closing]`——投影只作用于检查点原生消息，纠正草稿与收尾系统消息永不投影。投影两层机制（research R-1）：超限旧工具结果先占位替换（role=tool、tool_call_id 原样，配对不破），仍超限则整组删除最老完整轮次（assistant + 尾随 tool 一起走，绝不拆对）；不可删集合：首条 system、任何 user、最后一段 assistant 交换。检查点永远存原生消息，投影每轮确定性重算。
- **seen 修复**：恢复检查点时，seen 重建除 question + user 消息编号外，扫描历史 tool 消息内容把工具真实返回过的编号并入；assistant 历史消息仍不计入；disputed() 判定规则不动。

## Project Structure

```
backend/src/flowpay/core/
├── chat/
│   ├── usage_view.py        # 新增：UsageEntry/UsageSummary + 提取与聚合纯函数
│   ├── projection.py        # 新增：ContextPolicy + project_messages（全函数）
│   ├── __init__.py          # 公共导出更新
│   └── session_view.py      # ChatMessage 增加 usage 字段（缺键宽容）
├── agent/
│   ├── turn.py              # start 加 context_policy；think() 投影接入与 usage 累计；seen 重建修复
│   └── __init__.py          # 导出更新（ContextPolicy 归属包按就近原则定）
├── execution/
│   └── contracts.py         # TurnResult.usage 字段
└── bootstrap/settings       # 投影 opt-in 配置键（默认关闭 = 现行为）
backend/src/flowpay/workflows/
├── manual_loop.py           # 策略透传
└── multi_agent/
    ├── state.py             # SharedTurn 收集 usage entries
    ├── investigation.py     # worker 策略透传
    └── graph.py             # 汇总 usage 写入 TurnResult
backend/tests/
├── chat/test_usage_view.py  # 新增：提取容错、聚合
├── chat/test_projection.py  # 新增：四不变量、长会话场景
└── workflows/fixtures.py    # completion() 可选 usage；Model 多 chunk/参数分片
```

## Implementation Phases

1. **Phase A（无行为变化）**：fixtures 增强（P3）：completion() 加可选 usage、Model.invoke 加可选多 chunk/参数分片；既有测试全绿
2. **Phase B（聚合核心）**：usage_view.py + 单测（单次提取容错、聚合、缺失 None 不按 0）
3. **Phase C（用量接线）**：AgentTurn 累计 → TurnResult.usage → manager payload → session_view；两阶段汇总；契约测试
4. **Phase D（投影核心）**：projection.py + 四不变量单测（配对完整/存活集合/占位编号清单/畸形输入原样返回）
5. **Phase E（接入与修复）**：think() 投影接入 + start 的 context_policy + settings 开关（默认关）；seen 重建修复 + 跨轮引用回归测试
6. **Phase F（两阶段 opt-in）**：manual_loop/multi_agent 装配策略；LangGraph Send 并发隔离测试；长会话场景测试（体积受控 + disputed 仍正确拦截）
7. **Phase G（验收）**：质量门禁 + 容器全量（既有失败集合不得扩大）

任务分解见 [tasks.md](./tasks.md)（待 `/speckit-tasks` 生成）。
