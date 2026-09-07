"""trading_cal 休市末夜（复市前夜）判定回归测试。

晚间研究等"面向次日开盘"任务闸门：交易日 / 下一交易日就在明天（gap==1）才跑。
2026 年关键休市段（configs/trading_days.json 静态全年清单）：
  中秋 2026-09-25(法定假) + 周末 → 复市 09-28，末夜 09-27
  国庆 2026-10-01~10-07 → 复市 10-08(周四)，末夜 10-07
"""
from datetime import date

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from trading_cal import days_to_next_trading_day, is_trading_day  # noqa: E402


def gap(d: str) -> int:
    y, m, dd = map(int, d.split("-"))
    return days_to_next_trading_day(date(y, m, dd))


def test_weekend_eve_runs_and_saturday_mid_skips():
    assert gap("2026-09-12") == 2          # 周六：下一交易日周一，gap=2
    assert gap("2026-09-13") == 1          # 周日晚 = 复市前夜


def test_mid_autumn_eve():
    assert not is_trading_day(date(2026, 9, 27))
    assert gap("2026-09-27") == 1          # 中秋末夜（周一复市）


def test_national_day_eve():
    assert gap("2026-10-01") == 7          # 假期开头：远未复市
    assert gap("2026-10-05") == 3          # 假期中段
    assert gap("2026-10-07") == 1          # 国庆末夜（10-08 复市）
    assert is_trading_day(date(2026, 10, 8))
