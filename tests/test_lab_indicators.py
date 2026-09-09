"""指标捕获：纯函数单测 + 一次真回测的端到端校验。

纯函数部分不碰 pynecore；端到端部分需要 pine_lab 的 pynecore 与 quantdb 行情，
缺任一就 skip（本机开发机都有）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import lab_indicators as li   # noqa: E402

_PINE_SITE = ROOT / "pine_lab/.venv/lib/python3.14/site-packages"
if _PINE_SITE.is_dir() and str(_PINE_SITE) not in sys.path:
    sys.path.append(str(_PINE_SITE))


# ---------- 纯函数 ----------


class TestNum:
    def test_nan_and_inf_and_bool_are_none(self):
        assert li._num(float("nan")) is None
        assert li._num(float("inf")) is None
        assert li._num(True) is None
        assert li._num(None) is None
        assert li._num("x") is None

    def test_rounds_to_four_decimals(self):
        assert li._num(1.23456789) == 1.2346
        assert li._num(3) == 3


class TestStableArgs:
    def test_keeps_periods_drops_prices(self):
        calls = [((107.66, 20), 1.0), ((108.02, 20), 2.0)]
        assert li._stable_args(calls) == "(20)"

    def test_drops_args_that_change_between_bars(self):
        calls = [((100.0, 20), 1.0), ((101.0, 20), 2.0)]
        assert li._stable_args(calls) == "(20)"

    def test_keeps_whole_float_but_not_fractional(self):
        assert li._stable_args([((1.0, 2.0), 0), ((1.0, 2.0), 0)]) == "(1,2)"
        assert li._stable_args([((1.0, 0.5), 0), ((1.0, 0.5), 0)]) == "(1)"

    def test_empty(self):
        assert li._stable_args([]) == ""


class TestSourceValue:
    """pynecore 给 sma/ema/rsi/macd 在实参最前面塞状态对象，源值要跳过它。"""

    def test_skips_injected_state_object(self):
        assert li._source_value((["state"], 16.29, 8.0)) == 16.29

    def test_plain_source_stays_first(self):
        assert li._source_value((62.34, 14)) == 62.34

    def test_na_source_is_none(self):
        assert li._source_value((float("nan"), 14)) is None


class TestSeries:
    def _hit(self, name, by_bar, line=1):
        return {f"{name}@{line}@0": {"name": name, "line": line, "by_bar": by_bar}}

    def test_macd_tuple_splits_into_three(self):
        hit = self._hit("macd", {i: [((100.0 + i, 12, 26, 9), (0.1 * i, 0.05 * i, 0.2 * i))]
                                 for i in range(4)})
        out = li.build(hit, 4)["panes"]
        assert [s["label"] for s in out] == ["MACD DIF(12,26,9)", "MACD DEA(12,26,9)",
                                             "MACD 柱(12,26,9)"]
        assert [s["kind"] for s in out] == ["line", "line", "hist"]
        assert out[0]["values"] == [0.0, 0.1, 0.2, 0.3]

    def test_same_line_three_periods_get_separate_series(self):
        # 第一个实参是价格（每根都变）→ 不该进标签；周期才是要显示的
        by_bar = {i: [((100.0 + i, 20), 1.0 + i), ((100.0 + i, 50), 2.0 + i),
                      ((100.0 + i, 100), 3.0 + i)] for i in range(3)}
        out = li.build(self._hit("highest", by_bar), 3)["overlays"]
        assert [s["label"] for s in out] == ["区间高点1(20)", "区间高点2(50)", "区间高点3(100)"]
        assert out[2]["values"] == [3.0, 4.0, 5.0]

    def test_constant_and_all_na_series_dropped(self):
        by_bar = {0: [((1.0,), 5.0)], 1: [((1.0,), 5.0)], 2: [((1.0,), 5.0)]}
        assert li.build(self._hit("rsi", by_bar), 3)["panes"] == []

    def test_missing_bars_are_null(self):
        by_bar = {0: [((1.0,), 1.0)], 2: [((1.0,), 3.0)]}
        out = li.build(self._hit("rsi", by_bar), 4)["panes"]
        assert out[0]["values"] == [1.0, None, 3.0, None]

    def test_signal_functions_ignored(self):
        by_bar = {i: [((1.0,), True)] for i in range(3)}
        assert li.build(self._hit("crossover", by_bar), 3) == {
            "bars": 3, "overlays": [], "panes": []}

    def test_duplicate_series_kept_once(self):
        by_bar = {i: [((1.0,), float(i)), ((1.0,), float(i))] for i in range(3)}
        out = li.build(self._hit("rsi", by_bar), 3)["panes"]
        assert len(out) == 1

    def test_caps_series_count(self):
        by_bar = {i: [((1.0, j), float(i + j)) for j in range(li.MAX_PANES + 4)]
                  for i in range(3)}
        assert len(li.build(self._hit("rsi", by_bar), 3)["panes"]) <= li.MAX_PANES

    def test_overlay_with_wrong_scale_moves_to_pane(self):
        # 成交量的均线（3e7）画进主图会把价格压成直线 → 降级到副图
        vol = {i: [((100.0 + i, 22), 3.0e7 + i)] for i in range(3)}
        out = li.build(self._hit("sma", vol), 3, price_ref=100.0)
        assert out["overlays"] == []
        assert [s["label"] for s in out["panes"]] == ["MA(22)"]

    def test_overlay_with_price_scale_stays(self):
        px = {i: [((100.0 + i, 20), 99.0 + i)] for i in range(3)}
        out = li.build(self._hit("ema", px), 3, price_ref=100.0)
        assert [s["label"] for s in out["overlays"]] == ["EMA(20)"]
        assert out["panes"] == []

    def test_child_follows_parent_pane(self):
        """`ta.lowest(rsi, 14)` 是 RSI 尺度的线：量纲护栏拦不住（50 落在价格 ±4 倍内），
        必须靠「输入是谁」判定——跟随 RSI 进副图，并并入 RSI 那一格。

        认亲只能比数值：pynecore 传给 ta.* 的是当前 bar 的浮点数（不是 Series 对象），
        对象身份不可用。
        """
        n = li.LINEAGE_MIN_BARS + 2
        rsi = {i: [((100.0 + i, 14), 50.0 + i)] for i in range(n)}
        low = {i: [((50.0 + i, 14), 45.0 + i)] for i in range(n)}   # 第一个实参=RSI 值
        out = li.build({"rsi@10@0": {"name": "rsi", "line": 10, "by_bar": rsi},
                        "lowest@20@0": {"name": "lowest", "line": 20, "by_bar": low}},
                       n, price_ref=100.0)
        assert out["overlays"] == []
        assert [(s["label"], s["group"]) for s in out["panes"]] == [
            ("RSI(14)", "rsi@10@0#0"), ("区间低点(14)", "rsi@10@0#0")]

    def test_child_follows_parent_overlay(self):
        """价格上的区间高低点仍跟价格走主图（别把父子规则做成一刀切）。"""
        n = li.LINEAGE_MIN_BARS + 2
        ema = {i: [((100.0 + i, 20), 99.0 + i)] for i in range(n)}
        hi = {i: [((99.0 + i, 14), 101.0 + i)] for i in range(n)}
        out = li.build({"ema@10@0": {"name": "ema", "line": 10, "by_bar": ema},
                        "highest@20@0": {"name": "highest", "line": 20, "by_bar": hi}},
                       n, price_ref=100.0)
        assert [s["label"] for s in out["overlays"]] == ["EMA(20)", "区间高点(14)"]
        assert out["panes"] == []

    def test_parent_chain_resolves_recursively(self):
        """链式跟随：lowest(ema(rsi,9),5) 的父是 ema(rsi,9)，父的父才是 RSI。"""
        n = li.LINEAGE_MIN_BARS + 2
        rsi = {i: [((100.0 + i, 14), 50.0 + i)] for i in range(n)}
        ema = {i: [((50.0 + i, 9), 51.0 + i)] for i in range(n)}    # 输入 = RSI
        low = {i: [((51.0 + i, 5), 49.0 + i)] for i in range(n)}    # 输入 = EMA
        out = li.build({"rsi@10@0": {"name": "rsi", "line": 10, "by_bar": rsi},
                        "ema@20@0": {"name": "ema", "line": 20, "by_bar": ema},
                        "lowest@30@0": {"name": "lowest", "line": 30, "by_bar": low}},
                       n, price_ref=100.0)
        assert out["overlays"] == []
        assert [(s["label"], s["group"]) for s in out["panes"]] == [
            ("RSI(14)", "rsi@10@0#0"), ("EMA(9)", "rsi@10@0#0"),
            ("区间低点(5)", "rsi@10@0#0")]

    def test_source_after_injected_state_object(self):
        """`ta.sma(stoch_rsi_k, 3)`：源值被状态对象挡住，认不出上游 →
        StochRSI 的 0-100 线被画到价格主图上（0975 实拍）。跳过状态对象即可。"""
        n = li.LINEAGE_MIN_BARS + 2
        stoch = {i: [((["state"], 100.0 + i, 14.0), 60.0 + i)] for i in range(n)}
        sma = {i: [((["state"], 60.0 + i, 3.0), 61.0 + i)] for i in range(n)}
        out = li.build({"stoch@10@0": {"name": "stoch", "line": 10, "by_bar": stoch},
                        "sma@20@0": {"name": "sma", "line": 20, "by_bar": sma}},
                       n, price_ref=100.0)
        assert out["overlays"] == []
        assert [(s["label"], s["group"]) for s in out["panes"]] == [
            ("KDJ/Stoch(14)", "stoch@10@0#0"), ("MA(3)", "stoch@10@0#0")]

    def test_na_input_does_not_fake_a_lineage(self):
        """na_float 是单例：na 参与比对会把不相干的调用点串成父子（实测踩过）。"""
        n = li.LINEAGE_MIN_BARS + 2
        rsi = {i: [((100.0 + i, 14), 50.0 + i)] for i in range(n)}
        low = {i: [((float("nan"), 14), 45.0 + i)] for i in range(n)}
        out = li.build({"rsi@10@0": {"name": "rsi", "line": 10, "by_bar": rsi},
                        "lowest@20@0": {"name": "lowest", "line": 20, "by_bar": low}},
                       n, price_ref=100.0)
        assert [s["label"] for s in out["overlays"]] == ["区间低点(14)"]
        assert [s["label"] for s in out["panes"]] == ["RSI(14)"]

    def test_same_line_different_scale_splits_panes(self):
        """一行三个 ta.sma 可以是三条完全不同量纲的线（0541 实录：中位数
        0.0036 / 0.031 / 2.14）。挤在一格的话小的两条会被压成贴着 0 的直线。"""
        by_bar = {i: [((100.0 + i, 21), 0.0032 + i * 1e-4),
                      ((100.0 + i, 21), 0.032 + i * 1e-3),
                      ((100.0 + i, 21), 2.2 + i * 0.1)] for i in range(3)}
        out = li.build(self._hit("sma", by_bar), 3, price_ref=100.0)["panes"]
        assert [s["label"] for s in out] == ["MA1(21)", "MA2(21)", "MA3(21)"]
        assert len({s["group"] for s in out}) == 3

    def test_different_call_sites_stay_apart(self):
        """RSI(21) 与 Stoch(9) 虽同为 0-100，但各是一个独立指标 → 各占一格副图。
        跨调用点合并只是把不相干的线塞进同一个纵轴，看着是一格其实各说各话。"""
        rsi = {i: [((100.0 + i, 21), 50.0 + i)] for i in range(3)}
        stoch = {i: [((100.0 + i, 9), 55.0 + i)] for i in range(3)}
        out = li.build({"rsi@10@0": {"name": "rsi", "line": 10, "by_bar": rsi},
                        "stoch@20@0": {"name": "stoch", "line": 20, "by_bar": stoch}},
                       3, price_ref=100.0)["panes"]
        assert [s["label"] for s in out] == ["RSI(21)", "KDJ/Stoch(9)"]
        assert len({s["group"] for s in out}) == 2

    def test_same_root_different_scale_splits(self):
        """同一条推导链的线量级相差百倍时也要拆（否则小的那条看不见）。"""
        panes = [{"root": "a#0", "values": [1.0, 2.0]},
                 {"root": "a#0", "values": [500.0, 900.0]},
                 {"root": "a#0", "values": [1.5, 2.5]}]
        out = li._group_panes(panes)
        assert out[0]["group"] == out[2]["group"]      # 1 和 1.5 同格
        assert out[1]["group"] != out[0]["group"]      # 900 单独一格


# ---------- 真回测 ----------

PINE = '''
"""
@pyne

