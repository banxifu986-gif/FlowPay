from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hmdp_simulator.generate import generate_dataset


class GenerateDatasetTests(unittest.TestCase):
    def test_generates_complete_dataset_with_expected_layout(self) -> None:
        """一次生成应包含规则文档、业务快照、运行日志、题库和清单。"""
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "hmdp-dataset"
            result = generate_dataset(target, seed=2026, scale="smoke")

            self.assertGreaterEqual(result["documents"], 12)
            self.assertGreaterEqual(result["benchmark_cases"], 12)
            for name in (
                "data/generated/canonical/hmdp.sqlite",
                "data/generated/canonical/runtime/seckill_attempts.jsonl",
                "data/generated/canonical/runtime/order_events.jsonl",
                "data/generated/canonical/runtime/shop_events.jsonl",
                "benchmark/design/benchmark_blueprints.jsonl",
                "data/generated/canonical/manifest.json",
            ):
                self.assertTrue((target / name).is_file(), name)

            db = sqlite3.connect(target / "data/generated/canonical/hmdp.sqlite")
            try:
                tables = {
                    row[0]
                    for row in db.execute(
                        "select name from sqlite_master where type='table'"
                    )
                }
            finally:
                db.close()
            self.assertTrue(
                {"users", "shops", "vouchers", "voucher_orders", "reviews"} <= tables
            )

            with (target / "data/generated/canonical/manifest.json").open(
                encoding="utf-8"
            ) as handle:
                manifest = json.load(handle)
            self.assertIn("files", manifest)
            self.assertGreater(len(manifest["files"]), 0)

    def test_same_seed_reproduces_same_manifest(self) -> None:
        """相同 seed 的数据必须可重放；manifest 哈希作为对比口径。"""
        with tempfile.TemporaryDirectory() as raw:
            first = Path(raw) / "first"
            second = Path(raw) / "second"
            generate_dataset(first, seed=2026, scale="smoke")
            generate_dataset(second, seed=2026, scale="smoke")
            first_manifest = (
                first / "data/generated/canonical/manifest.json"
            ).read_text(encoding="utf-8")
            second_manifest = (
                second / "data/generated/canonical/manifest.json"
            ).read_text(encoding="utf-8")
            self.assertEqual(first_manifest, second_manifest)


if __name__ == "__main__":
    unittest.main()

