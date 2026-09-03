#!/usr/bin/env python3
"""核心逻辑最小测试（P0-3，unittest 零依赖）。运行：python -m unittest discover -s tests
覆盖：风险档位判定 / 假设状态判定 / 分歧检测 / JSON 块抽取 / 预算防抖档位。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


class TestRiskDecide(unittest.TestCase):
    def test_calm_when_no_triggers(self):
        from risk_budget_agent import decide_level

        self.assertEqual(decide_level(0.8, 1.0, 50, 1)[0], "calm")

    def test_caution_on_high_vol(self):
        from risk_budget_agent import decide_level

        self.assertEqual(decide_level(1.5, 1.0, 50, 1)[0], "caution")

    def test_caution_on_cold_sentiment(self):
        from risk_budget_agent import decide_level

        self.assertEqual(decide_level(0.5, 1.0, 15, 1)[0], "caution")

    def test_defensive_on_deep_drawdown(self):
        from risk_budget_agent import decide_level

        lvl, reasons = decide_level(0.5, 6.0, 50, 1)
        self.assertEqual(lvl, "defensive")
        self.assertTrue(any("回撤" in r for r in reasons))

    def test_budget_tightens_with_level(self):
        from risk_budget_agent import LEVELS

        self.assertGreater(LEVELS["calm"]["leverage_max"], LEVELS["caution"]["leverage_max"])
        self.assertGreater(LEVELS["caution"]["per_stock_pct"], LEVELS["defensive"]["per_stock_pct"])


class TestHypothesisStatus(unittest.TestCase):
    def test_verified_when_bull_and_positive_diff(self):
        from hypothesis_lab import decide_status

        self.assertEqual(decide_status(True, 0.05, 50), "verified")

    def test_contradicted_when_bear_claim_but_positive(self):
        from hypothesis_lab import decide_status

        self.assertEqual(decide_status(False, 0.05, 50), "contradicted")

    def test_proposed_when_small_diff(self):
        from hypothesis_lab import decide_status

        self.assertEqual(decide_status(True, 0.02, 50), "proposed")

    def test_insufficient_when_small_sample(self):
        from hypothesis_lab import decide_status

        self.assertEqual(decide_status(True, 0.10, 10), "insufficient")


class TestConflictDetect(unittest.TestCase):
    def test_conflict_sell_vs_buy(self):
        from debate_arbiter import find_conflicts

        m = {"A": {"decisions": [{"action": "sell", "code": "600519.SH", "confidence": 0.8}]},
             "B": {"decisions": [{"action": "buy", "code": "600519.SH", "confidence": 0.8}]}}
        self.assertEqual(len(find_conflicts(m)), 1)

    def test_no_conflict_same_direction(self):
        from debate_arbiter import find_conflicts

        m = {"A": {"decisions": [{"action": "sell", "code": "600519.SH", "confidence": 0.8}]},
             "B": {"decisions": [{"action": "sell", "code": "600519.SH", "confidence": 0.9}]}}
        self.assertEqual(find_conflicts(m), [])


class TestJsonBlocks(unittest.TestCase):
    def test_plain_and_fenced(self):
        from post_review import json_blocks

        t = '前置\n```json\n{"a": 1}\n```\n尾随 {"b": [1, 2]} 结束'
        blocks = json_blocks(t)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["a"], 1)
        self.assertEqual(blocks[1]["b"], [1, 2])

    def test_garbage_ignored(self):
        from post_review import json_blocks

        self.assertEqual(json_blocks("no braces here"), [])
        self.assertEqual(json_blocks('{"bad": }'), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)