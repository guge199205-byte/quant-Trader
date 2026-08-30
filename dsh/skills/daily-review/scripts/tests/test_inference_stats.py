"""模型推理信号复盘纯函数单元测试。

用合成数据验证：
- 信号 top-N 与当日 pct_series 的命中率/平均涨幅/超额计算
- 明日信号 top5 抽取（fusion_score 排序 + 符号归一化）
不连真库，PG 查询函数在 daily_review.py 里属 IO，此处仅测纯函数。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_stats as rs  # noqa: E402


class TestInferenceHitRate:
    """昨日推理 top-N 信号 → 今日实际涨跌的命中率复盘。"""

    def _signals(self) -> list[dict]:
        # 按 fusion_score 降序的 top-N 信号（symbol 已归一化为 suffix 格式）
        return [
            {"symbol": "300502.SZ", "name": "标A", "fusion_score": 0.074, "signal_side": "HOLD"},
            {"symbol": "688806.SH", "name": "标B", "fusion_score": 0.044, "signal_side": "HOLD"},
            {"symbol": "000733.SZ", "name": "标C", "fusion_score": 0.041, "signal_side": "HOLD"},
            {"symbol": "600563.SH", "name": "标D", "fusion_score": 0.040, "signal_side": "HOLD"},
        ]

    def test_hit_rate_computes_average_and_up_ratio(self):
        pct = pd.Series({"300502.SZ": 5.0, "688806.SH": -2.0, "000733.SZ": 3.0})
        out = rs.inference_hit_rate(self._signals(), pct)
        assert out["n"] == 3  # 600563.SH 无今日数据被跳过
        assert out["avg_pct"] == pytest.approx(2.0)  # (5-2+3)/3
        assert out["up"] == 2
        assert out["down"] == 1
        assert out["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
        assert out["missing"] == 1

    def test_excess_returns_vs_market(self):
        pct = pd.Series({"300502.SZ": 5.0, "688806.SH": -2.0, "000733.SZ": 3.0})
        out = rs.inference_hit_rate(self._signals(), pct, market_avg=0.5)
        assert out["excess_pct"] == pytest.approx(1.5)  # 2.0 - 0.5

    def test_counts_limit_up_down(self):
        pct = pd.Series({"300502.SZ": 9.98, "688806.SH": -9.9, "000733.SZ": 1.0})
        out = rs.inference_hit_rate(self._signals(), pct, category_map={"300502.SZ": "limit_up", "688806.SH": "limit_down"})
        assert out["limit_up"] == 1
        assert out["limit_down"] == 1

    def test_empty_signals(self):
        out = rs.inference_hit_rate([], pd.Series(dtype=float))
        assert out["n"] == 0
        assert out["avg_pct"] is None
        assert out["hit_rate"] == 0.0


class TestTopNSymbolNormalization:
    """fusion_score top5 抽取 + 纯数字 symbol → suffix。"""

    def _rows(self) -> list[dict]:
        return [{"symbol": s, "fusion_score": sc} for s, sc in
                [("300502", 0.02), ("688806", 0.01), ("000733", 0.009),
                 ("600563", 0.007), ("002600", 0.006), ("688538", 0.005)]]

    def test_top5_sorted_and_normalized(self):
        top = rs.top_n_signals(self._rows(), n=5)
        assert [t["symbol"] for t in top] == [
            "300502.SZ", "688806.SH", "000733.SZ", "600563.SH", "002600.SZ",
        ]
        assert all("." in t["symbol"] for t in top)

    def test_top5_respects_n(self):
        top = rs.top_n_signals(self._rows(), n=3)
        assert len(top) == 3

    def test_empty(self):
        assert rs.top_n_signals([], n=5) == []