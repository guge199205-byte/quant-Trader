"""基本面/长期走势劣化清单（fundamental_flags）：quantdb → 买入硬拦数据底座。

背景（2026-09-11 用户口径）：「财务状况不好的、基于kuantdb、也需要、财务状况一直
不好的、也需要排除、长期下跌趋势，不管牛市，熊市、都不好的」「长期横盘，股价
没有啥变化的。也排除」「对比最近几年的走势，是不是不好？然后不要误伤一些」。

本测试锁定：
  - 连亏年数只数**年报**（1231），最近一年盈利即归零，缺值当断点
  - 趋势判据必须是**相对全市场**：绝对口径会 58% 命中（市场 beta），
    相对口径才筛得出"涨得少、跌得多"的个股
  - 多年阴跌口径要求**每个窗口都跑输**（防"牛股回调"误伤）
  - 横盘判据要两个窗口都没动；流动性/次新用原始日线长度
  - 金融业豁免负债率（银行天然 90%+，不是风险）
  - 缓存文件结构（flags/reason/asof）与 dt ≤ asof 的防未来函数

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_fundamental_flags.py -q
"""
import json
import sys
from datetime import date
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


# ---------------------------------------------------------------- 保壳特征

def test_shell_signal_hits_when_profit_comes_from_nonrecurring():
    """扣非连亏 2 年但净利润为正 = 靠补贴/卖资产撑账面 → 保壳。"""
    rows = [("20241231", 2e7, -1e8), ("20251231", 1e7, -2e8)]
    assert ff.shell_signal(rows, 2) is True


def test_shell_signal_needs_full_window():
    """只有一年数据不算——单年扣非为负可能是正常波动。"""
    assert ff.shell_signal([("20251231", 1e7, -2e8)], 2) is False


def test_shell_signal_skips_when_net_profit_also_negative():
    """真亏损（净利也为负）走连亏判据，不算保壳特征。"""
    rows = [("20241231", -1e8, -2e8), ("20251231", -1e8, -3e8)]
    assert ff.shell_signal(rows, 2) is False


def test_shell_signal_healthy_stock_false():
    rows = [("20241231", 5e8, 4e8), ("20251231", 6e8, 5e8)]
    assert ff.shell_signal(rows, 2) is False


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
    assert st["ret750"] is None          # 600 < 750
    assert st["amp250"] is None          # 没给 high/low


def test_trend_stats_amplitude_from_high_low():
    closes = [10.0] * 600
    highs = [12.0] * 600
    lows = [8.0] * 600
    st = ff.trend_stats(closes, highs, lows)
    assert st["amp250"] == pytest.approx((12.0 - 8.0) / 10.0)
    assert st["amp500"] == pytest.approx(0.4)


# ---------------------------------------------------------------- 财务判据

def _fin(loss_years=0, span="", net_assets=None, ded_years=0, **kw):
    return {"loss_years": loss_years, "span": span, "net_assets": net_assets,
            "ded_years": ded_years, **kw}


def test_fin_flags_loss_streak_hits():
    out = ff.fin_flags({"600001.SH": _fin(loss_years=3, span="2023-2025")}, {})
    assert "600001" in out and "连续3年亏损" in out["600001"]


def test_fin_flags_below_threshold_passes():
    assert ff.fin_flags({"600001.SH": _fin(loss_years=2)}, {}) == {}


def test_fin_flags_negative_net_assets_hits():
    out = ff.fin_flags({"600001.SH": _fin(net_assets=-3.2e8)}, {})
    assert "资不抵债" in out["600001"]


def test_fin_flags_deducted_loss_hits_when_net_profit_ok():
    """净利润靠非经常性损益做正，但扣非连续 3 年亏损 → 主业已不赚钱。"""
    out = ff.fin_flags({"600001.SH": _fin(ded_years=3)}, {})
    assert "扣非连续3年亏损" in out["600001"]


def test_fin_flags_deducted_loss_suppressed_when_already_counted():
    """连亏已命中时不重复写扣非（文案冗余）。"""
    out = ff.fin_flags({"600001.SH": _fin(loss_years=3, ded_years=3)}, {})
    assert "扣非" not in out["600001"]


def test_fin_flags_goodwill_ratio_hits():
    out = ff.fin_flags({"600001.SH": _fin(net_assets=10e8, goodwill=6e8)}, {})
    assert "商誉" in out["600001"]


def test_fin_flags_goodwill_ratio_below_threshold_passes():
    assert ff.fin_flags({"600001.SH": _fin(net_assets=10e8, goodwill=4e8)}, {}) == {}


