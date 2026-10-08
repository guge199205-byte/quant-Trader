"""实盘绩效指标（scripts/live_performance.py）。

背景：本仓库绩效指标此前全在回测侧（lab_batch_backtest）与研究侧（hypothesis_lab），
实盘账户侧一个都没有（最大回撤在全部脚本里 0 处命中）。本模块从
live_equity.jsonl + live_roundtrips.jsonl 离线算。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_performance.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_performance as P  # noqa: E402


# ---------- 最大回撤 ----------

def test_max_drawdown_finds_peak_to_trough():
    s = [("d1", 100.0), ("d2", 120.0), ("d3", 90.0), ("d4", 110.0)]
    r = P.max_drawdown(s)

    assert r["max_drawdown"] == -25.0          # 120 → 90
    assert r["peak_date"] == "d2"
    assert r["trough_date"] == "d3"


def test_max_drawdown_monotonic_rise_is_zero():
    r = P.max_drawdown([("d1", 100.0), ("d2", 110.0), ("d3", 120.0)])

    assert r["max_drawdown"] == 0.0
    assert r["peak_date"] is None


def test_max_drawdown_needs_two_points():
    assert P.max_drawdown([("d1", 100.0)])["max_drawdown"] is None


# ---------- 收益与年化抑制 ----------

def test_returns_total_and_series():
    r = P.returns_metrics([("d1", 100.0), ("d2", 110.0)])

    assert r["total_return_pct"] == 10.0
    assert r["n_days"] == 2
    assert r["n_daily_returns"] == 1


def test_annualized_and_sharpe_suppressed_on_small_sample():
    """10 天样本的年化是噪声 —— 宁可标出来，也不给看着像结论的数字。"""
    s = [(f"d{i}", 100.0 + i) for i in range(10)]
    r = P.returns_metrics(s)

    assert r["annualized_return_pct"] is None
    assert r["sharpe"] is None
    assert "无统计意义" in r["reason_annualized"]


def test_annualized_and_sharpe_present_when_sample_suffices():
    s = [(f"d{i}", 100.0 * (1.001 ** i)) for i in range(25)]
    r = P.returns_metrics(s)

    assert r["annualized_return_pct"] is not None
    assert r["sharpe"] is not None


# ---------- 交易指标 ----------

def _rt(pnl, agent="a", hold=1.0):
    return {"agent": agent, "realized_pnl": pnl, "holding_days": hold}


def test_trading_metrics_from_roundtrips():
    t = P.trading_metrics([_rt(100, hold=2.0), _rt(-50, hold=4.0), _rt(200, hold=1.0)])

    assert t["n_roundtrips"] == 3
    assert t["win_rate_pct"] == 66.67          # 2 胜 1 负
    assert t["gross_profit"] == 300.0
    assert t["gross_loss"] == 50.0
    assert t["net_pnl"] == 250.0
    assert t["payoff_ratio"] == 3.0            # 平均盈 150 / 平均亏 50
    assert t["profit_factor"] == 6.0           # 300 / 50
    assert t["avg_holding_days"] == 2.333


def test_trading_metrics_empty_gives_reason_not_zero():
    """没有回合时给原因，不给 0 —— 否则"没数据"会被读成"胜率 0%"。"""
    t = P.trading_metrics([])

    assert t["n_roundtrips"] == 0
    assert "win_rate_pct" not in t
    assert "尚无已平仓回合" in t["reason"]


def test_trading_metrics_no_losses_avoids_infinity():
    t = P.trading_metrics([_rt(100), _rt(50)])

    assert t["profit_factor"] is None          # 不写 inf
    assert t["payoff_ratio"] is None
    assert t["win_rate_pct"] == 100.0


# ---------- 记账跳变（关键：防止把口径变更当亏损） ----------

def test_discontinuity_flags_bookkeeping_jump():
    """2026-09-01 实录：flash 107,979 → 91,325（-15.4%）是分账口径调整，
    不是交易亏损；不标注就会被拿去当真实回撤决策。"""
    s = [("2026-08-31", 107978.8), ("2026-09-01", 91325.0), ("2026-09-02", 91538.0)]
    dcs = P.detect_discontinuities(s)

    assert len(dcs) == 1
    assert dcs[0]["date"] == "2026-09-01"
    assert dcs[0]["change_pct"] == -15.42
    assert "非交易损益" in dcs[0]["note"]


def test_discontinuity_ignores_normal_moves():
    s = [("d1", 100.0), ("d2", 105.0), ("d3", 99.0)]

    assert P.detect_discontinuities(s) == []


# ---------- 序列提取 ----------

def test_daily_series_takes_last_value_of_day():
    rows = [
        {"agent": "a", "date": "d1", "ts": "d1T09:00", "value": 100},
        {"agent": "a", "date": "d1", "ts": "d1T15:00", "value": 110},   # 当日最后
        {"agent": "b", "date": "d1", "ts": "d1T15:00", "value": 999},   # 别的 agent
        {"agent": "a", "date": "d2", "ts": "d2T15:00", "value": 120},
    ]
    assert P.daily_series(rows, "a") == [("d1", 110.0), ("d2", 120.0)]


def test_daily_series_tolerates_garbage():
    rows = [{"agent": "a", "date": "d1", "ts": "t", "value": "不是数字"},
            {"agent": "a", "date": "d2", "ts": "t", "value": None},
            {"agent": "a", "date": "d3", "ts": "t", "value": 100}]
    assert P.daily_series(rows, "a") == [("d3", 100.0)]
