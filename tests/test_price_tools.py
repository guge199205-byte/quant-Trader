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


class TestLocalFunctionDateValidation:
    """`get_price_local_function` 调了一个**不存在的** `_validate_date`。

    它在 `try/except ValueError` 里，而 `NameError` 不是 `ValueError` 的子类 →
    异常直接穿出函数，调用方拿到的是 traceback 而不是 `{"error": ...}`。同类
    （同一份 merged.jsonl、同一个 YYYY-MM-DD 口径）的 `get_price_local_daily`
    用的是 `_validate_date_daily`，这里本意也是它。
    """

    def test_bad_date_returns_a_structured_error(self):
        from agent_tools.tool_get_price_local import get_price_local_function

        out = get_price_local_function("600519.SH", "2026/09/22")
        assert out["error"] == "date must be in YYYY-MM-DD format"

    def test_valid_date_does_not_raise_nameerror(self):
        from agent_tools.tool_get_price_local import get_price_local_function

        out = get_price_local_function("600519.SH", "2026-09-22")
        # 数据可能不在（那也是一种结构化答复）；唯独不该是 NameError
        assert out.get("error") != "name '_validate_date' is not defined"
