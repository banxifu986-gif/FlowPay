# 接口契约：004

## core/memory/contracts.py

```python
@dataclass(frozen=True)
class MemoryPolicy:
    recall_char_budget: int          # >0 启用整链路，0=关闭
    semantic_limit: int = 0          # 语义兜底候选数（>0 需 embedding）
    max_notes: int = 200             # 总量封顶

@dataclass(frozen=True)
class MemoryNote:
    note_id: str; session_id: str; workflow: str; created_at: str
    keys: list[str]; conditions: JsonObject; body: str
    embedding: list[float] | None; body_chars: int

@dataclass(frozen=True)
class RecalledNote:
    note_id: str; body: str; score: float

class MemoryRecall(Protocol):
    async def recall(self, keys: list[str], query: str, *, limit: int = 3) -> list[RecalledNote]: ...
```

## core/memory/store.py

```python
class MemoryStore:
    def __init__(self, path: Path, max_notes: int = 200) -> None: ...
    def save(self, note: MemoryNote) -> None: ...            # 同键替换+淘汰，单事务；返回淘汰数由调用方经事件核对
    def match_keys(self, keys: list[str], *, limit: int) -> list[MemoryNote]: ...
    def search_semantic(self, vector: list[float], *, limit: int) -> list[tuple[MemoryNote, float]]: ...
    def close(self) -> None: ...
```

## core/memory/extract.py

```python
def extract_note(session_id: str, workflow: str, saved_state: JsonObject, answer: str) -> MemoryNote | None:
    """确定性抽取；不满足存储条件返回 None。按 JsonObject 字段存在性适配两种 workflow。"""
```

## core/memory/recall.py

```python
class TracedMemoryRecall:  # MemoryStore + EmbeddingClient | None + Tracer
    async def recall(self, keys: list[str], query: str, *, limit: int = 3) -> list[RecalledNote]:
        """键精确匹配优先；未命中且配置了嵌入则余弦 top-k（阈值过滤）；发 memory.recalled/recall_failed。"""
```

## 接线签名变化

- `core/execution/runtime.py::AgentRuntime` + `memory: MemoryRecall | None = None`
- `core/execution/resources.py::RuntimeResources` 持有 `memory_store: MemoryStore | None`，close 时关闭
- `router.py::create_stage / StageFactory` + `memory_policy: MemoryPolicy | None`
- `core/chat/projection.py::ContextPolicy` + `briefing_cap: int = 0`（P2）
- `core/llm/chat/openai_chat.py` 新异常 `ModelHTTPError(RuntimeError)`（status_code, body_excerpt）（P1）

## workflows 消费点

- manual_loop：briefing() 拼接处追加记忆段（召回键 = user_message 编号 + case.known.values()）
- multi_agent/graph：case_note 拼记忆段（单点注入携带至 plan/worker/synthesize）
