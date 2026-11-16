"""MemoryStore：同键替换、总量淘汰、键匹配与余弦检索的受控测试。"""

import json
from pathlib import Path

from flowpay.core.memory.contracts import MemoryNote
from flowpay.core.memory.store import MemoryStore

OWNER = "acct_test_owner"


def note(
    note_id: str = "note_1",
    keys: list[str] | None = None,
    body: str = "排查结论正文",
    created_at: str = "2026-11-15T10:00:00+00:00",
    embedding: list[float] | None = None,
    owner: str = OWNER,
) -> MemoryNote:
    """构造一条可调形状的记忆；缺省键指向 pay_bg_001_0003，归属 OWNER。"""
    return MemoryNote(
        note_id=note_id,
        session_id="session_1",
        workflow="manual-loop",
        owner=owner,
        created_at=created_at,
        keys=keys if keys is not None else ["pay_bg_001_0003"],
        conditions={"known": {"payment_id": "pay_bg_001_0003"}},
        body=body,
        embedding=embedding,
        body_chars=len(body),
    )


async def test_key_match_roundtrips_note(tmp_path: Path) -> None:
    """保存后按键召回：正文与条件原样返回，大小写不敏感。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note(body="HTTP 410 由端点停用导致"))
    hits = store.match_keys(OWNER, ["PAY_BG_001_0003"], limit=3)
    assert len(hits) == 1
    assert hits[0].body == "HTTP 410 由端点停用导致"
    assert hits[0].conditions == {"known": {"payment_id": "pay_bg_001_0003"}}
    assert hits[0].keys == ["pay_bg_001_0003"]


async def test_key_match_without_overlap_returns_empty(tmp_path: Path) -> None:
    """键无交集不召回；空键与零预算直接返回空列表。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note())
    assert store.match_keys(OWNER, ["pay_other_9"], limit=3) == []
    assert store.match_keys(OWNER, [], limit=3) == []
    assert store.match_keys(OWNER, ["pay_bg_001_0003"], limit=0) == []


async def test_same_keys_replaces_existing_note(tmp_path: Path) -> None:
    """键集完全相同 → 删旧插新：仍只有一条，正文是新结论。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note(note_id="note_old", body="旧结论"))
    store.save(note(note_id="note_new", body="新结论", created_at="2026-11-15T11:00:00+00:00"))
    hits = store.match_keys(OWNER, ["pay_bg_001_0003"], limit=5)
    assert [item.note_id for item in hits] == ["note_new"]
    assert hits[0].body == "新结论"


async def test_partial_key_overlap_appends(tmp_path: Path) -> None:
    """键有交集但不全等 → 照常追加，两条都在。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note(note_id="note_1"))
    store.save(note(note_id="note_2", keys=["pay_bg_001_0003", "acct_bg_001"]))
    hits = store.match_keys(OWNER, ["pay_bg_001_0003"], limit=5)
    assert {item.note_id for item in hits} == {"note_1", "note_2"}


async def test_eviction_drops_oldest_and_reports_count(tmp_path: Path) -> None:
    """超出总量封顶时最旧淘汰：save 返回淘汰数，留存的是最新两条。"""
    store = MemoryStore(tmp_path / "memory.sqlite", max_notes=2)
    assert (
        store.save(note(note_id="n1", keys=["pay_1"], created_at="2026-11-15T01:00:00+00:00")) == 0
    )  # noqa: E501
    assert (
        store.save(note(note_id="n2", keys=["pay_2"], created_at="2026-11-15T02:00:00+00:00")) == 0
    )  # noqa: E501
    assert (
        store.save(note(note_id="n3", keys=["pay_3"], created_at="2026-11-15T03:00:00+00:00")) == 1
    )  # noqa: E501
    assert store.match_keys(OWNER, ["pay_1"], limit=5) == []
    assert {item.note_id for item in store.match_keys(OWNER, ["pay_2", "pay_3"], limit=5)} == {
        "n2",
        "n3",
    }


