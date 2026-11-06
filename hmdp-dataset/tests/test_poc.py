from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hmdp_simulator.generate import generate_dataset
from hmdp_simulator.poc import run_poc


class PocTests(unittest.TestCase):
    def test_poc_passes_on_generated_dataset(self) -> None:
        """生成的 smoke 数据包必须通过 PoC 校验。"""
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "hmdp-dataset"
            generate_dataset(target, seed=2026, scale="smoke")
            result = run_poc(target)

            self.assertTrue(result["ok"], result)
            self.assertGreaterEqual(result["counts"]["documents"], 12)
            self.assertGreaterEqual(result["counts"]["benchmark_cases"], 18)
            for name in (
                "manifest_present",
                "runtime_counts_match_db",
                "scenario_sold_out",
                "scenario_duplicate_claim",
                "scenario_not_started",
                "scenario_sale_closed",
                "scenario_order_statuses",
                "scenario_shop_paused",
                "scenario_review_score",
                "scenario_documents_present",
            ):
                self.assertTrue(result["checks"][name], result["checks"])


if __name__ == "__main__":
    unittest.main()

