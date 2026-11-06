"""验证黑马点评模拟数据包是否能支撑用户端 PoC。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

from hmdp_simulator.docs import documents


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """读取 JSONL；空文件返回空列表。"""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [cast(dict[str, object], json.loads(line)) for line in handle if line.strip()]


def _scalar(db: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()) -> object:
    """执行单值查询。"""
    return db.execute(sql, parameters).fetchone()[0]


def _scenario_sold_out(db: sqlite3.Connection) -> bool:
    """验证：库存为 0 后请求失败，且该用户没有生成订单。"""
    attempt = db.execute(
        """
        select result, error_code
        from seckill_attempts
        where user_id=? and voucher_id=? and error_code='VOUCHER_SOLD_OUT'
        """,
        ("usr_u_000101", "vou_s1_001"),
    ).fetchone()
    if attempt is None or attempt[0] != "failed":
        return False
    order_count = _scalar(
        db,
        "select count(*) from voucher_orders where user_id=? and voucher_id=?",
        ("usr_u_000101", "vou_s1_001"),
    )
    stock = _scalar(
        db, "select remaining_stock from vouchers where voucher_id=?", ("vou_s1_001",)
    )
    return order_count == 0 and stock == 0


def _scenario_duplicate_claim(db: sqlite3.Connection) -> bool:
    """验证：已有订单时新请求返回重复领取。"""
    has_order = _scalar(
        db,
        "select count(*) from voucher_orders where user_id=? and voucher_id=?",
        ("usr_u_000102", "vou_s1_002"),
    ) > 0
    failed = _scalar(
        db,
        """
        select count(*)
        from seckill_attempts
        where user_id=? and voucher_id=? and error_code='DUPLICATE_CLAIM'
        """,
        ("usr_u_000102", "vou_s1_002"),
    ) > 0
    return has_order and failed


def _scenario_time_result(
    db: sqlite3.Connection, user_id: str, voucher_id: str, error_code: str
) -> bool:
    """验证未开始/已结束请求明确失败，且不会生成对应券订单。"""
    has_attempt = _scalar(
        db,
        """
        select count(*)
        from seckill_attempts
        where user_id=? and voucher_id=? and error_code=?
        """,
        (user_id, voucher_id, error_code),
    ) > 0
    has_order = _scalar(
        db,
        "select count(*) from voucher_orders where user_id=? and voucher_id=?",
        (user_id, voucher_id),
    ) > 0
    return has_attempt and not has_order


def _scenario_order_statuses(db: sqlite3.Connection) -> bool:
    """验证四类核心订单状态均按预置数据存在。"""
    expected = {
        "ord_o1_001": "used",
        "ord_o1_002": "created",
        "ord_o1_003": "expired",
        "ord_o1_004": "refund_rejected",
    }
    for order_id, status in expected.items():
        actual = _scalar(
            db, "select status from voucher_orders where order_id=?", (order_id,)
        )
        if actual != status:
            return False
    return True


def _scenario_shop_paused(db: sqlite3.Connection) -> bool:
    """验证门店暂停状态和事件同时存在。"""
    status = _scalar(db, "select status from shops where shop_id=?", ("shp_s1_001",))
    event_count = _scalar(
        db,
        "select count(*) from shop_events where shop_id=? and event='shop.paused'",
        ("shp_s1_001",),
    )
    return status == "paused" and event_count > 0


def _scenario_review_score(db: sqlite3.Connection) -> bool:
    """验证低分门店确实有足够差评样本。"""
    row = db.execute(
        """
        select avg(rating), count(*)
        from reviews
        where shop_id=?
        """,
        ("shp_r1_001",),
    ).fetchone()
    return row[1] >= 12 and row[0] is not None and row[0] < 3.5


def _runtime_counts_match(db: sqlite3.Connection, canonical: Path) -> bool:
    """对比 JSONL 流水行数和 SQLite 表行数。"""
    mappings = {
        "seckill_attempts.jsonl": "seckill_attempts",
        "order_events.jsonl": "order_events",
        "shop_events.jsonl": "shop_events",
        "stock_changes.jsonl": "stock_changes",
    }
    for filename, table in mappings.items():
        rows = _read_jsonl(canonical / "runtime" / filename)
        if len(rows) != _scalar(db, f"select count(*) from {table}"):
            return False
    return True


def run_poc(root: Path) -> dict[str, object]:
    """执行数据包级 PoC 校验，返回结构化结果。"""
    root = Path(root)
    canonical = root / "data/generated/canonical"
    manifest_path = canonical / "manifest.json"
    knowledge_dir = root / "data/knowledge"
    benchmark_path = root / "benchmark/design/benchmark_blueprints.jsonl"
    db_path = canonical / "hmdp.sqlite"

    checks: dict[str, bool] = {}
    counts: dict[str, int] = {}

    checks["manifest_present"] = manifest_path.is_file()
    checks["benchmark_present"] = benchmark_path.is_file()
    benchmark_count = len(_read_jsonl(benchmark_path))
    counts["benchmark_cases"] = benchmark_count
    checks["benchmark_cases_present"] = benchmark_count >= 18

    document_ids = {path.stem for path in knowledge_dir.glob("*.md")} if knowledge_dir.exists() else set()
    expected_docs = {doc.doc_id for doc in documents()}
    counts["documents"] = len(document_ids)
    checks["scenario_documents_present"] = expected_docs <= document_ids

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        for table in (
            "users",
            "shops",
            "vouchers",
            "voucher_orders",
            "reviews",
            "seckill_attempts",
            "order_events",
            "shop_events",
            "stock_changes",
            "incidents",
        ):
            counts[table] = int(_scalar(db, f"select count(*) from {table}"))

        checks["runtime_counts_match_db"] = _runtime_counts_match(db, canonical)
        checks["scenario_sold_out"] = _scenario_sold_out(db)
        checks["scenario_duplicate_claim"] = _scenario_duplicate_claim(db)
        checks["scenario_not_started"] = _scenario_time_result(
            db, "usr_u_000103", "vou_s1_003", "NOT_STARTED"
        )
        checks["scenario_sale_closed"] = _scenario_time_result(
            db, "usr_u_000104", "vou_s1_004", "SALE_CLOSED"
        )
        checks["scenario_order_statuses"] = _scenario_order_statuses(db)
        checks["scenario_shop_paused"] = _scenario_shop_paused(db)
        checks["scenario_review_score"] = _scenario_review_score(db)
    finally:
        db.close()

    return {"ok": all(checks.values()), "root": str(root), "counts": counts, "checks": checks}


def main() -> None:
    """命令行入口：python -m hmdp_simulator.poc --root ."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = run_poc(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


