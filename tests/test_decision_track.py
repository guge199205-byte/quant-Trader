#!/usr/bin/env python3
"""决策远期记分卡测试（scripts/decision_track.py）。

口径纪律的回归保护：入场价必须 T+1 开盘、一字板必须剔除、卖出收益必须取负、
样本入池必须幂等。运行：python -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _bars(closes, start=20260101, spread=0.01):
    """收盘价序列 → bars [(dt, open, close, high, low)]；open=close（便于手算）。"""
    return [(start + i, c, c, c * (1 + spread), c * (1 - spread))
            for i, c in enumerate(closes)]


class TestIdAndEntry(unittest.TestCase):
    def test_entry_dt_is_next_trading_day(self):
        from decision_track import entry_dt_of

        # 周五决策 → 下周一入场（跨周末）
        self.assertEqual(entry_dt_of("2026-09-04"), 20260907)

    def test_entry_dt_accepts_datetime_string(self):
        from decision_track import entry_dt_of

        self.assertEqual(entry_dt_of("2026-09-08T14:32:00+08:00"), 20260909)

    def test_make_id_stable_and_distinct(self):
        from decision_track import make_id

        a = make_id("agent-a", "2026-09-08", "600519", "buy")
        self.assertEqual(a, make_id("agent-a", "2026-09-08", "600519", "buy"))
        self.assertNotEqual(a, make_id("agent-b", "2026-09-08", "600519", "buy"))
        self.assertNotEqual(a, make_id("agent-a", "2026-09-08", "600519", "sell"))
        self.assertNotEqual(a, make_id("agent-a", "2026-09-08", "000001", "buy"))


class TestKindOf(unittest.TestCase):
    def test_buy_is_bullish(self):
        from decision_track import _kind_of

        self.assertEqual(_kind_of("buy", "600519", set()), "bullish")

    def test_sell_is_sell(self):
        from decision_track import _kind_of

        self.assertEqual(_kind_of("sell", "600519", set()), "sell")

    def test_watch_on_held_is_position(self):
        from decision_track import _kind_of

        self.assertEqual(_kind_of("watch", "600519", {"600519"}), "position")

    def test_watch_off_holding_is_bullish(self):
        from decision_track import _kind_of

        self.assertEqual(_kind_of("watch", "600519", {"000001"}), "bullish")

    def test_hold_is_not_tracked(self):
        from decision_track import _kind_of

        self.assertIsNone(_kind_of("hold", "600519", {"600519"}))


class TestIngest(unittest.TestCase):
    def _pool_path(self, tmp):
        return Path(tmp) / "pool.jsonl"

    def test_ingest_and_idempotent(self):
        from decision_track import ingest_decisions, load_pool

        with tempfile.TemporaryDirectory() as tmp:
            p = self._pool_path(tmp)
            decisions = [
                {"action": "buy", "code": "600519", "name": "贵州茅台", "pct": 10},
                {"action": "watch", "code": "000001", "name": "平安银行"},
                {"action": "hold", "code": "600036"},          # 不跟踪
                {"action": "buy", "code": ""},                 # 空代码跳过
            ]
            n1 = ingest_decisions("agent-a", decisions, "2026-09-08T10:00:00", path=p)
            self.assertEqual(n1, 2)
            n2 = ingest_decisions("agent-a", decisions, "2026-09-08T14:00:00", path=p)
            self.assertEqual(n2, 0, "同日同标的同动作重复入池必须幂等")
            pool = load_pool(p)
            self.assertEqual(len(pool), 2)
            self.assertEqual({e["kind"] for e in pool}, {"bullish"})

    def test_watch_classified_by_holdings(self):
        from decision_track import ingest_decisions, load_pool

        with tempfile.TemporaryDirectory() as tmp:
            p = self._pool_path(tmp)
            ingest_decisions("agent-a", [{"action": "watch", "code": "600519"}],
                             "2026-09-08T10:00:00", held_codes={"600519"}, path=p)
            self.assertEqual(load_pool(p)[0]["kind"], "position")

    def test_ingest_sets_t_plus_one_entry(self):
        from decision_track import ingest_decisions, load_pool

        with tempfile.TemporaryDirectory() as tmp:
            p = self._pool_path(tmp)
            ingest_decisions("agent-a", [{"action": "buy", "code": "600519"}],
                             "2026-09-08T10:00:00", path=p)
            self.assertEqual(load_pool(p)[0]["entry_dt"], 20260909)

    def test_load_pool_skips_garbage(self):
        from decision_track import load_pool

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "pool.jsonl"
            p.write_text('{"id":"x","agent":"a"}\nnot json\n\n{"no_id":1}\n', encoding="utf-8")
            self.assertEqual(len(load_pool(p)), 1)

    def test_load_pool_missing_file_is_empty(self):
        from decision_track import load_pool

        self.assertEqual(load_pool(Path("/nonexistent/nope.jsonl")), [])


class TestForward(unittest.TestCase):
    def test_forward_returns_from_entry_open(self):
        from decision_track import _forward_for

        bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        r = _forward_for(bars, bars[0][0])
        self.assertEqual(r["entry_px"], 10.0)
        self.assertTrue(r["tradable"])
        self.assertEqual(r["fwd"]["t1"], 0.0)      # T+1 = 入场日收盘
        self.assertEqual(r["fwd"]["t5"], 0.4)      # 14/10 - 1
        self.assertIsNone(r["fwd"]["t20"])         # 数据不足 → None

    def test_forward_uses_open_not_close_of_entry_day(self):
        from decision_track import _forward_for

        # 入场日 open=10, close=20 —— 必须按 open 计价（防前视/防当日追高虚增）
        bars = [(20260101, 10.0, 20.0, 20.5, 9.5), (20260102, 20.0, 21.0, 21.5, 19.5)]
        r = _forward_for(bars, 20260101)
        self.assertEqual(r["entry_px"], 10.0)
        self.assertEqual(r["fwd"]["t1"], 1.0)      # 20/10 - 1，而非 20/20-1

    def test_one_word_board_marked_untradable(self):
        from decision_track import _forward_for

        # 一字板：全无振幅（高=低）且收 > 开 × 1.045
        bars = [(20260101, 10.0, 11.0, 11.0, 11.0), (20260102, 11.0, 11.5, 11.8, 10.9)]
        r = _forward_for(bars, 20260101)
        self.assertFalse(r["tradable"])

    def test_normal_limit_up_not_flagged(self):
        from decision_track import _forward_for

        # 涨停但盘中有振幅（高≠低）→ 可成交
        bars = [(20260101, 10.0, 11.0, 11.0, 9.9), (20260102, 11.0, 11.5, 11.8, 10.9)]
        r = _forward_for(bars, 20260101)
        self.assertTrue(r["tradable"])

    def test_missing_entry_day_returns_empty(self):
        from decision_track import _forward_for

        self.assertEqual(_forward_for(_bars([10.0, 11.0]), 20991231), {})

    def test_zero_open_returns_empty(self):
        from decision_track import _forward_for

        bars = [(20260101, 0.0, 10.0, 10.5, 9.5)]
        self.assertEqual(_forward_for(bars, 20260101), {})

    def test_t1_exits_on_entry_day_close(self):
        from decision_track import _forward_for

        # T+1 = 入场日 开盘→收盘 的 1 日持有（j = idx + h - 1）
        bars = [(20260101, 10.0, 10.5, 10.6, 9.9), (20260102, 10.5, 11.0, 11.1, 10.4)]
        r = _forward_for(bars, 20260101)
        self.assertAlmostEqual(r["fwd"]["t1"], 0.05, places=4)

    def test_zero_close_gives_none_ret(self):
        from decision_track import _forward_for

        bars = [(20260101, 10.0, 0.0, 10.5, 9.5)]   # 入场日收盘价缺失 → 该周期 None
        r = _forward_for(bars, 20260101)
        self.assertIsNone(r["fwd"]["t1"])


class TestTags(unittest.TestCase):
    def test_chase_high_and_above_ma20(self):
        from decision_track import _tags

        closes = [10.0] * 19 + [10.0, 10.5, 11.0, 11.5, 12.0]   # 24 根，入场在第 25 根
        bars = _bars(closes + [12.0])
        tags = _tags(bars, bars[-1][0])
        self.assertIn("追高", tags)
        self.assertIn("接近60日高", tags)
        self.assertIn("站上MA20", tags)
        self.assertNotIn("超跌", tags)

    def test_oversold_and_below_ma20(self):
        from decision_track import _tags

        closes = [20.0] * 19 + [20.0, 19.0, 18.0, 17.0, 16.0]
        bars = _bars(closes + [16.0])
        tags = _tags(bars, bars[-1][0])
        self.assertIn("超跌", tags)
        self.assertIn("破MA20", tags)
        self.assertNotIn("追高", tags)

    def test_insufficient_history_is_empty(self):
        from decision_track import _tags

        bars = _bars([10.0, 11.0, 12.0])
        self.assertEqual(_tags(bars, bars[0][0]), [])

    def test_missing_entry_day_is_empty(self):
        from decision_track import _tags

        bars = _bars([10.0] * 30)
        self.assertEqual(_tags(bars, 20991231), [])


class TestStat(unittest.TestCase):
    def _entry(self, ret, excess=None):
        fwd = {"t5": {"ret": ret, "bench": None,
                      "excess": excess if excess is not None else ret}}
        return {"fwd": fwd}

    def test_win_rate_and_avg(self):
        from decision_track import _stat

        s = _stat([self._entry(0.1), self._entry(-0.05), self._entry(0.02)], 5)
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["win_rate"], 0.667, places=3)
        self.assertAlmostEqual(s["avg_ret"], 0.0233, places=4)

    def test_invert_for_sell(self):
        from decision_track import _stat

        # 卖出决策：个股跌 10% → 取负后 +10% = 卖对
        s = _stat([self._entry(-0.10)], 5, invert=True)
        self.assertAlmostEqual(s["avg_ret"], 0.10)
        self.assertEqual(s["win_rate"], 1.0)

    def test_skips_missing_horizon(self):
        from decision_track import _stat

        s = _stat([self._entry(0.1), {"fwd": {"t5": {"ret": None}}}], 5)
        self.assertEqual(s["n"], 1)

    def test_empty_returns_none_stats(self):
        from decision_track import _stat

        s = _stat([], 5)
        self.assertEqual(s, {"n": 0, "win_rate": None, "avg_ret": None, "avg_excess": None})


class TestScorecard(unittest.TestCase):
    def _pool(self):
        def e(agent, kind, ret, tradable=True, tags=None):
            return {"agent": agent, "kind": kind, "tradable": tradable, "tags": tags or [],
                    "fwd": {f"t{h}": {"ret": ret, "bench": 0.0, "excess": ret}
                            for h in (1, 5, 20, 60)}}
        return [
            e("a1", "bullish", 0.05),
            e("a1", "bullish", -0.02),
            e("a1", "sell", -0.08),          # 卖出后跌 → 卖对
            e("a1", "position", 0.01),
            e("a2", "bullish", -0.10),
            e("a1", "bullish", 0.99, tradable=False),   # 一字板 → 必须剔除
        ]

    def test_groups_by_agent_and_kind(self):
        from decision_track import build_scorecard

        sc = build_scorecard(self._pool(), today="2026-09-08")
        self.assertEqual(sc["generated_at"], "2026-09-08")
        a1 = sc["agents"]["a1"]
        self.assertEqual(a1["n_total"], 5)
        self.assertEqual(a1["bullish"]["n"], 2, "一字板必须从看多统计中剔除")
        self.assertEqual(a1["bullish"]["t5"]["n"], 2)
        self.assertAlmostEqual(a1["bullish"]["t5"]["win_rate"], 0.5)

    def test_sell_returns_inverted(self):
        from decision_track import build_scorecard

        sc = build_scorecard(self._pool(), today="2026-09-08")
        self.assertAlmostEqual(sc["agents"]["a1"]["sell"]["t5"]["avg_ret"], 0.08)

    def test_empty_pool_has_no_agents(self):
        from decision_track import build_scorecard

        self.assertEqual(build_scorecard([], today="2026-09-08")["agents"], {})

    def test_unmatured_decisions_report_zero_sample(self):
        from decision_track import build_scorecard

        # 刚入池、还没到 T+1 → 显示条数但各周期 n=0（不得凭空给胜率）
        sc = build_scorecard([{"agent": "a1", "kind": "bullish", "fwd": {}}], today="2026-09-08")
        blk = sc["agents"]["a1"]["bullish"]
        self.assertEqual(blk["n"], 1)
        self.assertEqual(blk["t5"]["n"], 0)
        self.assertIsNone(blk["t5"]["win_rate"])

    def test_by_tag_requires_five_samples(self):
        from decision_track import build_scorecard

        pool = [{"agent": "a1", "kind": "bullish", "tradable": True, "tags": ["追高"],
                 "fwd": {"t5": {"ret": 0.01, "bench": 0.0, "excess": 0.01}}} for _ in range(5)]
        sc = build_scorecard(pool, today="2026-09-08")
        self.assertIn("追高", sc["agents"]["a1"]["bullish"]["by_tag_t5"])

        sc4 = build_scorecard(pool[:4], today="2026-09-08")
        self.assertNotIn("by_tag_t5", sc4["agents"]["a1"]["bullish"])

    def test_write_scorecard_atomic(self):
        from decision_track import build_scorecard, write_scorecard
        import json

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sc.json"
            sc = write_scorecard(path=out, pool_path=Path(tmp) / "none.jsonl",
                                 today="2026-09-08")
            self.assertTrue(out.is_file())
            self.assertFalse((Path(tmp) / "sc.json.tmp").exists(), "临时文件必须被 replace")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["generated_at"],
                             sc["generated_at"])
            self.assertEqual(sc["agents"], {})


class TestMarketBenchmark(unittest.TestCase):
    def _panel(self):
        import pandas as pd

        return pd.DataFrame({
            "symbol": ["A", "A", "A", "B", "B", "B"],
            "dt": [20260101, 20260102, 20260103] * 2,
            "open": [10.0, 11.0, 12.0, 20.0, 22.0, 24.0],
            "close": [11.0, 12.0, 13.0, 22.0, 24.0, 26.0],
            "high": [11.5, 12.5, 13.5, 22.5, 24.5, 26.5],
            "low": [9.5, 10.5, 11.5, 19.5, 21.5, 23.5],
        })

    def test_bars_grouped_and_sorted(self):
        from decision_track import _bars_by_symbol

        bars = _bars_by_symbol(self._panel())
        self.assertEqual(sorted(bars), ["A", "B"])
        self.assertEqual([b[0] for b in bars["A"]], [20260101, 20260102, 20260103])

    def test_market_return_is_equal_weight_mean(self):
        from decision_track import _market_returns

        m = _market_returns(self._panel(), {20260101}, horizons=(1,))
        self.assertAlmostEqual(m[(20260101, 1)], 0.10, places=4)   # 两只都 +10%

    def test_market_return_skips_short_window(self):
        from decision_track import _market_returns

        m = _market_returns(self._panel(), {20260101}, horizons=(5,))
        self.assertEqual(m, {})

    def test_empty_panel_is_safe(self):
        from decision_track import _bars_by_symbol, _market_returns

        self.assertEqual(_bars_by_symbol(None), {})
        self.assertEqual(_market_returns(None, {20260101}), {})


if __name__ == "__main__":
    unittest.main()
