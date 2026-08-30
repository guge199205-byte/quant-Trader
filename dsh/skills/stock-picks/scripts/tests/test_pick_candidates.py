"""pick_candidates 纯逻辑单测：跨日聚合 / ST 过滤 / 仓位门 / L2 主导。

不需要 PG/QuantDB——直接构造输入调纯函数。
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pick_candidates import _per_day_scores, score_candidates


def _scores():
    return {
        "600519": {"fusion": 0.04, "side": "BUY", "position_score": 0.85,
                   "pct_industry": 0.99, "industry_top10_avg": 0.038, "consensus": 2},
        "000001": {"fusion": 0.03, "side": "HOLD", "position_score": 0.5,
                   "pct_industry": 0.85, "industry_top10_avg": 0.032, "consensus": 0},
        "300750": {"fusion": 0.05, "side": "BUY", "position_score": 0.9,
                   "pct_industry": 0.98, "industry_top10_avg": 0.041, "consensus": 3},
        "002001": {"fusion": 0.02, "side": "HOLD", "position_score": 0.0,
                   "pct_industry": 0.5, "industry_top10_avg": 0.025, "consensus": 0},
        "600001": {"fusion": 0.045, "side": "HOLD", "position_score": 0.88,
                   "pct_industry": 0.97, "industry_top10_avg": 0.039, "consensus": 1},
    }


def _l2():
    # 300750 的 L2 健康分最高（正 IC 高、负 IC 低）
    return {
        "600519": {"l2_score": 0.5, "l2_pos": 0.5, "l2_neg": 0.5},
        "000001": {"l2_score": 0.4, "l2_pos": 0.4, "l2_neg": 0.6},
        "300750": {"l2_score": 0.9, "l2_pos": 0.9, "l2_neg": 0.1},
        "600001": {"l2_score": 0.3, "l2_pos": 0.3, "l2_neg": 0.7},
    }


class TestScoreCandidates(unittest.TestCase):
    def setUp(self):
        self.names = {"600519": "贵州茅台", "000001": "平安银行", "300750": "宁德时代",
                      "002001": "新和成", "600001": "邯郸钢铁"}
        self.ind = {"600519": "白酒", "000001": "银行", "300750": "电池",
                    "002001": "医药", "600001": "钢铁"}

    def _day(self, scores=None, l2=None):
        return _per_day_scores(scores or _scores(), {}, l2 or _l2(), {}, self.ind, {})

    def test_l2_dominates_ranking(self):
        # 单日：300750 融合中等但 L2 最高(0.9)，600519 融合高但 L2 0.5
        cands = score_candidates([self._day()], self.names)
        by = {c["symbol"]: c for c in cands}
        # 300750 应排第一（L2 权重 40% 主导）
        self.assertEqual(cands[0]["symbol"], "300750")
        # 002001：仓位门不过（pos=0 且行业分位<0.8）→ 剔除
        self.assertNotIn("002001", by)

    def test_trend_not_in_output(self):
        cands = score_candidates([self._day()], self.names)
        for c in cands:
            self.assertNotIn("d_trend", c)
            self.assertNotIn("trend", c)

    def test_st_excluded_by_default(self):
        names = dict(self.names)
        names["600001"] = "*ST邯郸"
        cands = score_candidates([self._day()], names)
        syms = {c["symbol"] for c in cands}
        self.assertNotIn("600001", syms)

    def test_st_kept_with_flag(self):
        names = dict(self.names)
        names["600001"] = "*ST邯郸"
        cands = score_candidates([self._day()], names, exclude_st=False)
        syms = {c["symbol"] for c in cands}
        self.assertIn("600001", syms)

    def test_multi_day_aggregation_mean(self):
        # 两天：300750 每天都高分 → 均值高；600519 第二天 L2 掉到 0.1 → 均值拉低
        l2_day2 = dict(_l2())
        l2_day2["600519"] = {"l2_score": 0.1, "l2_pos": 0.1, "l2_neg": 0.9}
        days = [self._day(), self._day(l2=l2_day2)]
        cands = score_candidates(days, self.names)
        self.assertEqual(cands[0]["symbol"], "300750")
        self.assertGreater(next(c for c in cands if c["symbol"] == "300750")["n_days"], 1)

    def test_negative_fusion_excluded(self):
        scores = dict(_scores())
        scores["600519"] = {**scores["600519"], "fusion": -0.02}
        cands = score_candidates([self._day(scores=scores)], self.names)
        self.assertNotIn("600519", {c["symbol"] for c in cands})

    def test_each_candidate_has_dim_breakdown(self):
        cands = score_candidates([self._day()], self.names)
        for c in cands:
            for k in ("d_fusion", "d_position", "d_sector", "d_news", "d_l2", "score"):
                self.assertIn(k, c)
                self.assertGreaterEqual(c[k], 0.0)
                self.assertLessEqual(c[k], 1.0)


if __name__ == "__main__":
    unittest.main()