def test_fin_flags_debt_ratio_hits():
    out = ff.fin_flags({"600001.SH": _fin(tot_assets=100e8, tot_liab=90e8)}, {})
    assert "资产负债率" in out["600001"]


def test_fin_flags_finance_exempt_from_debt():
    """银行/保险负债率天然 90%+ —— 是商业模式不是风险，必须豁免。"""
    f = _fin(tot_assets=100e8, tot_liab=92e8, is_fin=True)
    assert ff.fin_flags({"600001.SH": f}, {}) == {}


def test_fin_flags_merges_all_reasons():
    out = ff.fin_flags({"600001.SH": _fin(loss_years=4, span="2022-2025", net_assets=-1e8)}, {})
    assert "连续4年亏损" in out["600001"] and "净资产" in out["600001"]


def test_fin_flags_bad_symbol_skipped():
    assert ff.fin_flags({"BAD": _fin(loss_years=5)}, {}) == {}


def test_shell_flags_from_snapshot():
    out = ff.shell_flags({"600001.SH": {"is_shell": True, "shell_years": 2}}, {})
    assert "保壳特征" in out["600001"]
    assert ff.shell_flags({"600001.SH": {"is_shell": False}}, {}) == {}


# ---------------------------------------------------------------- 趋势判据（相对口径）

def _stat(close, ma250, ret250, ret500, ret750=None, amp250=0.5, amp500=0.8, n=760):
    return {"close": close, "ma250": ma250, "ma250_prev": ma250, "ret250": ret250,
            "ret500": ret500, "ret750": ret750, "amp250": amp250, "amp500": amp500, "n": n}


def _market_with(**overrides):
    """基准：两只横盘票（中位收益 0、振幅大不算横盘），目标票可覆盖。"""
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


def test_trend_flags_multi_year_slow_bleed_hits():
    """多年阴跌：3 年跑输 90%、2 年 32%、1 年也跑输 → 拦（急跌口径没命中也拦）。"""
    stats = {"600001.SH": _stat(10.0, 10.0, 0.0, 0.0, ret750=0.5),
             "600002.SH": _stat(10.0, 10.0, 0.0, 0.0, ret750=0.5),
             "600003.SH": _stat(5.0, 10.0, -0.10, -0.32, ret750=-0.40)}
    out = ff.trend_flags(stats, {})
    assert "多年阴跌" in out["600003"]


def test_trend_flags_multi_year_needs_every_window():
    """3/2 年都很差但近 1 年已跑赢（正在修复）→ 不拦，避免踩在反转点上。"""
    stats = {"600001.SH": _stat(10.0, 10.0, 0.0, 0.0, ret750=0.5),
             "600002.SH": _stat(10.0, 10.0, 0.0, 0.0, ret750=0.5),
             "600003.SH": _stat(5.0, 10.0, 0.05, -0.32, ret750=-0.40)}
    assert ff.trend_flags(stats, {}) == {}


def test_trend_flags_empty_stats():
    assert ff.trend_flags({}, {}) == {}


# ---------------------------------------------------------------- 横盘判据

def test_flat_flags_two_year_flat_hits():
    """两年不动（涨跌 ±10% 内、振幅 20% 内）→ 没有交易价值。"""
    stats = _market_with(**{"600003.SH": _stat(10.0, 10.0, 0.03, -0.05,
                                               amp250=0.15, amp500=0.35)})
    out = ff.flat_flags(stats, {})
    assert "长期横盘" in out["600003"]


def test_flat_flags_one_year_flat_but_two_year_moved_passes():
    """只看近一年横盘可能是蓄势；两年不动才是死水。"""
    stats = _market_with(**{"600003.SH": _stat(10.0, 10.0, 0.03, 0.60,
                                               amp250=0.15, amp500=1.2)})
    assert ff.flat_flags(stats, {}) == {}


def test_flat_flags_volatile_stock_passes():
    stats = _market_with(**{"600003.SH": _stat(10.0, 10.0, 0.03, -0.05,
                                               amp250=0.9, amp500=1.4)})
    assert ff.flat_flags(stats, {}) == {}


def test_flat_flags_missing_amplitude_skipped():
    """没有 high/low（只给了 close）→ 不敢判横盘。"""
    stats = _market_with(**{"600003.SH": _stat(10.0, 10.0, 0.03, -0.05,
                                               amp250=None, amp500=None)})
    assert ff.flat_flags(stats, {}) == {}