async def test_key_match_ranks_by_overlap_then_recency(tmp_path: Path) -> None:
    """交集数大的排前；同数时新的排前。键集互不全等，不触发同键替换。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note(note_id="one", keys=["pay_1", "pay_9"], created_at="2026-11-15T01:00:00+00:00"))
    store.save(note(note_id="two", keys=["pay_1", "pay_2"], created_at="2026-11-15T02:00:00+00:00"))
    store.save(note(note_id="three", keys=["pay_1"], created_at="2026-11-15T03:00:00+00:00"))
    hits = store.match_keys(OWNER, ["pay_1", "pay_2"], limit=3)
    assert [item.note_id for item in hits] == ["two", "three", "one"]


async def test_eviction_never_drops_just_saved_note(tmp_path: Path) -> None:
    """同秒写入达封顶：淘汰排除本次插入的笔记，新结论不被误删。"""
    store = MemoryStore(tmp_path / "memory.sqlite", max_notes=2)
    same_second = "2026-11-15T10:00:00+00:00"
    store.save(note(note_id="n1", keys=["pay_1"], created_at=same_second))
    store.save(note(note_id="n2", keys=["pay_2"], created_at=same_second))
    store.save(note(note_id="n3", keys=["pay_3"], created_at=same_second))
    assert store.match_keys(OWNER, ["pay_3"], limit=5)[0].note_id == "n3"


async def test_semantic_search_orders_by_cosine(tmp_path: Path) -> None:
    """余弦降序返回前 limit 条；阈值过滤由调用方负责，存储层不过滤。"""
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save(note(note_id="same", keys=["pay_1"], embedding=[1.0, 0.0]))
    store.save(note(note_id="orthogonal", keys=["pay_2"], embedding=[0.0, 1.0]))
    store.save(note(note_id="opposite", keys=["pay_3"], embedding=[-1.0, 0.0]))
    store.save(note(note_id="no_vector", keys=["pay_4"]))
    hits = store.search_semantic([1.0, 0.0], OWNER, limit=3)
    assert [item[0].note_id for item in hits] == ["same", "orthogonal", "opposite"]
    assert [item[1] for item in hits] == [1.0, 0.0, -1.0]


async def test_save_and_reload_keeps_embedding_json(tmp_path: Path) -> None:
    """embedding 以 JSON 数组持久化，重开后可读回；conditions/键同样保真。"""
    path = tmp_path / "memory.sqlite"
    store = MemoryStore(path)
    store.save(note(embedding=[0.5, -0.5]))
    reopened = MemoryStore(path)
    hits = reopened.search_semantic([1.0, -1.0], OWNER, limit=1)
    assert len(hits) == 1 and hits[0][0].embedding == [0.5, -0.5]
    assert json.loads(json.dumps(hits[0][0].keys)) == hits[0][0].keys


async def test_notes_are_isolated_by_owner(tmp_path: Path) -> None:
    """同一键集在不同 owner 下互不可见；封顶也按 owner 分别计算（specs/005 US4）。"""
    store = MemoryStore(tmp_path / "memory.sqlite", max_notes=1)
    store.save(note(note_id="a1", owner="acct_a", created_at="2026-11-15T01:00:00+00:00"))
    store.save(
        note(
            note_id="a2",
            owner="acct_a",
            created_at="2026-11-15T02:00:00+00:00",
            embedding=[1.0, 0.0],
        )
    )
    assert store.match_keys("acct_a", ["pay_bg_001_0003"], limit=5)[0].note_id == "a2"
    assert store.match_keys("acct_b", ["pay_bg_001_0003"], limit=5) == []
    store.save(note(note_id="b1", owner="acct_b", embedding=[1.0, 0.0]))
    assert [item[0].note_id for item in store.search_semantic([1.0, 0.0], "acct_a", limit=5)] == [
        "a2"
    ]
    assert [item[0].note_id for item in store.search_semantic([1.0, 0.0], "acct_b", limit=5)] == [
        "b1"
    ]
