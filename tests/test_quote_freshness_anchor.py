"""行情新鲜度锚点的唯一出处（P2）。

同一句判据（「日K 最后一根 bar 的日期 ≥ 锚点」）在仓里有三份实现，各自拿
「今天」当锚点：
  * `live_hourly_analysis.market_data_stale`（主闸：提示词禁价 + 延期单暂缓）
  * `dsh/skills/realtime-quotes-tdx/scripts/tdx_realtime.py`（weekday 算术）
  * 桥侧 `brokers/tdx-bridge/src/tdx/client.py`（docstring 自承节假日误报）

「今天」只在「今天确实该有 bar」的时候才是锚点。锚点真正的含义是
**此刻最新一根日K 应该是什么日期** —— 它取决于日期与时刻两个输入，所以它属于
交易日历，不属于任何一个调用点。本文件把那个定义钉住。

判据方向**不动**：取不到/抛异常仍是 fail-open（视为新鲜）。改成 fail-closed 会
在桥抖动时禁止报价格、暂缓延期单，风险方向是阻塞交易。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import trading_cal as TC  # noqa: E402

BJ = ZoneInfo("Asia/Shanghai")


def _at(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=BJ)


# 2026-09 已知日历：09-21 周一、09-22 周二为交易日。
# 中秋 2026-09-25（周五）休市；09-26/27 周末。
TRADING_DAY = (2026, 9, 22)
FRI_HOLIDAY = (2026, 9, 25)
SATURDAY = (2026, 9, 26)


class TestAnchorOnTradingDay:
    def test_before_the_open_anchor_stays_at_previous_day(self):
        """09:15 还没开盘，今天的日K 本来就不该存在 —— 锚点是上一交易日。

        旧实现此时不判（窗口 9:25 起），但 9:25-9:30 这段是真空档：集合竞价已
        开始、日K 还没生成，拿「今天」当锚点会假报停更。
        """
        assert TC.expected_bar_date(_at(*TRADING_DAY, 9, 15)) == "20260921"
        assert TC.expected_bar_date(_at(*TRADING_DAY, 9, 26)) == "20260921"

    def test_after_the_open_anchor_is_today(self):
        for hh, mm in ((9, 30), (10, 30), (14, 59)):
            assert TC.expected_bar_date(_at(*TRADING_DAY, hh, mm)) == "20260922"

    def test_after_the_close_anchor_stays_at_today(self):
        """收盘后今天的 bar 已经定稿，锚点仍是今天 —— 次日才回退。"""
        assert TC.expected_bar_date(_at(*TRADING_DAY, 15, 30)) == "20260922"
        assert TC.expected_bar_date(_at(*TRADING_DAY, 23, 0)) == "20260922"


class TestAnchorOnNonTradingDay:
    """2026-09 实测日历：21/22/23/24 交易日，25 中秋休市，26/27 周末。

    故 25 日与 26 日的锚点都是 **09-24**（最近一个交易日），不是 09-21。
    """

    def test_holiday_falls_back_to_previous_trading_day(self):
        """中秋休市：锚点必须是上一个交易日，否则整个假日都在假报停更。"""
        anchor = TC.expected_bar_date(_at(*FRI_HOLIDAY, 11, 0))
        assert anchor != "20260925", "节假日拿今天当锚点"
        assert anchor == "20260924"

    def test_weekend_falls_back_over_the_holiday_to_thursday(self):
        """周六回看的路上还隔着中秋休市 —— 锚点要穿过它落到周四。"""
        assert TC.expected_bar_date(_at(*SATURDAY, 11, 0)) == "20260924"

    def test_holiday_ignores_time_of_day(self):
        """休市日整天都该回退：11 点是盘中也一样（否则 9:30 前后报两种状态）。"""
        for hh in (0, 9, 15, 23):
            assert TC.expected_bar_date(_at(*FRI_HOLIDAY, hh, 0)) == "20260924"


class TestCalendarCoverageGap:
    def test_beyond_coverage_falls_back_to_weekday_rule(self):
        """日历只覆盖到某天；越界时 is_trading_day 退回星期判断，锚点要跟着走。"""
        far = _at(2099, 3, 4, 11, 0)          # 周三，远在覆盖之外
        assert TC.expected_bar_date(far) == "20990304"
        assert TC.expected_bar_date(_at(2099, 3, 7, 11, 0)) == "20990306"   # 周六 → 周五
