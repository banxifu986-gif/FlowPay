# 数据模型：004

## memory.sqlite（{data_dir}/memory.sqlite）

单表 `memory_notes`：

| 列 | 类型 | 语义 |
|---|---|---|
| note_id | TEXT PK | uuid4 hex |
| session_id | TEXT | 来源会话（不与 delete_sessions 联动） |
| workflow | TEXT | 来源 workflow.id（"flowpay-multi-turn-rag" / "flowpay-multi-agent-support"） |
| created_at | TEXT | ISO8601 UTC |
| keys_json | TEXT | 结构化键 JSON 数组（记录编号/case 值，casefold 归一） |
| conditions_json | TEXT | 已知条件 JsonObject |
| body | TEXT | 三段正文（条件/结论摘要/最终回答），超 NOTE_BODY_CAP 截断 |
| embedding | TEXT NULL | JSON 数组（qwen3-embedding 向量空间），仅 semantic_limit>0 时写入 |
| body_chars | INTEGER | 截断后长度 |

写路径单事务：同键全等替换（keys 集合完全相等 → 删旧插新）+ 总量最旧淘汰。

## MemoryPolicy（frozen dataclass）

- `recall_char_budget: int` — >0 启用整链路；注入段落字符预算
- `semantic_limit: int = 0` — 语义兜底候选数；>0 需 embedding 配置
- `max_notes: int = 200` — 总量封顶

## MemoryNote / RecalledNote

- MemoryNote(note_id, session_id, workflow, created_at, keys: list[str], conditions: JsonObject, body: str, embedding: list[float] | None, body_chars: int)
- RecalledNote(note_id, body, score) — 键命中 score=1.0，语义=余弦分

## 事件（会话事件流，对齐「域.动作」）

| 事件 | 载荷 | 时机 |
|---|---|---|
| memory.extracted | note_id, keys 数, chars, workflow | 抽取落库成功 |
| memory.extract_skipped | reason | waiting_user / no_findings |
| memory.extract_failed | error_type, message | 抽取/落库异常（不阻断） |
| memory.recalled | note_ids, matched(key/semantic/none), chars | 召回完成 |
| memory.recall_failed | error_type, message | 召回异常（不阻断本轮） |
| memory.compacted | dropped, remaining | 总量淘汰发生 |
| context.reactive_compacted | before_chars, after_chars, rounds, trigger:"over_limit" | reactive 压缩成功 |

## 模块常量（不进配置面）

NOTE_BODY_CAP=4000、QUERY_KEYS_CAP=200、ACTIONS_CAP=50、MERGED_BRIEFING_CAP=4000、RECALL_LIMIT=3、RECALL_SEMANTIC_THRESHOLD=0.35（试点值，真机校准后回填 quickstart）。

## 注意

multi-agent 检查点组装 `{**turn.saved_state, ...}`（graph.py:219）会透传旧键——workflow identity 已隔离会话，现实不可达；键集以此处为准：case/queried/findings/evidence（multi-agent）、case/queried/actions（manual-loop）+ messages/evidence（turn.checkpoint 合入）。
