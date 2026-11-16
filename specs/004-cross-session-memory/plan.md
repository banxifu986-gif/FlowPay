# 实施计划：004 跨会话记忆 + 上下文管理硬化

架构定稿见 spec.md 与 ../plans（会话计划文件）。本文件记录落地路径与批次。

## 批次

| 批次 | 内容 | 提交 |
|---|---|---|
| P0 | US1 记忆链路（store/extract/manager 收尾）+ US2 键召回注入 + US3 回归收口 | feat（跨会话记忆 P0） |
| P1 | US4 语义兜底 + US5 reactive compact | feat（跨会话记忆 P1） |
| P2 | US6 briefing 封顶 + US7 查询键封顶 + actions 治理 | feat（跨会话记忆 P2） |
| Polish | US8 冒烟校准回填 + 003 加注 + tasks 勾选 | docs（004 收尾） |

每批次：容器 pytest 失败集合不超基线（13F/238P/3S @2026-11-15）+ make quality。

## 分层接线（对齐 AGENTS.md）

- `core/memory/`：contracts（策略与数据结构）/ store（唯一碰 sqlite3 处）/ extract（纯函数）/ recall（键匹配 + 语义兜底 + 事件编排）。不依赖 workflows/router/bootstrap。
- `core/execution/`：RuntimeResources 持有 MemoryStore（生命周期 from_settings/close）；AgentRuntime 增可选 `memory: MemoryRecall | None`；SessionManager._execute 收尾抽取。
- `workflows/`：只经能力接口调用（manual_loop briefing 拼接处 / multi_agent graph case_note）；禁 import sqlite3/psycopg。
- `bootstrap.py` 装配 MemoryPolicy；`router.py` create_stage 签名扩展。

## 测试策略

- memory 纯函数（extract/condense/cap_query_keys/memory_section）→ MemoryStore（tmp_path）→ TracedMemoryRecall（假 EmbeddingClient）→ workflow 注入（FIFO 桩，对齐 test_agent_compaction 手法）→ reactive compact（桩抛 ModelHTTPError 各分支）。
- 真机冒烟 scripts/smoke_004_memory.py：A 写入→B 新会话键召回→C 语义兜底→D 诱导 400 实测补救。

## 风险

- 供应商 400 超限形态启发式：漏判=维持现状，误判=一次多余压缩+重试，均无害。
- reactive compact 需 openai_chat.py 把 APIStatusError 转为携带 status_code/body 摘要的 RuntimeError 子类（现有 except Exception 零改动）。
- graph.py:219 `{**turn.saved_state}` 透传遗留字段：workflow identity 隔离会话，现实不可达，data-model 注明即可。
