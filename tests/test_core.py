#!/usr/bin/env python3
"""核心逻辑最小测试（P0-3，unittest 零依赖）。运行：python -m unittest discover -s tests
覆盖：风险档位判定 / 假设状态判定 / 分歧检测 / JSON 块抽取 / 预算防抖档位。"""
import json
import sys
import tempfile
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


class TestRegisterReviewHypotheses(unittest.TestCase):
    """2026-09-08 回归：原式 `cand.get("description") or cand if isinstance(...)`
    三元优先级错误 → dict 候选 str(dict) 进假设库；全局 H_{date}_{i} 撞 key
    使后跑 agent 的候选静默丢失。"""

    def _reg(self, tmp_path, agent, cands):
        from post_review import register_review_hypotheses

        p = tmp_path / "hypotheses.json"
        n = register_review_hypotheses(agent, "2026-09-08", cands, hyp_path=p)
        hyps = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return n, hyps

    def test_dict_candidate_uses_description_not_repr(self):
        with tempfile.TemporaryDirectory() as td:
            n, hyps = self._reg(Path(td), "deepseek-v4-flash", [
                {"description": "信号真空即正期望", "direction": "空仓/不建仓"}])
            self.assertEqual(n, 1)
            entry = hyps["H_2026-09-08_deepseek-v4-flash_0"]
            self.assertEqual(entry["name"], "信号真空即正期望")
            self.assertNotIn("{", entry["name"])
            self.assertEqual(entry["direction"], "空仓/不建仓")
            self.assertEqual(entry["status"], "proposed")

    def test_chinese_keys_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            n, hyps = self._reg(Path(td), "glm-5.3-flash", [
                {"描述": "主线高低切后减仓时效", "方向": "看空持仓股短线"}])
            self.assertEqual(n, 1)
            entry = hyps["H_2026-09-08_glm-5.3-flash_0"]
            self.assertEqual(entry["name"], "主线高低切后减仓时效")
            self.assertEqual(entry["direction"], "看空持仓股短线")

    def test_agent_prefix_prevents_key_collision(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            from post_review import register_review_hypotheses
            p = td / "hypotheses.json"
            cands = [{"description": "假设甲", "direction": "d1"}]
            n1 = register_review_hypotheses("agent-a", "2026-09-08", cands, hyp_path=p)
            n2 = register_review_hypotheses("agent-b", "2026-09-08", cands, hyp_path=p)
            self.assertEqual((n1, n2), (1, 1))
            hyps = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(len(hyps), 2)

    def test_string_candidate_and_garbage_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            n, hyps = self._reg(Path(td), "a", ["纯文本假设", 42, {"trigger": "无description"}])
            self.assertEqual(n, 1)
            self.assertEqual(hyps["H_2026-09-08_a_0"]["name"], "纯文本假设")

    def test_missing_description_not_registered(self):
        with tempfile.TemporaryDirectory() as td:
            n, hyps = self._reg(Path(td), "a", [{"trigger": "无description键"}])
            self.assertEqual(n, 0)
            self.assertEqual(hyps, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)