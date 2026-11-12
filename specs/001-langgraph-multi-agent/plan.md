# Implementation Plan: LangGraph 多 Agent 编排（意图路由 + 并行协作排查）

**Branch**: `feat/langgraph-s5`（实施开始时创建）
**Spec**: [spec.md](./spec.md)
**Date**: 2026-11-12

> 说明：项目未初始化 spec-kit CLI（无 setup 脚本与模板），本计划按标准模板手动生成。
> 已批准的技术方案细节见计划文件（compressed-churning-stearns.md），本目录是它的需求侧沉淀与设计工件。

## Summary

在 FlowPay 后端引入 LangGraph 接管轮内编排：每轮自动意图路由（排查/文档问答/闲聊），排查场景由 supervisor 拆解、SQL 与文档调查员经 Send API 并行执行、汇总合成答案；SessionManager 继续负责跨轮持久化（saved_state 通道不变，不引入 LangGraph checkpointer）；现有阶段 `s4` 更名 `manual-loop`，新阶段注册为 `multi-agent` 并设为默认。

## Technical Context

| 项 | 值 |
|---|---|
| 语言/运行时 | Python 3.12+，uv 管理依赖（backend/） |
| 后端框架 | FastAPI + uvicorn；SQLite 事件溯源（append-only） |
| 新增依赖 | `langgraph>=0.4,<0.6`（锁定小版本区间，防 Send/conditional edge API 漂移） |
| 模型接入 | 自研 ChatModel（OpenAI 兼容协议），单实例装配 |
| 嵌入检索 | qwen3-embedding:0.6b（compose ollama 服务，端口必须 11434） |
| 测试 | pytest + 自研 fixtures（Model FIFO mock、Search mock）；`make test` / `uv run pytest backend/tests` |
| 质量门禁 | `make quality`（ruff + basedpyright + pytest + 前端构建） |
| 容器 | pgvector/pgvector:0.8.2-pg17、ollama/ollama:0.12.3（镜像已在本地，无需拉取） |

## Constitution Check（依据 AGENTS.md 约定）

| 约定 | 结论 |
|---|---|
| workflows 只写本阶段编排/提示词/业务决策 | ✅ multi_agent 包只含图编排与节点逻辑 |
| workflows 不实现 HTTP/SSE/SQL/文件读取等底层 | ✅ 经 AgentRuntime 能力接口访问 |
| core 不依赖 workflows/router/bootstrap/scripts | ✅ 不动 core 依赖方向；边界由 tests/core/test_boundaries.py 强制 |
| 公共方法声明输入返回类型，已知结构不退回 dict[str, Any] | ✅ GraphState/SharedTurn/各实体均为强类型；saved_state 是既有 JSON 边界，可保留 |
| 公共导出只在 __init__.py | ✅ multi_agent/__init__.py 只导出 MultiAgentWorkflow |

无违规。**风险提示**（非违规，实施时须遵守）：langgraph 同步节点走线程池会破坏共享 Budget/done 的原子性——所有节点必须 `async def`；`workflows` 内部互相 import 复用 manual_loop 不违反边界。

## Design Summary

图结构（详细设计见 data-model.md 与 contracts/）：

```
START → route ─┬─ troubleshoot → plan ──Send──> sql_worker ─┐
               │                └──Send──> doc_worker ──────┤→ synthesize → END
               ├─ docs        → doc_qa ────────────────────┘(汇入 answer)
               └─ chitchat    → chitchat → END
```

关键决策（依据 research.md）：轮内编排无 checkpointer；意图结构化输出走 set_intent 工具；claim 提为模块级纯同步临界区函数；Budget(16,12) 全体共享、plan 按 task 数 extend；waiting_user 经 pending 置位短路 worker 循环实现；findings 结构化摘要解决跨轮去重与上下文的矛盾。

## Project Structure

```
backend/src/flowpay/workflows/
├── manual_loop.py          # 由 s4.py 改名；类 S4Workflow → ManualLoopWorkflow
└── multi_agent/
    ├── __init__.py         # 导出 MultiAgentWorkflow
    ├── state.py            # GraphState、SharedTurn、实体类型
    ├── intent.py           # 规则短路 + set_intent + 兜底
    ├── simple.py           # chitchat / doc_qa 节点
    ├── investigation.py    # plan / workers / synthesize / claim
    └── graph.py            # build_graph()、MultiAgentWorkflow
```

## Implementation Phases

1. **Phase A（无行为变化）**：建分支、加依赖、s4→manual_loop 命名落地，全量测试绿
2. **Phase B（骨架）**：multi_agent 包 + 桩节点 + router 注册，桩级冒烟测试
3. **Phase C（意图）**：intent.py + fixtures `intent_call`
4. **Phase D（轻分支）**：simple.py
5. **Phase E（排查）**：investigation.py + saved_state 组装
6. **Phase F（追问）**：ask_user/pending + waiting_user
7. **Phase G（切默认）**：settings/cli/bootstrap/Makefile 遗留序号清理 + 受影响测试同步
8. **Phase H（验收）**：质量门禁 + 手工冒烟

任务分解见 [tasks.md](./tasks.md)（已生成并全部完成，2026-11-12）。
