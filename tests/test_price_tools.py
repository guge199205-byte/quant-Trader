"""价格工具与数据源测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.price_tools import (
    _ts_key_matches_date,
    get_all_trading_days,
    is_trading_day,
)
from agent_tools.datasources import available_datasources, get_datasource


class TestTsKeyMatching:
    def test_daily_exact(self):
        assert _ts_key_matches_date("2025-10-30", "2025-10-30")
        assert not _ts_key_matches_date("2025-10-31", "2025-10-30")

    def test_hourly_prefix(self):
        assert _ts_key_matches_date("2025-10-30 15:00:00", "2025-10-30")
        assert not _ts_key_matches_date("2025-10-30 15:00:00", "2025-10-31")


class TestTradingDays:
    def test_us_hourly_data(self):
        # 仓库数据为小时级，纯日期应匹配（回归：is_trading_day 兼容性）
        assert is_trading_day("2025-10-30", market="us")
        assert not is_trading_day("2025-11-01", market="us")  # 周六

    def test_cn_daily_data(self):
        assert is_trading_day("2025-10-09", market="cn")

    def test_hk_daily_data(self):
        assert is_trading_day("2025-10-09", market="hk")
        assert not is_trading_day("2025-10-11", market="hk")  # 周六

    def test_all_days_sorted(self):
        days = get_all_trading_days(market="hk")
        assert len(days) > 100
        assert days == sorted(days)


class TestDataSources:
    def test_registry(self):
        assert "local" in available_datasources()
        assert "tdx" in available_datasources()

    def test_local_quote(self):
        ds = get_datasource("local")
        q = ds.get_quote("00700.HK", "2025-10-09", market="hk")
        assert q and q.get("buy price") is not None

    def test_local_trading_day(self):
        ds = get_datasource("local")
        assert ds.is_trading_day("2025-10-09", market="hk")
        assert not ds.is_trading_day("2025-11-01", market="us")

    def test_local_trading_days(self):
        ds = get_datasource("local")
        days = ds.get_trading_days(market="hk")
        assert len(days) > 100
