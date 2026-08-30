"""daily-review 复盘统计核心逻辑单元测试。

纯函数测试：不依赖真实 parquet，全部用合成数据。
涨跌停规则由 backend/services/trade/simulation/services/local_market_data.py
（compute_limits/limit_pct）背书，这里验证 review_stats 的封装逻辑。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_stats as rs  # noqa: E402

D = date(2026, 8, 14)
PRE_RELAX = date(2026, 7, 1)


# ---------- 涨跌停判定（价格精确 + pct 兜底） ----------

class TestLimitClassification:
    def test_price_limit_up_exact(self):
        assert rs.classify_price(72.81, 72.81, 72.81, 59.57) == "limit_up"

    def test_price_broke_up(self):
        # 盘中触及涨停价但收盘未封 → 炸板
        assert rs.classify_price(70.5, 72.9, 72.81, 59.57) == "broke_up"
        assert rs.classify_price(70.5, 72.0, 72.81, 59.57) == "normal"

    def test_price_limit_down(self):
        assert rs.classify_price(59.57, 60.5, 72.81, 59.57) == "limit_down"

    def test_price_no_limit_new_stock(self):
        # 新股无涨跌幅限制：up_price=0 → 不判涨停
        assert rs.classify_price(30.0, 31.0, 0.0, 0.0) == "normal"

    def test_corp_action_pct(self):
        # |pct| 显著超过板块限制 → 除权/拆并股
        assert rs.is_corp_action_pct(-74.3, 0.20) is True
        assert rs.is_corp_action_pct(-42.16, 0.10) is True
        # 正常跌停区间不是公司行为
        assert rs.is_corp_action_pct(-10.0, 0.10) is False
        assert rs.is_corp_action_pct(20.0, 0.20) is False

    def test_pct_main_board(self):
        assert rs.classify_by_pct(10.0, "601138.SH", False, D) == "limit_up"
        assert rs.classify_by_pct(9.7, "601138.SH", False, D) == "limit_up"
        assert rs.classify_by_pct(9.4, "601138.SH", False, D) == "up"
        assert rs.classify_by_pct(-10.0, "601138.SH", False, D) == "limit_down"
        assert rs.classify_by_pct(-9.6, "601138.SH", False, D) == "limit_down"
        assert rs.classify_by_pct(-9.0, "601138.SH", False, D) == "down"

    def test_pct_st_pre_relax_5pct(self):
        assert rs.classify_by_pct(5.0, "600301.SH", True, PRE_RELAX) == "limit_up"
        assert rs.classify_by_pct(4.4, "600301.SH", True, PRE_RELAX) == "up"
        # 2026-07-06 起主板 ST 放宽到 ±10%
        assert rs.classify_by_pct(9.9, "600301.SH", True, D) == "limit_up"

    def test_pct_growth_20pct(self):
        assert rs.classify_by_pct(20.0, "300750.SZ", False, D) == "limit_up"
        assert rs.classify_by_pct(19.6, "300750.SZ", False, D) == "limit_up"
        assert rs.classify_by_pct(12.0, "688981.SH", False, D) == "up"

    def test_pct_bse_30pct(self):
        assert rs.classify_by_pct(30.0, "832000.BJ", False, D) == "limit_up"
        assert rs.classify_by_pct(29.2, "832000.BJ", False, D) == "limit_up"
        assert rs.classify_by_pct(28.0, "832000.BJ", False, D) == "up"


# ---------- 连板 ----------

class TestStreak:
    def test_streak_count(self):
        # 数组尾 = 最新交易日；今日涨停，往前连续 3 板
        assert rs.streak_from_tail([-3.0, 10.0, 10.0, 9.99], 9.5) == 3

    def test_streak_broken(self):
        assert rs.streak_from_tail([10.0, -2.0, 10.0], 9.5) == 1

    def test_streak_st_board_threshold(self):
        assert rs.streak_from_tail([5.1, 5.0, 4.99], 4.5) == 3

    def test_streak_none_tolerant(self):
        assert rs.streak_from_tail([10.0, None, 10.0], 9.5) == 1


# ---------- 涨跌分布 ----------

class TestBreadth:
    def test_buckets(self):
        pct = pd.Series(
            [10.1, 7.5, 5.0, 3.2, 1.5, 0.5, 0.0, -0.4, -1.2, -2.5, -4.5, -7.1, -10.2, 4.0]
        )
        dist = rs.breadth_distribution(pct)
        assert dist["涨停"] == 1
        assert dist[">7"] == 1
        assert dist["5~7"] == 1
        assert dist["3~5"] == 2
        assert dist["1~3"] == 1
        assert dist["0~1"] == 1
        assert dist["平盘"] == 1
        assert dist["跌停"] == 1
        assert dist["<-7"] == 1
        assert sum(dist.values()) == 14

    def test_market_breadth(self):
        pct = pd.Series([1.0, -1.0, 0.0, 2.0])
        r = rs.market_breadth(pct)
        assert r["up_count"] == 2
        assert r["down_count"] == 1
        assert r["flat_count"] == 1
        assert r["up_down_ratio"] == pytest.approx(2.0)


# ---------- 板块聚合 ----------

class TestSector:
    def _members(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "SectorCode": ["BK1", "BK1", "BK2"],
                "SectorName": ["行业A", "行业A", "行业B"],
                "SectorType": ["行业板块(一级)"] * 3,
                "Symbol": ["600000.SH", "600001.SH", "600002.SH"],
            }
        )

    def test_equal_weight_average(self):
        pct = pd.Series({"600000.SH": 5.0, "600001.SH": 3.0, "600002.SH": -1.0})
        out = rs.sector_aggregate(self._members(), pct, None)
        a = out[out["SectorName"] == "行业A"].iloc[0]
        assert a["n"] == 2
        assert a["avg_pct"] == pytest.approx(4.0)
        assert a["mv_weighted_pct"] is None

    def test_mv_weighted(self):
        pct = pd.Series({"600000.SH": 10.0, "600001.SH": 0.0})
        mv = pd.Series({"600000.SH": 3e11, "600001.SH": 1e11})
        out = rs.sector_aggregate(self._members(), pct, mv)
        a = out[out["SectorName"] == "行业A"].iloc[0]
        assert a["mv_weighted_pct"] == pytest.approx(7.5)

    def test_duplicate_member_kept_once(self):
        members = pd.concat([self._members(), self._members().iloc[[0]]], ignore_index=True)
        pct = pd.Series({"600000.SH": 5.0, "600001.SH": 3.0, "600002.SH": -1.0})
        out = rs.sector_aggregate(members, pct, None)
        assert out["n"].sum() == 3

    def test_missing_pct_dropped(self):
        pct = pd.Series({"600001.SH": 3.0})
        out = rs.sector_aggregate(self._members(), pct, None)
        a = out[out["SectorName"] == "行业A"].iloc[0]
        assert a["n"] == 1
        assert a["avg_pct"] == pytest.approx(3.0)


# ---------- 单位换算 ----------

class TestUnits:
    def test_wan_to_yi(self):
        assert rs.wan_to_yi(703474.0) == pytest.approx(70.35, abs=0.01)
        assert rs.wan_to_yi(None) is None

    def test_fmt_yi(self):
        assert rs.fmt_yi(123456.7) == "12.35 亿元"
        assert rs.fmt_yi(None) == "[数据缺失]"

    def test_volume_ratio(self):
        prior = [100.0, 110.0, 90.0, 120.0, 130.0]
        assert rs.volume_ratio_5(300.0, prior) == pytest.approx(2.73, abs=0.01)
        assert rs.volume_ratio_5(300.0, []) is None


# ---------- 除权日检测 ----------

class TestExDiv:
    def test_normal_day(self):
        assert rs.is_ex_div(1.4717, 66.19, 65.23) is False

    def test_ex_div_day(self):
        assert rs.is_ex_div(1.5, 10.0, 10.5) is True

    def test_missing_prev_close(self):
        assert rs.is_ex_div(1.5, 10.0, None) is False