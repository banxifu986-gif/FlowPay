"""排查记忆的 SQLite 存取；core 内唯一触碰 sqlite3 的记忆实现（specs/004）。

不与 sessions.sqlite 共用文件：事件日志的 append-only 触发器是那份文件的存在
理由，可变、可淘汰的记忆放进去会纠缠删除语义。量级 ≤ max_notes（缺省 200），
键匹配与余弦都在内存里做，不需要向量索引。
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

from flowpay.core.json import JsonObject, require_int, require_list, require_object, require_text

from .contracts import MemoryNote

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_notes (
    note_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    created_at TEXT NOT NULL,
    keys_json TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    body TEXT NOT NULL,
    embedding TEXT,
    body_chars INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_notes (created_at);
"""

_NOTE_COLUMNS = (
    "note_id, session_id, workflow, created_at, keys_json, conditions_json, body, "
    "embedding, body_chars"
)

_INSERT_SQL = "INSERT INTO memory_notes (" + _NOTE_COLUMNS + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"

_EVICT_SQL = (
    "DELETE FROM memory_notes WHERE note_id IN ("
    "SELECT note_id FROM memory_notes WHERE note_id != ? "
    "ORDER BY created_at, note_id LIMIT ?)"
)


def _keys_json(keys: list[str]) -> str:
    """键的规范序列化：casefold 归一并排序，同键全等判定退化为字符串相等。"""
    return json.dumps(sorted({key.casefold() for key in keys}), ensure_ascii=False)


def _rows(db: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    """执行查询并取回全部行；sqlite3 的返回值无类型标注，按行对象收窄。"""
    return cast("list[sqlite3.Row]", db.execute(sql).fetchall())


class MemoryStore:
    """memory_notes 单表的同步存取；调用方负责在事件循环外经线程调用。"""

    def __init__(self, path: Path, max_notes: int = 200):
        """打开（必要时创建）记忆库；目录不存在则逐级创建。"""
        self._path: Path = path
        self._max_notes: int = max_notes
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_SCHEMA)

    def save(self, note: MemoryNote) -> int:
        """写入一条记忆并执行总量封顶；返回本轮被淘汰的条数。

        新笔记的键集与某条现存笔记完全相同时先删旧再插入（重复排查同一 case
        的最简合并形态，新结论天然更相关）；键有交集但不全等则照常追加。
        删除与插入在同一事务里，崩溃即回滚。
        """
        embedding_json = (
            json.dumps(note.embedding, ensure_ascii=False) if note.embedding is not None else None
        )
        values = (
            note.note_id,
            note.session_id,
            note.workflow,
            note.created_at,
            _keys_json(note.keys),
            json.dumps(note.conditions, ensure_ascii=False),
            note.body,
            embedding_json,
            require_int(note.body_chars),
        )
        dropped = 0
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("DELETE FROM memory_notes WHERE keys_json = ?", (values[4],))
            db.execute(_INSERT_SQL, values)
            counted = cast("tuple[int]", db.execute("SELECT COUNT(*) FROM memory_notes").fetchone())
            count = counted[0]
            overflow = count - self._max_notes
            if overflow > 0:
                # 最旧淘汰：created_at 升序，note_id 做同刻稳定次序；排除本次插入的
                # 笔记——created_at 只有秒级精度，同秒回填时随机 note_id 可能把
                # 刚落库的结论排进淘汰区间。
                db.execute(_EVICT_SQL, (note.note_id, overflow))
                dropped = overflow
        return dropped

    def match_keys(self, keys: list[str], *, limit: int) -> list[MemoryNote]:
        """按结构化键精确匹配：取键集与查询键集有交集的笔记，按交集数、时间倒序。

        量级 ≤ max_notes，全表载入内存比对即可；无键或零命中返回空列表。
        """
        wanted = {key.casefold() for key in keys if key.strip()}
        if not wanted or limit <= 0:
            return []
        scored: list[tuple[int, str, sqlite3.Row]] = []
        with closing(sqlite3.connect(self._path)) as db:
            db.row_factory = sqlite3.Row
            for row in _rows(db, "SELECT " + _NOTE_COLUMNS + " FROM memory_notes"):
                stored = set(cast("list[str]", json.loads(cast("str", row["keys_json"]))))
                if overlap := len(stored & wanted):
                    created = cast("str", row["created_at"])
                    scored.append((overlap, created, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [_load_note(item[2]) for item in scored[:limit]]

    def search_semantic(self, vector: list[float], *, limit: int) -> list[tuple[MemoryNote, float]]:
        """余弦相似度兜底：只看有向量的笔记，返回分数降序的前 limit 条。

        阈值过滤由调用方（recall 层）负责，存储层保持纯检索语义。
        """
        if not vector or limit <= 0:
            return []
        scored: list[tuple[float, str, MemoryNote]] = []
        with closing(sqlite3.connect(self._path)) as db:
            db.row_factory = sqlite3.Row
            sql = "SELECT " + _NOTE_COLUMNS + " FROM memory_notes WHERE embedding IS NOT NULL"
            for row in _rows(db, sql):
                note = _load_note(row)
                scored.append((_cosine(vector, note.embedding or []), note.created_at, note))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [(note, score) for score, _, note in scored[:limit]]

    def close(self) -> None:
        """无长驻连接（每次操作即开即关），无需释放；保留接口对齐资源生命周期。"""


def _load_note(row: sqlite3.Row) -> MemoryNote:
    """从行构造 MemoryNote；列值由本模块自身写入，畸形内容按契约校验。"""
    raw_embedding = cast("str | None", row["embedding"])
    embedding = (
        cast("list[float]", json.loads(raw_embedding)) if raw_embedding is not None else None
    )
    conditions: JsonObject = require_object(
        cast("object", json.loads(cast("str", row["conditions_json"])))
    )
    keys = cast("list[str]", json.loads(cast("str", row["keys_json"])))
    return MemoryNote(
        require_text(cast("object", row["note_id"])),
        require_text(cast("object", row["session_id"])),
        require_text(cast("object", row["workflow"])),
        require_text(cast("object", row["created_at"])),
        [require_text(item) for item in require_list(keys)],
        conditions,
        require_text(cast("object", row["body"])),
        embedding,
        require_int(cast("object", row["body_chars"])),
    )


def _cosine(a: list[float], b: list[float]) -> float:
    """两个等长向量的余弦相似度；零向量返回 0，不抛异常。"""
    if len(a) != len(b) or not a:
        return 0.0
    numerator = math.fsum(x * y for x, y in zip(a, b, strict=True))
    if numerator == 0:
        return 0.0
    norm_a = math.sqrt(math.fsum(x * x for x in a))
    norm_b = math.sqrt(math.fsum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return numerator / (norm_a * norm_b)