测试用：MACD 金叉开仓 + 一条 EMA（只为验证指标捕获）
"""
from pynecore.lib import close, input, script, strategy, ta
from pynecore.types import Series


@script.strategy("指标捕获测试", overlay=True, pyramiding=0,
                 default_qty_type=strategy.percent_of_equity, default_qty_value=95,
                 initial_capital=100000)
def main(fast=input(12, "快线"), slow=input(26, "慢线"), sig=input(9, "信号")):
    m, s, h = ta.macd(close, fast, slow, sig)
    e: Series[float] = ta.ema(close, 20)
    r: Series[float] = ta.rsi(close, 14)
    r_hi: Series[float] = ta.highest(r, 14)      # RSI 尺度 → 必须跟 RSI 进副图
    c_hi: Series[float] = ta.highest(close, 14)  # 价格尺度 → 留在主图
    r_sm: Series[float] = ta.sma(r, 5)           # 源值前有状态对象 → 也要跟 RSI
    up: Series[bool] = m > s
    if up and not up[1]:
        strategy.entry('L', strategy.long)
    elif not up and up[1]:
        strategy.close('L')
'''


def _ema(values: list[float], n: int) -> list[float | None]:
    """独立实现（Pine 口径：前 n 根用 SMA 起头，之后 alpha = 2/(n+1)）。"""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    alpha = 2 / (n + 1)
    for i in range(n, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


@pytest.fixture(scope="module")
def backtest(tmp_path_factory):
    pytest.importorskip("pynecore", reason="pynecore 只在 pine_lab/.venv 里")
    from backend.services import market_lab as ml

    try:
        bars = ml.load_klines("600309.SH", adj="backward", limit=0)
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"没有行情数据: {exc}")
    d = tmp_path_factory.mktemp("ind")
    script = d / "probe_strategy.py"
    script.write_text(PINE, encoding="utf-8")
    plain = ml.run_script_file(script, "600309.SH", adj="backward")
    got = ml.run_script_file(script, "600309.SH", adj="backward", want_indicators=True)
    return bars, plain, got


class TestRealBacktest:
    def test_capture_does_not_change_results(self, backtest):
        _, plain, got = backtest
        assert got["stats"] == plain["stats"]
        assert got["trades"] == plain["trades"]

    def test_labels_carry_real_periods(self, backtest):
        _, _, got = backtest
        ind = got["indicators"]
        labels = [s["label"] for s in ind["overlays"] + ind["panes"]]
        assert "EMA(20)" in labels
        assert "MACD DIF(12,26,9)" in labels
        assert "MACD 柱(12,26,9)" in labels

    def test_ema_matches_independent_calculation(self, backtest):
        bars, _, got = backtest
        ema = next(s for s in got["indicators"]["overlays"] if s["label"].startswith("EMA"))
        want = _ema([b["close"] for b in bars], 20)
        assert ema["values"][-1] == pytest.approx(want[-1], abs=1e-3)
        assert ema["values"][19] == pytest.approx(want[19], abs=1e-6)

    def test_values_aligned_to_bars(self, backtest):
        bars, _, got = backtest
        ind = got["indicators"]
        assert ind["bars"] == len(bars)
        assert all(len(s["values"]) == len(bars)
                   for s in ind["overlays"] + ind["panes"])

    def test_indicator_of_indicator_follows_source(self, backtest):
        """实测 0975 的坑：`ta.highest(rsi, 14)` 中位数 61.7、价格 ~95，
        量纲护栏（0.25~4 倍）拦不住 → 被画到价格主图。跟随输入序列才能判对。"""
        _, _, got = backtest
        ind = got["indicators"]
        rsi = next(s for s in ind["panes"] if s["label"].startswith("RSI"))
        child = next(s for s in ind["panes"] if s["label"] == "区间高点(14)")
        assert child["group"] == rsi["group"]          # 与 RSI 同一格副图
        # 价格上的那一条仍留在主图：同名两条线各归各位
        assert sum(s["label"] == "区间高点(14)" for s in ind["overlays"]) == 1
        # sma 的源值前有 pynecore 塞的状态对象，同样要跟随（0975 的 StochRSI 情形）
        ma5 = next(s for s in ind["panes"] if s["label"] == "MA(5)")
        assert ma5["group"] == rsi["group"]
        assert not any(s["label"] == "MA(5)" for s in ind["overlays"])