# ---------------------------------------------------------------- 流动性 / 次新

def test_liquidity_flag_below_floor_hits():
    """近 60 日日均成交额 1200 万 < 2000 万 → 买卖价差大、跌停时出不来。"""
    assert "流动性枯竭" in ff.liquidity_flag({"amt": 1200.0, "n": 600}, {})


def test_liquidity_flag_above_floor_passes():
    assert ff.liquidity_flag({"amt": 13600.0, "n": 600}, {}) == ""


def test_liquidity_flag_short_history_passes():
    """次新股没有 60 日成交额 —— 由次新判据处理，流动性判据不越权。"""
    assert ff.liquidity_flag({"amt": 100.0, "n": 30}, {}) == ""


def test_liquidity_flag_zero_disables():
    assert ff.liquidity_flag({"amt": 1.0, "n": 600}, {"min_amount_yi": 0}) == ""


def test_new_stock_flag_hits_below_threshold():
    assert "次新股" in ff.new_stock_flag(45, {})


def test_new_stock_flag_passes_at_threshold():
    assert ff.new_stock_flag(60, {}) == ""


def test_new_stock_flag_zero_disables():
    assert ff.new_stock_flag(10, {"new_stock_days": 0}) == ""


# ---------------------------------------------------------------- 组装 / 落盘

def _income(code, losses):
    """{symbol: [(报告期, 净利润, 扣非)]}（income/balance 的三/五元组形态）。

    注意：balance 行是 (报告期, 净资产, 商誉, 总资产, 总负债) 五元组。
    """
    return {code: [(f"{y}1231", -1e8, -1e8) for y in losses]}


def _day(closes, amount=1e4):
    """单票日线字典（high/low 缺省=close；amount 单位万元，默认 1 亿）。"""
    return {"close": closes, "high": list(closes), "low": list(closes),
            "amount": [amount] * len(closes)}


_UP = [10.0 + 0.05 * i for i in range(600)]   # 上行且振幅足够（不落横盘/趋势）
_DOWN = [10.0] * 599 + [5.0]                  # 长期跑输（破年线）


def test_build_writes_cache_with_flags(tmp_path):
    income = _income("600001.SH", (2023, 2024, 2025))       # 连亏 3 年
    balance = {"600002.SH": [("20251231", -5e8, None, None, None)]}   # 资不抵债
    daily = {"600001.SH": _day(_UP), "600002.SH": _day(_UP),
             "600003.SH": _day(_DOWN)}
    out = tmp_path / "fundamental_flags.json"
    st = ff.build(ASOF, {}, out, income=income, balance=balance, daily=daily)
    doc = json.loads(out.read_text(encoding="utf-8"))
    items = doc["items"]
    assert items["600001"]["flags"] == ["fin"]
    assert "连续3年亏损（2023-2025）" in items["600001"]["reason"]
    assert items["600002"]["flags"] == ["fin"] and "资不抵债" in items["600002"]["reason"]
    assert items["600003"]["flags"] == ["trend"]
    assert "600004" not in items
    assert doc["asof"] == ASOF.isoformat()
    assert st["fin"] == 2 and st["trend"] == 1 and st["total"] == 3


def test_build_flags_illiquid_and_new_stock(tmp_path):
    daily = {"600001.SH": _day([10.0] * 400, amount=500.0),   # 日均 500 万
             "600002.SH": _day([10.0] * 45, amount=1e4)}      # 上市 45 日
    out = tmp_path / "ff.json"
    st = ff.build(ASOF, {}, out, income={}, balance={}, daily=daily)
    items = json.loads(out.read_text(encoding="utf-8"))["items"]
    assert items["600001"]["flags"] == ["illiquid"]
    assert items["600002"]["flags"] == ["new"]
    assert st["illiquid"] == 1 and st["new"] == 1


def test_build_healthy_stock_absent(tmp_path):
    out = tmp_path / "ff.json"
    st = ff.build(ASOF, {}, out,
                  income=_income("600001.SH", ()), balance={}, daily={})
    assert st["total"] == 0 and json.loads(out.read_text(encoding="utf-8"))["items"] == {}


# ---------------------------------------------------------------- IO（读日线，防未来）

