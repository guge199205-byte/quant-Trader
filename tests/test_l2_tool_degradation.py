"""`get_l2_market_data` 的降级可见性（LOW-5）。

逐笔（tick）那一步失败时，旧形状是：`ok: True` + `ticks: []` +
`agg: {n: 0, buy_vol: 0, sell_vol: 0, net_buy_vol: 0, buy_pct: None}`——
**与「今天真的一笔主动成交都没有」逐字相同**，而抛出的异常文本被就地丢弃。
对交易决策来说这两件事的含义完全相反：一个是「盘口有、分笔缺」，
一个是「确实没人买」。

契约（本文件钉住）：
  * `tick_error` 键**只在逐笔取不到时出现**（异常文本 / 「无数据」说明）；
    它缺席 = 逐笔载荷真的到了（哪怕里面是空的）。
  * 逐笔取不到 → `agg` 为 **null**，不是一串 0。
  * `snapshot` 是主载荷，逐笔失败不影响它；`ok: True` 指的是快照取到了。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_tools import tool_get_price_local as tool  # noqa: E402
from agent_tools.datasources import tdx_aidata as ta  # noqa: E402

QUOTE = {
    "Now": "1255.22", "Open": "1250", "Max": "1260", "Min": "1240",
    "LastClose": "1248", "Average": "1252", "Before5MinNow": "1251",
    "Inside": "100", "Outside": "200",
    "Buyp": ["1255.2", "1255.1"], "Buyv": ["10", "20"],
    "Sellp": ["1255.3", "1255.4"], "Sellv": ["30", "40"],
}

TICKS_OK = {
    "Price": ["1255.2", "1255.3", "1255.1"],
    "Volume": ["10", "20", "5"],
    "BSFlag": ["1", "2", "0"],
    "Time": ["09:31:00", "09:31:05", "09:31:10"],
}


def l2(**kw):
    """调用被测函数。

    模块属性被 `@mcp.tool()` 包成 FunctionTool（fastmcp），测试要的是底层函数——
    经由 `.fn` 取回；顺带钉住「注册的是同一个函数对象」这个前提。
    """
    fn = getattr(tool.get_l2_market_data, "fn", tool.get_l2_market_data)
    return fn("600519.SH", **kw)


@pytest.fixture
def chan(monkeypatch):
    """闸门开、通道活、快照可用 —— 只把逐笔那一步换成被测形状。"""
    monkeypatch.setattr(tool, "_l2_cache", {})
    monkeypatch.setattr(ta, "gate_reason", lambda: "")
    monkeypatch.setattr(ta, "breaker_open", lambda: None)
    monkeypatch.setattr(ta, "available", lambda: True)
    monkeypatch.setattr(ta, "get_quote", lambda symbol: dict(QUOTE))
    return ta


def test_tick_exception_keeps_the_snapshot_but_marks_the_gap(chan, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("逐笔接口挂死")

    monkeypatch.setattr(chan, "get_tick_data", boom)

    r = l2()

    assert r["ok"] is True and r["cached"] is False
    assert r["snapshot"]["now"] == 1255.22, "快照是主载荷，逐笔失败不该带走它"
    assert r["ticks"] == []
    assert r["agg"] is None, "全 0 的 agg 会被读成「今天真没有主动成交」"
    assert "逐笔接口挂死" in r["tick_error"], "异常文本不能丢"


def test_empty_tick_payload_is_named_not_zeroed(chan, monkeypatch):
    """空结果（None）是正常业务形状（未开盘/停牌/限流）——但同样不是「零成交」。"""
    monkeypatch.setattr(chan, "get_tick_data", lambda *a, **k: None)

    r = l2()

    assert r["ok"] is True and r["agg"] is None
    assert "逐笔" in r["tick_error"], r


def test_payload_without_price_array_is_also_a_gap(chan, monkeypatch):
    monkeypatch.setattr(chan, "get_tick_data", lambda *a, **k: {"Time": []})

    r = l2()

    assert r["agg"] is None and "tick_error" in r


def test_healthy_ticks_keep_the_original_shape(chan, monkeypatch):
    """回归钉：正常路径的字段与数值口径一字不变（没有 tick_error 键）。"""
    monkeypatch.setattr(chan, "get_tick_data", lambda *a, **k: dict(TICKS_OK))

    r = l2()

    assert "tick_error" not in r, "逐笔到了就不该有缺口语义"
    assert r["agg"] == {"n": 3, "buy_vol": 10, "sell_vol": 20,
                        "net_buy_vol": -10, "buy_pct": 33.3}
    assert [t["side"] for t in r["ticks"]] == ["buy", "sell", "neutral"]


def test_a_payload_with_zero_ticks_is_not_reported_as_a_gap(chan, monkeypatch):
    """真·零成交（载荷到了、里面是空数组）与「取不到」必须分得开。"""
    monkeypatch.setattr(chan, "get_tick_data", lambda *a, **k: {"Price": []})

    r = l2()

    assert "tick_error" not in r
    assert r["agg"] == {"n": 0, "buy_vol": 0, "sell_vol": 0,
                        "net_buy_vol": 0, "buy_pct": None}


def test_cached_degraded_result_stays_marked(chan, monkeypatch):
    """缓存命中也要带缺口标记——否则「第二次调用」看起来一切正常。"""
    monkeypatch.setattr(chan, "get_tick_data",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("挂了")))

    first = l2()
    second = l2()

    assert second["cached"] is True
    assert second["agg"] is None and "tick_error" in second
    assert first["tick_error"] == second["tick_error"]


# ---------- 三条前置能力分支（二轮审查 LOW-1） ----------
#
# 这三段早退是本批次**新增**的（闸门/熔断/加载失败给出可行动说明），但 chan
# fixture 把它们全 patch 成健康态 → 一条都没跑到。它们是「降级要可见」主题的
# 门面：模型读到的就是这三句话，回归了但没有用例 = 静默。

def test_gate_closed_names_the_switch_not_a_bare_error(chan, monkeypatch):
    monkeypatch.setattr(chan, "gate_reason", lambda: "TdxAiData 已禁用（TDX_AIDATA_DISABLE=1）")

    r = l2()

    assert r["ok"] is False
    assert "已禁用" in r["error"] and "TDX_AIDATA_DISABLE" in r["error"], r


def test_open_breaker_points_at_the_bridge_alternative(chan, monkeypatch):
    monkeypatch.setattr(chan, "breaker_open", lambda: {
        "fail_streak": 7, "first_fail_ts": "2026-09-22T09:00:00+08:00",
        "error": "错误码 11 连接失败"})

    r = l2()

    assert r["ok"] is False
    assert "熔断" in r["error"] and "7" in r["error"]
    assert "get_market_snapshot" in r["error"], "要给出替代取数路径，否则模型只能瞎猜"


def test_unloaded_channel_is_reported_as_such(chan, monkeypatch):
    monkeypatch.setattr(chan, "available", lambda: False)

    r = l2()

    assert r["ok"] is False and "不可用" in r["error"]
    assert "snapshot" not in r and "ticks" not in r, "没取到任何数据时不许给出半截载荷"
