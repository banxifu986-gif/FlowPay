"""生成黑马点评用户端模拟数据包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TextIO

from hmdp_simulator.benchmark import write_benchmarks
from hmdp_simulator.docs import documents
from hmdp_simulator.snapshot import Snapshot, build_snapshot

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    city TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE shops (
    shop_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    region TEXT NOT NULL,
    address TEXT NOT NULL,
    business_hours TEXT NOT NULL,
    status TEXT NOT NULL,
    rating REAL NOT NULL
);

CREATE TABLE vouchers (
    voucher_id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id),
    title TEXT NOT NULL,
    sale_type TEXT NOT NULL,
    face_value INTEGER NOT NULL,
    sale_price INTEGER NOT NULL,
    total_stock INTEGER NOT NULL,
    remaining_stock INTEGER NOT NULL,
    per_user_limit INTEGER NOT NULL,
    sale_start_at TEXT NOT NULL,
    sale_end_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE voucher_orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    shop_id TEXT NOT NULL REFERENCES shops(shop_id),
    status TEXT NOT NULL,
    sale_price INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT,
    expired_at TEXT,
    refunded_at TEXT
);

CREATE TABLE reviews (
    review_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    shop_id TEXT NOT NULL REFERENCES shops(shop_id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    detail TEXT NOT NULL
);

CREATE TABLE seckill_attempts (
    attempt_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    result TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    stock_after INTEGER NOT NULL
);

CREATE TABLE order_events (
    event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES voucher_orders(order_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE shop_events (
    event_id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id),
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    detail TEXT NOT NULL
);

CREATE TABLE stock_changes (
    change_id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    delta INTEGER NOT NULL,
    stock_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_orders_user ON voucher_orders(user_id, created_at);
CREATE INDEX idx_orders_voucher ON voucher_orders(voucher_id, created_at);
CREATE INDEX idx_attempts_user_voucher ON seckill_attempts(user_id, voucher_id, created_at);
CREATE INDEX idx_reviews_shop_time ON reviews(shop_id, created_at);
CREATE INDEX idx_order_events_order ON order_events(order_id, created_at);
"""


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> int:
    """写出稳定 JSONL；字段按名称排序，便于复现。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def write_documents(target: Path) -> int:
    """把规则文档写入 knowledge 目录。"""
    directory = target / "data/knowledge"
    directory.mkdir(parents=True, exist_ok=True)
    for doc in documents():
        path = directory / f"{doc.doc_id}.md"
        path.write_text(f"# {doc.title}\n\n{doc.content}", encoding="utf-8", newline="\n")
    return len(documents())


def snapshot_digest(snapshot: Snapshot) -> str:
    """计算业务事实的稳定摘要；SQLite 文件字节不作为复现口径。"""
    digest = hashlib.sha256()
    groups = (
        ("users", snapshot.users),
        ("shops", snapshot.shops),
        ("vouchers", snapshot.vouchers),
        ("orders", snapshot.orders),
        ("reviews", snapshot.reviews),
        ("incidents", snapshot.incidents),
        ("seckill_attempts", snapshot.seckill_attempts),
        ("order_events", snapshot.order_events),
        ("shop_events", snapshot.shop_events),
        ("stock_changes", snapshot.stock_changes),
    )
    for name, rows in groups:
        digest.update(f"{name}:{len(rows)}\n".encode("utf-8"))
        for row in rows:
            digest.update(
                (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            )
    return digest.hexdigest()


def insert_rows(db: sqlite3.Connection, table: str, rows: list[dict[str, object]]) -> int:
    """按 dict 字段批量插入一行列表，返回行数。"""
    if not rows:
        return 0
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(columns)
    db.executemany(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return len(rows)


def write_database(path: Path, snapshot: Snapshot) -> dict[str, int]:
    path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        with db:
            db.executescript(SCHEMA)
            counts = {
                "users": insert_rows(db, "users", snapshot.users),
                "shops": insert_rows(db, "shops", snapshot.shops),
                "vouchers": insert_rows(db, "vouchers", snapshot.vouchers),
                "orders": insert_rows(db, "voucher_orders", snapshot.orders),
                "reviews": insert_rows(db, "reviews", snapshot.reviews),
                "incidents": insert_rows(db, "incidents", snapshot.incidents),
                "seckill_attempts": insert_rows(db, "seckill_attempts", snapshot.seckill_attempts),
                "order_events": insert_rows(db, "order_events", snapshot.order_events),
                "shop_events": insert_rows(db, "shop_events", snapshot.shop_events),
                "stock_changes": insert_rows(db, "stock_changes", snapshot.stock_changes),
            }
    finally:
        db.close()
    return counts


def sha256(path: Path) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    target: Path,
    *,
    seed: int,
    scale: str,
    counts: dict[str, int],
    digest: str,
) -> None:
    """写出可重放清单；文本文件纳入哈希，SQLite 字节不纳入。"""
    canonical = target / "data/generated/canonical"
    files: list[dict[str, object]] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path == canonical / "manifest.json":
            continue
        if path.suffix == ".sqlite":
            continue
        relative = path.relative_to(target).as_posix()
        files.append(
            {"path": relative, "sha256": sha256(path), "size": path.stat().st_size}
        )
    manifest = {
        "schema_version": "1.0",
        "project": "hmdp-user-assistant-poc",
        "seed": seed,
        "scale": scale,
        "snapshot_date": "2026-08-10T00:00:00+08:00",
        "snapshot_sha256": digest,
        "counts": counts,
        "files": files,
    }
    path = canonical / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def generate_dataset(target: Path, *, seed: int = 2026, scale: str = "smoke") -> dict[str, int]:
    """生成完整数据包；重复执行会覆盖目标目录中的生成物。"""
    target.mkdir(parents=True, exist_ok=True)
    document_count = write_documents(target)
    benchmark_count = write_benchmarks(target / "benchmark/design/benchmark_blueprints.jsonl")
    snapshot = build_snapshot(seed=seed, scale=scale)
    canonical = target / "data/generated/canonical"
    db_counts = write_database(canonical / "hmdp.sqlite", snapshot)
    log_counts = {
        "seckill_attempts": write_jsonl(
            canonical / "runtime/seckill_attempts.jsonl", snapshot.seckill_attempts
        ),
        "order_events": write_jsonl(
            canonical / "runtime/order_events.jsonl", snapshot.order_events
        ),
        "shop_events": write_jsonl(canonical / "runtime/shop_events.jsonl", snapshot.shop_events),
        "stock_changes": write_jsonl(
            canonical / "runtime/stock_changes.jsonl", snapshot.stock_changes
        ),
    }
    counts = {
        "documents": document_count,
        "benchmark_cases": benchmark_count,
        **db_counts,
        **log_counts,
    }
    write_manifest(
        target,
        seed=seed,
        scale=scale,
        counts=counts,
        digest=snapshot_digest(snapshot),
    )
    return counts


def main() -> None:
    """命令行入口：python -m hmdp_simulator.generate --root ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--scale", choices=("smoke", "standard"), default="smoke")
    args = parser.parse_args()
    counts = generate_dataset(args.root, seed=args.seed, scale=args.scale)
    for key, value in counts.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