def _write_kline(tmp_path, rows):
    """rows: (symbol, dt, close[, high, low, amount]) → quantdb 风格分区 parquet。"""
    import duckdb

    d = tmp_path / ff.KLINE_REL
    for day in sorted({r[1] for r in rows}):
        part = d / f"dt={day}"
        part.mkdir(parents=True, exist_ok=True)
        vals = ", ".join(
            "('{s}', {dt}, {c}, {h}, {l}, {a})".format(
                s=r[0], dt=r[1], c=r[2],
                h=r[3] if len(r) > 3 else r[2],
                l=r[4] if len(r) > 4 else r[2],
                a=r[5] if len(r) > 5 else 1e4)
            for r in rows if r[1] == day)
        con = duckdb.connect()
        con.execute(f"CREATE TABLE t AS SELECT * FROM (VALUES {vals}) "
                    f"AS v(symbol, dt, high, low, close, amount)")
        con.execute(f"COPY t TO '{part / 'data.parquet'}' (FORMAT PARQUET)")
        con.close()


def test_read_daily_orders_and_drops_future(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_kline(tmp_path, [("600001.SH", 20260909, 10.0),
                            ("600001.SH", 20260910, 11.0),
                            ("600002.SH", 20260910, 20.0),
                            ("600001.SH", 20260912, 999.0)])  # 未来分区
    got = ff.read_daily(date(2026, 9, 10))
    assert got["600001.SH"]["close"] == [10.0, 11.0]
    assert got["600001.SH"]["amount"] == [1e4, 1e4]
    assert got["600002.SH"]["close"] == [20.0]


def test_read_daily_missing_source_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path / "nope")
    assert ff.read_daily(date(2026, 9, 10)) == {}


def test_read_daily_stale_snapshot_is_dropped(tmp_path, monkeypatch):
    """后复权日线停更 → 趋势判据停用（拿一个月前的价格判"跌破年线"比不判更危险）。"""
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_kline(tmp_path, [("600001.SH", 20260801, 10.0)])
    assert ff.latest_kline_dt(date(2026, 9, 10)) == date(2026, 8, 1)
    assert ff.read_daily(date(2026, 9, 10)) == {}


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


def _write_income(tmp_path, rows):
    """rows: (Symbol, m_timetag, m_anntime, np, ded) → income 风格 parquet。"""
    import duckdb

    d = tmp_path / "3_financial_data" / "income"
    d.mkdir(parents=True, exist_ok=True)
    vals = ", ".join(f"('{s}', '{t}', '{a}', {np_}, {dd})"
                     for s, t, a, np_, dd in rows)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM (VALUES {vals}) AS v("
                f"Symbol, m_timetag, m_anntime, net_profit_excl_min_int_inc, "
                f"deducted_net_profit)")
    con.execute(f"COPY t TO '{d / '600001.SH.parquet'}' (FORMAT PARQUET)")
    con.close()


def test_read_income_dedupes_restatements_by_anntime(tmp_path, monkeypatch):
    """财报重述（同报告期多版本）不能数成两个年度——loss_streak 按行数计数。"""
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_income(tmp_path, [
        ("600001.SH", "20231231", "20240330", -1e8, -1e8),
        ("600001.SH", "20241231", "20250330", -1e8, -1e8),
        ("600001.SH", "20241231", "20260330", -2e8, -2e8),   # 重述版（公告更晚）
        ("600001.SH", "20251231", "20260330", -1e8, -1e8),
    ])
    rows = ff.read_income(ASOF)["600001.SH"]
    assert len(rows) == 3                              # 只留每个报告期的最新版本
    assert ("20241231", -2e8, -2e8) in rows            # 取重述后的数字
    assert ff.loss_streak([(t, v) for t, v, _ in rows]) == 3


def test_read_income_excludes_future_announcements(tmp_path, monkeypatch):
    """公告日晚于 asof 的报表不可见（防未来函数）。"""
    monkeypatch.setattr(ff, "QUANTDB", tmp_path)
    _write_income(tmp_path, [
        ("600001.SH", "20251231", "20260330", -1e8, -1e8),
        ("600001.SH", "20261231", "20270330", -9e8, -9e8),   # 未来
    ])
    rows = ff.read_income(ASOF)["600001.SH"]
    assert rows == [("20251231", -1e8, -1e8)]


def test_read_income_missing_source_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "QUANTDB", tmp_path / "nope")
    assert ff.read_income(ASOF) == {}


def test_conf_overrides_and_bad_values_fall_back(tmp_path):
    p = tmp_path / "live_symbols.json"
    p.write_text(json.dumps({"risk": {"min_amount_yi": 0.5, "loss_years": "脏值"}}),
                 encoding="utf-8")
    conf = ff.load_conf(p)
    assert conf["min_amount_yi"] == 0.5
    assert conf["loss_years"] == ff.DEFAULTS["loss_years"]
