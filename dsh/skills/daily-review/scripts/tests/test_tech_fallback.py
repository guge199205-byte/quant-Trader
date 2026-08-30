"""load_tech 兜底逻辑单元测试。

tech_ind 官方分区缺行时（供应商发布中断），用 daily_backward 后复权序列
计算 pct_change/ma5/ma20 兜底。核心不变量：
  后复权相邻收盘比 = 官方涨跌幅（含除权除息口径，已与官方 tech_ind 交叉验证）。
纯函数测试：不依赖真实 parquet。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daily_review as dr  # noqa: E402


def _closes(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "dt", "close"])


DTS_5 = ["20260810", "20260811", "20260812", "20260813", "20260814"]


class TestTechFallback:
    def test_pct_simple(self):
        closes = _closes(
            [("600000.SH", "20260813", 10.0), ("600000.SH", "20260814", 10.5)]
        )
        fb = dr.tech_fallback_from_backward(closes, ["20260813", "20260814"])
        row = fb[fb["dt"] == "20260814"].iloc[0]
        assert row["pct_change"] == pytest.approx(5.0)
        # 历史不足 5/20 日 → ma 缺失，不编造
        assert pd.isna(row["ma5"])
        assert pd.isna(row["ma20"])

    def test_ex_div_backward_ratio_is_official_pct(self):
        # 除权日：后复权序列相邻比 = 官方涨跌幅（不复权收盘比会错）
        closes = _closes(
            [("000001.SZ", "20260813", 5.0), ("000001.SZ", "20260814", 5.1)]
        )
        fb = dr.tech_fallback_from_backward(closes, ["20260813", "20260814"])
        row = fb[fb["dt"] == "20260814"].iloc[0]
        assert row["pct_change"] == pytest.approx(2.0)

    def test_suspended_day_ffill_zero_pct(self):
        # 0813 停牌缺行：ffill 后 pct=0，且参与均线
        closes = _closes(
            [
                ("000002.SZ", "20260810", 8.0),
                ("000002.SZ", "20260811", 8.1),
                ("000002.SZ", "20260812", 8.0),
                ("000002.SZ", "20260814", 8.2),
            ]
        )
        fb = dr.tech_fallback_from_backward(closes, DTS_5)
        r13 = fb[fb["dt"] == "20260813"].iloc[0]
        r14 = fb[fb["dt"] == "20260814"].iloc[0]
        assert r13["pct_change"] == pytest.approx(0.0)
        assert r14["pct_change"] == pytest.approx(2.5)
        assert r14["ma5"] == pytest.approx((8.0 + 8.1 + 8.0 + 8.0 + 8.2) / 5)

    def test_ma5_window_includes_today(self):
        closes = _closes(
            [("600002.SH", d, float(10 + i)) for i, d in enumerate(DTS_5)]
        )
        fb = dr.tech_fallback_from_backward(closes, DTS_5)
        row = fb[fb["dt"] == "20260814"].iloc[0]
        assert row["ma5"] == pytest.approx(12.0)  # 10..14 均值

    def test_first_day_dropped_no_prev(self):
        closes = _closes([("000003.SZ", "20260814", 3.0)])
        fb = dr.tech_fallback_from_backward(closes, ["20260814"])
        assert fb.empty

    def test_duplicate_rows_tolerated(self):
        closes = _closes(
            [
                ("600001.SH", "20260813", 4.0),
                ("600001.SH", "20260813", 4.0),
                ("600001.SH", "20260814", 4.4),
            ]
        )
        fb = dr.tech_fallback_from_backward(closes, ["20260813", "20260814"])
        row = fb[fb["dt"] == "20260814"].iloc[0]
        assert row["pct_change"] == pytest.approx(10.0)
