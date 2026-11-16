"""TracedMemoryRecall：键匹配优先、语义兜底、失败降级与事件埋点。"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast, override

from flowpay.core.agent.trace import Tracer
from flowpay.core.event_log import EventStore
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.embedding import EmbeddedBatch, EmbeddingClient
from flowpay.core.memory.contracts import MemoryNote
from flowpay.core.memory.recall import RECALL_SEMANTIC_THRESHOLD, TracedMemoryRecall
from flowpay.core.memory.store import MemoryStore

SESSION = "session_mem"
RUN = "run_mem"


class FakeEmbedder:
    """测试替身：按预设顺序返回查询向量并记录调用次数。

    不继承 EmbeddingClient——父类绑定适配器与事件记录，替身只需要 embed。
    传给 TracedMemoryRecall 时经 object 中转 cast，绕开形状不同的类型检查。
    """

    def __init__(self, vectors: list[list[float]]) -> None:
        """保存预设向量序列，每次 embed 消费一个。"""
        self._vectors: list[list[float]] = vectors
        self.calls: int = 0

    async def embed(self, texts: list[str]) -> EmbeddedBatch:
        """逐次返回预设向量；测试自己保证输入与向量对齐。"""
        _ = texts
        self.calls += 1
        return cast("EmbeddedBatch", cast("object", SimpleNamespace(vectors=list(self._vectors))))


def as_embedder(fake: FakeEmbedder) -> EmbeddingClient:
    """替身进入召回器参数位：双 cast 是项目对形状不一致测试替身的通行做法。"""
    return cast("EmbeddingClient", cast("object", fake))


def make_store(path: Path) -> MemoryStore:
    """预置一条键与向量都能命中测试查询的记忆。"""
    store = MemoryStore(path)
    store.save(
        MemoryNote(
            note_id="note_seed",
            session_id="session_seed",
            workflow="manual-loop",
            created_at="2026-11-15T09:00:00+00:00",
            keys=["pay_bg_001_0003"],
            conditions={"known": {}},
            body="此前结论：投递被 410 拒绝。",
            embedding=[1.0, 0.0],
            body_chars=14,
        )
    )
    return store


def make_recall(
    tmp_path: Path,
    embedder: FakeEmbedder | None = None,
    semantic_limit: int = 0,
    store: MemoryStore | None = None,
) -> TracedMemoryRecall:
    """按测试需要装配召回器；事件写入真实 EventStore 以便断言埋点。"""
    tracer = Tracer(EventStore(tmp_path / "events.sqlite"), SESSION, RUN)
    return TracedMemoryRecall(
        store or make_store(tmp_path / "memory.sqlite"),
        tracer,
        as_embedder(embedder) if embedder is not None else None,
        semantic_limit,
    )


async def read_events(tmp_path: Path) -> list[tuple[str, JsonObject]]:
    """读回本会话全部事件，返回 (kind, payload) 列表。"""
    store = EventStore(tmp_path / "events.sqlite")
    return [(require_text(row["type"]), row["payload"]) for row in store.read(SESSION)]


async def test_key_hit_returns_note_and_emits_key_event(tmp_path: Path) -> None:
    """键精确命中：score=1.0，事件 matched=key 且带字符数。"""
    recall = make_recall(tmp_path)
    hits = await recall.recall(["PAY_BG_001_0003"], "支付 pay_bg_001_0003 还没收到通知")
    assert len(hits) == 1
    assert hits[0].note_id == "note_seed"
    assert hits[0].score == 1.0
    events = await read_events(tmp_path)
    assert events[-1][0] == "memory.recalled"
    payload = require_object(events[-1][1])
    assert payload["matched"] == "key"
    assert require_list(payload["note_ids"]) == ["note_seed"]


async def test_no_hit_without_embedder_reports_none(tmp_path: Path) -> None:
    """键未命中且未配嵌入：matched=none，零返回。"""
    recall = make_recall(tmp_path)
    hits = await recall.recall(["pay_other_9"], "另一个问题")
    assert hits == []
    events = await read_events(tmp_path)
    assert require_object(events[-1][1])["matched"] == "none"


async def test_semantic_fallback_on_key_miss(tmp_path: Path) -> None:
    """键未命中但语义相近：走向量兜底并按阈值过滤。"""
    embedder = FakeEmbedder([[0.9, 0.1]])
    recall = make_recall(tmp_path, embedder, semantic_limit=2)
    hits = await recall.recall(["pay_other_9"], "同症状但不同支付编号")
    assert len(hits) == 1
    assert hits[0].note_id == "note_seed"
    assert embedder.calls == 1
    events = await read_events(tmp_path)
    assert require_object(events[-1][1])["matched"] == "semantic"


async def test_semantic_below_threshold_reports_none(tmp_path: Path) -> None:
    """语义分数低于阈值不召回；matched 仍是 none。"""
    assert RECALL_SEMANTIC_THRESHOLD > 0.1
    recall = make_recall(tmp_path, FakeEmbedder([[0.0, 1.0]]), semantic_limit=2)
    hits = await recall.recall(["pay_other_9"], "正交方向的查询")
    assert hits == []
    events = await read_events(tmp_path)
    assert require_object(events[-1][1])["matched"] == "none"


async def test_zero_semantic_limit_never_embeds(tmp_path: Path) -> None:
    """semantic_limit=0：即使配了嵌入也不发生嵌入调用（键路径未命中即止）。"""
    embedder = FakeEmbedder([[1.0, 0.0]])
    recall = make_recall(tmp_path, embedder, semantic_limit=0)
    assert await recall.recall(["pay_other_9"], "查询") == []
    assert embedder.calls == 0


class BrokenStore(MemoryStore):
    """故障替身：键匹配一律抛错，模拟记忆子系统故障。"""

    @override
    def match_keys(self, keys: list[str], *, limit: int) -> list[MemoryNote]:
        """无条件失败，触发 recall 的降级路径。"""
        raise RuntimeError("memory offline")


async def test_store_failure_degrades_without_breaking_turn(tmp_path: Path) -> None:
    """存储故障：发 recall_failed 事件并返回空列表，不向调用方抛异常。"""
    broken = BrokenStore(tmp_path / "unused.sqlite")
    recall = make_recall(tmp_path, store=cast("MemoryStore", broken))
    assert await recall.recall(["pay_bg_001_0003"], "查询") == []
    events = await read_events(tmp_path)
    assert events[-1][0] == "memory.recall_failed"
    assert require_object(events[-1][1])["error_type"] == "RuntimeError"


async def test_event_payload_survives_roundtrip(tmp_path: Path) -> None:
    """memory.recalled 事件载荷字段完整可读，前端按事件流展示。"""
    recall = make_recall(tmp_path)
    await recall.recall(["pay_bg_001_0003"], "查询")
    events = await read_events(tmp_path)
    payload = require_object(events[-1][1])
    assert require_text(payload["matched"]) == "key"
    assert isinstance(payload["chars"], int) and payload["chars"] > 0
