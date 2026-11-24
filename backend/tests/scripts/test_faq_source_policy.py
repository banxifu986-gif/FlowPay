"""FAQ 源策略在既有只读查询器上区分本人，且不区分他人与不存在。"""

import json
import sqlite3
import tomllib
from pathlib import Path

import pytest
from flowpay.core.data_sources import DataSources, digest
from flowpay.core.json import JsonObject

POLICY = Path(__file__).resolve().parents[3] / "flowpay-faq-dataset" / "seed" / "source-policy.toml"


@pytest.fixture
def sources(tmp_path: Path) -> DataSources:
    with POLICY.open("rb") as handle:
        policy = tomllib.load(handle)
    database = tmp_path / "faq.sqlite"
    with sqlite3.connect(database) as db:
        _ = db.executescript("""
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY, payer_id TEXT NOT NULL, order_ref TEXT NOT NULL,
                status TEXT NOT NULL, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
                occurred_at TEXT NOT NULL, channel TEXT NOT NULL
            );
            CREATE TABLE refunds (
                refund_id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL REFERENCES payments(payment_id),
                status TEXT NOT NULL, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
                requested_at TEXT NOT NULL, settled_at TEXT
            );
            INSERT INTO payments VALUES
                ('pay_faq_a', 'payer_a', 'FAO-SHARED', 'succeeded', 1200, 'CNY',
                 '2026-11-01T08:00:00+08:00', 'bank'),
                ('pay_faq_b', 'payer_b', 'FAO-SHARED', 'succeeded', 1200, 'CNY',
                 '2026-11-01T08:00:00+08:00', 'bank');
            INSERT INTO refunds VALUES
                ('ref_faq_a', 'pay_faq_a', 'completed', 1200, 'CNY',
                 '2026-11-02T08:00:00+08:00', NULL),
                ('ref_faq_b', 'pay_faq_b', 'completed', 1200, 'CNY',
                 '2026-11-02T08:00:00+08:00', '2026-11-03T08:00:00+08:00');
        """)
    manifest = tmp_path / "manifest.json"
    _ = manifest.write_text(json.dumps({"files": {database.name: {"sha256": digest(database)}}}))
    return DataSources(
        tmp_path,
        {
            **policy,
            "release_id": "test-faq",
            "manifest": manifest.name,
            "manifest_sha256": digest(manifest),
        },
    )


@pytest.mark.parametrize(("payer", "other"), [("a", "b"), ("b", "a")])
def test_registered_queries_keep_payments_and_refunds_in_scope(
    sources: DataSources, payer: str, other: str
) -> None:
    scope: JsonObject = {"account_id": f"payer_{payer}"}
    scoped = sources.sql("payments", scope, {"order_ref": "FAO-SHARED"})["rows"]
    assert [row["payment_id"] for row in scoped] == [f"pay_faq_{payer}"]
    assert "payer_id" not in scoped[0]
    refunded = sources.sql("refunds", scope, {"payment_id": f"pay_faq_{payer}"})["rows"]
    assert [row["refund_id"] for row in refunded] == [f"ref_faq_{payer}"]
    assert refunded[0]["settled_at"] == (None if payer == "a" else "2026-11-03T08:00:00+08:00")
    assert "payer_id" not in refunded[0]

    for source, field, prefix in (
        ("payments", "payment_id", "pay_faq_"),
        ("refunds", "refund_id", "ref_faq_"),
        ("refunds", "payment_id", "pay_faq_"),
    ):
        outsider = sources.sql(source, scope, {field: prefix + other})["rows"]
        absent = sources.sql(source, scope, {field: prefix + "absent"})["rows"]
        assert outsider == absent == []


@pytest.mark.parametrize("source", ["payments", "refunds"])
def test_registered_queries_require_scope_and_reject_owner_filters(
    sources: DataSources, source: str
) -> None:
    with pytest.raises(ValueError, match="Scope must give exactly"):
        _ = sources.sql(source, {})
    for filter_name in ("account_id", "payer_id"):
        with pytest.raises(ValueError, match="Unsupported SQL filter"):
            _ = sources.sql(source, {"account_id": "payer_a"}, {filter_name: "payer_b"})
