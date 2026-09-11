"""基本面/长期趋势劣化清单（fundamental_flags）：quantdb → 买入硬拦数据底座。

背景（2026-09-11 用户口径）：「财务状况不好的、基于kuantdb、也需要、财务状况一直
不好的、也需要排除、长期下跌趋势，不管牛市，熊市、都不好的」。本测试锁定：
  - 连亏年数只数**年报**（1231），最近一年盈利即归零，缺值当断点
  - 净资产为负（资不抵债）→ 命中
  - 趋势判据必须是**相对全市场**：绝对口径会 58% 命中（市场 beta），
    相对口径才筛得出"涨得少、跌得多"的个股
  - 缓存文件结构（flags/reason/asof）与 dt ≤ asof 的防未来函数

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_fundamental_flags.py -q
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fundamental_flags as ff  # noqa: E402

ASOF = date(2026, 9, 11)


# ---------------------------------------------------------------- 连亏年数

def test_loss_streak_counts_consecutive_annual_losses():
    rows = [("20221231", -1e8), ("20231231", -2e8), ("20241231", -3e8), ("20251231", -4e8)]
    assert ff.loss_streak(rows) == 4


def test_loss_streak_resets_on_profit():
    rows = [("20231231", -2e8), ("20241231", -3e8), ("20251231", 5e7)]
    assert ff.loss_streak(rows) == 0


def test_loss_streak_stops_at_first_profit_going_back():
    rows = [("20221231", -1e8), ("20231231", 5e7), ("20241231", -3e8), ("20251231", -4e8)]
    assert ff.loss_streak(rows) == 2


def test_loss_streak_ignores_interim_reports():
    """季报不参与年度口径（1231 才是年报）。"""
    rows = [("20241231", -3e8), ("20250331", 9e8), ("20251231", -4e8)]
    assert ff.loss_streak(rows) == 2


def test_loss_streak_null_is_a_break():
    """缺值不猜：None 当断点。"""
    rows = [("20231231", -2e8), ("20241231", None), ("20251231", -4e8)]
    assert ff.loss_streak(rows) == 1


def test_loss_streak_empty_is_zero():
    assert ff.loss_streak([]) == 0


# ---------------------------------------------------------------- 取值 / 区间文案

def test_latest_value_takes_newest_period():
    assert ff.latest_value([("20241231", 1.0), ("20251231", 2.0)]) == 2.0


def test_latest_value_skips_nulls():
    assert ff.latest_value([("20241231", 1.0), ("20251231", None)]) == 1.0


def test_latest_value_none_when_empty():
    assert ff.latest_value([]) is None


def test_latest_span_formats_range():
    rows = [("20231231", -1e8), ("20241231", -1e8), ("20251231", -1e8)]
    assert ff.latest_span(rows) == "2023-2025"


def test_latest_span_single_year():
    assert ff.latest_span([("20251231", -1e8)]) == "2025"


def test_latest_span_only_covers_current_streak():
    """区间必须只覆盖**最近这段**连亏：写出「连续3年亏损（2016-2025）」是自相矛盾。"""
    rows = [("20161231", -1e8), ("20171231", 5e7), ("20221231", -1e8),
            ("20231231", -1e8), ("20241231", -1e8), ("20251231", -1e8)]
    assert ff.loss_streak(rows) == 4
    assert ff.latest_span(rows) == "2022-2025"


def test_latest_span_empty():
    assert ff.latest_span([]) == ""


# ---------------------------------------------------------------- 趋势统计

def test_trend_stats_none_when_too_short():
    assert ff.trend_stats([10.0] * (ff.MA_WINDOW + ff.MA_SLOPE_LAG - 1)) is None


def test_trend_stats_values():
    closes = [float(i + 1) for i in range(600)]  # 1..600 单调上行
    st = ff.trend_stats(closes)
    assert st["close"] == 600.0
    assert st["ma250"] == pytest.approx((351 + 600) / 2)
    assert st["ma250_prev"] == pytest.approx((291 + 540) / 2)
    assert st["ret250"] == pytest.approx(600 / 351 - 1)
    assert st["ret500"] == pytest.approx(600 / 101 - 1)


# ---------------------------------------------------------------- 财务判据

def _fin(loss_years=0, span="", net_assets=None):
    return {"loss_years": loss_years, "span": span, "net_assets": net_assets}


def test_fin_flags_loss_streak_hits():
    out = ff.fin_flags({"600001.SH": _fin(loss_years=3, span="2023-2025")}, {})
    assert "600001" in out and "连续3年亏损" in out["600001"]


def test_fin_flags_below_threshold_passes():
    assert ff.fin_flags({"600001.SH": _fin(loss_years=2)}, {}) == {}


def test_fin_flags_negative_net_assets_hits():
    out = ff.fin_flags({"600001.SH": _fin(net_assets=-3.2e8)}, {})
    assert "资不抵债" in out["600001"]


def test_fin_flags_merges_both_reasons():
    out = ff.fin_flags({"600001.SH": _fin(loss_years=4, span="2022-2025", net_assets=-1e8)}, {})
    assert "连续4年亏损" in out["600001"] and "净资产" in out["600001"]


def test_fin_flags_bad_symbol_skipped():
    assert ff.fin_flags({"BAD": _fin(loss_years=5)}, {}) == {}


# ---------------------------------------------------------------- 趋势判据（相对口径）

def _stat(close, ma250, ret250, ret500):
    return {"close": close, "ma250": ma250, "ma250_prev": ma250, "ret250": ret250,
            "ret500": ret500}


def _market_with(**overrides):
    """基准：两只横盘票（中位收益 0），目标票可覆盖。"""
    stats = {"600001.SH": _stat(10.0, 10.0, 0.0, 0.0),
             "600002.SH": _stat(10.0, 10.0, 0.0, 0.0)}
    stats.update(overrides)
    return stats


def test_trend_flags_relative_laggard_hits():
    stats = _market_with(**{"600003.SH": _stat(5.0, 10.0, -0.50, -0.50)})
    out = ff.trend_flags(stats, {})
    assert "600003" in out and "长期下跌" in out["600003"]
    assert "600001" not in out


def test_trend_flags_absolute_drop_but_market_beta_not_hit():
    """全市场都跌 40%，某票也跌 40% —— 相对中位数为 0，不是"个股差"。"""
    stats = {"600001.SH": _stat(6.0, 10.0, -0.40, -0.40),
             "600002.SH": _stat(6.0, 10.0, -0.40, -0.40),
             "600003.SH": _stat(6.0, 10.0, -0.40, -0.40)}
    assert ff.trend_flags(stats, {}) == {}


def test_trend_flags_above_ma250_not_hit():
    stats = _market_with(**{"600003.SH": _stat(12.0, 10.0, -0.50, -0.50)})
    assert ff.trend_flags(stats, {}) == {}


def test_trend_flags_short_horizon_ok_not_hit():
    """近 1 年跑输 30% 但近 2 年只跑输 5%（刚跌的，不是长期差）→ 不拦。"""
    stats = _market_with(**{"600003.SH": _stat(9.0, 10.0, -0.30, -0.05)})
    assert ff.trend_flags(stats, {}) == {}


def test_trend_flags_empty_stats():
    assert ff.trend_flags({}, {}) == {}


# ---------------------------------------------------------------- 组装 / 落盘

def _income(code, losses):
    return {code: [(f"{y}1231", -1e8) for y in losses]}


def test_build_writes_cache_with_flags(tmp_path):
    income = _income("600001.SH", (2023, 2024, 2025))       # 连亏 3 年
    balance = {"600002.SH": [("20251231", -5e8)]}           # 资不抵债
    closes = {"600001.SH": [10.0] * 600,
              "600002.SH": [10.0] * 600,
              "600003.SH": [10.0] * 599 + [5.0]}            # 长期跑输
    out = tmp_path / "fundamental_flags.json"
    st = ff.build(ASOF, {}, out, income=income, balance=balance, closes=closes)
    doc = json.loads(out.read_text(encoding="utf-8"))
    items = doc["items"]
    assert items["600001"]["flags"] == ["fin"]
    assert "连续3年亏损（2023-2025）" in items["600001"]["reason"]
    assert items["600002"]["flags"] == ["fin"] and "资不抵债" in items["600002"]["reason"]
    assert items["600003"]["flags"] == ["trend"]
    assert "600004" not in items
    assert doc["asof"] == ASOF.isoformat()
    assert st["fin"] == 2 and st["trend"] == 1 and st["total"] == 3


def test_build_healthy_stock_absent(tmp_path):
    out = tmp_path / "ff.json"
    st = ff.build(ASOF, {}, out,
                  income=_income("600001.SH", ()), balance={}, closes={})
    assert st["total"] == 0 and json.loads(out.read_text(encoding="utf-8"))["items"] == {}


# ---------------------------------------------------------------- IO（读日线，防未来）

def _write_kline(tmp_path, rows):
    import duckdb

    d = tmp_path / ff.KLINE_REL
    for day in sorted({r[1] for r in rows}):
        part = d / f"dt={day}"
        part.mkdir(parents=True, exist_ok=True)
        vals = ", ".join(f"('{s}', {dt}, {c})" for s, dt, c in rows if dt == day)
        con = duckdb.connect()
        con.execute(f"CREATE TABLE t AS SELECT * FROM (VALUES {vals}) AS v(symbol, dt, close)")
        con.execute(f"COPY t TO '{part / 'data.parquet'}' (FORMAT PARQUET)")
        con.close()


def test_read_closes_orders_and_drops_future(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_kline(tmp_path, [("600001.SH", 20260909, 10.0),
                            ("600001.SH", 20260910, 11.0),
                            ("600002.SH", 20260910, 20.0),
                            ("600001.SH", 20260912, 999.0)])  # 未来分区
    got = ff.read_closes(date(2026, 9, 10))
    assert got["600001.SH"] == [10.0, 11.0]
    assert got["600002.SH"] == [20.0]


def test_read_closes_missing_source_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path / "nope")
    assert ff.read_closes(date(2026, 9, 10)) == {}


def test_read_closes_stale_snapshot_is_dropped(tmp_path, monkeypatch):
    """后复权日线停更 → 趋势判据停用（拿一个月前的价格判"跌破年线"比不判更危险）。"""
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_kline(tmp_path, [("600001.SH", 20260801, 10.0)])
    assert ff.latest_kline_dt(date(2026, 9, 10)) == date(2026, 8, 1)
    assert ff.read_closes(date(2026, 9, 10)) == {}


def test_latest_kline_dt_ignores_future_partitions(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_kline(tmp_path, [("600001.SH", 20260910, 10.0), ("600001.SH", 20260915, 10.0)])
    assert ff.latest_kline_dt(date(2026, 9, 10)) == date(2026, 9, 10)
    assert ff.latest_kline_dt(date(2026, 7, 1)) is None


def test_trend_uses_backward_adjusted_kline():
    """趋势判据必须用后复权价：未复权会把送转/分红当跌幅（10 送 10 假跌 50%）。

    面值退市判据（risk_list.price_items）相反，必须用未复权——两处口径不能混，
    这里把"用哪份 K 线"钉死，防止后来者顺手改成 daily_unadjusted。
    """
    assert ff.KLINE_REL == "1_kline_data/daily_backward"
