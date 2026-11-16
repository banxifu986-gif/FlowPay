"""记忆召回编排：结构化键精确匹配优先，语义相似度兜底；失败不阻断本轮（specs/004）。

召回是尽力而为的增强能力：任何异常都降级为「本轮无记忆」，发 recall_failed
事件后返回空列表——排查链路不因记忆子系统故障而失败。
"""

from __future__ import annotations

import asyncio

from flowpay.core.agent.trace import Tracer
from flowpay.core.llm.embedding import EmbeddingClient

from .contracts import MemoryNote, RecalledNote
from .store import MemoryStore

# 语义兜底的最低余弦分数（试点值，真机校准后回填 specs/004 quickstart.md）。
RECALL_SEMANTIC_THRESHOLD = 0.35


class TracedMemoryRecall:
    """MemoryRecall 的默认实现：键匹配优先，键未命中且配置了嵌入时语义兜底。"""

    def __init__(
        self,
        store: MemoryStore,
        tracer: Tracer,
        embedder: EmbeddingClient | None = None,
        semantic_limit: int = 0,
    ):
        """保存存储、本轮追踪与可选嵌入客户端；semantic_limit=0 时不发生嵌入调用。"""
        self._store: MemoryStore = store
        self._tracer: Tracer = tracer
        self._embedder: EmbeddingClient | None = embedder
        self._semantic_limit: int = semantic_limit

    async def recall(self, keys: list[str], query: str, *, limit: int = 3) -> list[RecalledNote]:
        """按结构化键精确匹配召回；未命中且配置了嵌入则按余弦分数兜底。

        实现不抛异常：失败发 memory.recall_failed 后返回空列表，本轮照常进行。
        """
        normalized = sorted({key.casefold() for key in keys if key.strip()})
        try:
            notes = await asyncio.to_thread(self._store.match_keys, normalized, limit=limit)
            matched = "key" if notes else "none"
            if not notes and self._embedder is not None and self._semantic_limit > 0:
                scored = await self._semantic(query)
                notes = [note for note, score in scored if score >= RECALL_SEMANTIC_THRESHOLD]
                matched = "semantic" if notes else "none"
            await self._tracer.emit(
                "memory.recalled",
                {
                    "note_ids": [note.note_id for note in notes],
                    "matched": matched,
                    "chars": sum(len(note.body) for note in notes),
                },
            )
            return [_recalled(note) for note in notes]
        except Exception as error:
            await self._safe_emit(
                "memory.recall_failed",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            return []

    async def _semantic(self, query: str) -> list[tuple[MemoryNote, float]]:
        """把查询嵌成向量后做余弦检索；嵌入与检索的异常由 recall 统一降级。"""
        assert self._embedder is not None
        batch = await self._embedder.embed([query])
        vector = batch.vectors[0]
        return await asyncio.to_thread(
            self._store.search_semantic, vector, limit=self._semantic_limit
        )

    async def _safe_emit(self, kind: str, payload: dict[str, object]) -> None:
        """失败事件的记录本身也可能失败（存储不可用）；不能再向上抛。"""
        try:
            await self._tracer.emit(kind, payload)
        except Exception:
            pass


def _recalled(note: MemoryNote) -> RecalledNote:
    """键命中的召回分数固定 1.0（语义路径另有余弦分数）。"""
    return RecalledNote(note.note_id, note.body, 1.0)
