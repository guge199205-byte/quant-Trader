"""live_quotes（统一行情入口）单测：混合策略矩阵，全程 mock，无网络。

覆盖：AiData 优先成功、AiData 失败落桥、桥优先失败落 AiData、
双源皆失败返回空、AiData 不可用、日期归一（20260914→2026-09-14）、
数值字符串转 float、close<=0 行丢弃、批量路径缺码补桥、quote 字段探测。
"""
from __future__ import annotations

import pytest

import live_quotes as lq


@pytest.fixture
def aidata(monkeypatch):
    """可编程的 AiData 假实现（并关掉 conftest 的全局禁用开关）。"""
    monkeypatch.delenv("LIVE_QUOTES_DISABLE_AIDATA", raising=False)
    state = {"klines": {}, "batch": {}, "quote": {}, "up": True, "calls": []}

    class _FakeTa:
        @staticmethod
        def available():
            return state["up"]

        @staticmethod
        def get_klines(code, interval="daily", count=5):
            state["calls"].append(("klines", code))
            rows = state["klines"].get(code)
            if rows is None:
                raise RuntimeError("AiData 挂了")
            return rows

        @staticmethod
        def get_klines_batch(codes, interval="daily", count=5):
            state["calls"].append(("batch", tuple(codes)))
            return {c: state["batch"][c] for c in codes if c in state["batch"]}

        @staticmethod
        def get_quote(code):
            rows = state["quote"].get(code)
            if rows is None:
                raise RuntimeError("快照不可用")
            return rows

    import sys
    import types

    import agent_tools.datasources as _ds

    mod = types.SimpleNamespace(**{k: getattr(_FakeTa, k) for k in
                                   ("available", "get_klines", "get_klines_batch", "get_quote")})
    monkeypatch.setitem(sys.modules, "agent_tools.datasources.tdx_aidata", mod)
    monkeypatch.setattr(_ds, "tdx_aidata", mod, raising=False)  # from-import 取包属性
    return state


class _FakeBroker:
    def __init__(self, *, klines=None, quote=None, error=None):
        self._klines = klines or {}
        self._quote = quote or {}
        self._error = error

    def get_klines(self, code, interval="daily", **kw):
        if self._error:
            raise self._error
        if code not in self._klines:
            raise RuntimeError("桥无此码")
        return self._klines[code]

    def get_quote(self, code, date=""):
        if self._error:
            raise self._error
        return self._quote.get(code) or {}


def test_aidata_preferred_success_aidata(aidata):
    aidata["klines"]["600519.SH"] = [
        {"date": "20260914", "open": 1, "high": 2, "low": 0.5, "close": 1.5,
         "volume": "100", "amount": "10"}]
    bars = lq.klines("600519.SH", prefer="aidata")
    assert bars[0]["date"] == "2026-09-14"          # 日期归一
    assert bars[0]["close"] == 1.5
    assert bars[0]["volume"] == 100.0               # 字符串转 float


def test_aidata_failure_falls_back_to_bridge(aidata):
    # AiData 没有该码（抛错）→ 落桥
    broker = _FakeBroker(klines={"600519.SH": [
        {"date": "20260914", "close": "1.5", "open": 1, "high": 2, "low": 0.5,
         "volume": 1, "amount": 1}]})
    bars = lq.klines("600519.SH", prefer="aidata", broker=broker)
    assert bars and bars[-1]["close"] == 1.5


def test_bridge_preferred_failure_falls_back_to_aidata(aidata):
    # 哨兵路径：桥优先——桥断自动落 AiData（"桥断了也能获取"）
    aidata["klines"]["600817.SH"] = [
        {"date": "20260915", "close": 9.33, "open": 9.3, "high": 9.4, "low": 9.2,
         "volume": 1, "amount": 1}]
    broker = _FakeBroker(error=ConnectionError("桥断线"))
    bars = lq.klines("600817.SH", prefer="bridge", broker=broker)
    assert bars and bars[-1]["close"] == 9.33


def test_both_sources_fail_returns_empty(aidata):
    aidata["up"] = False
    broker = _FakeBroker(error=ConnectionError("桥断线"))
    assert lq.klines("000000.SZ", prefer="aidata", broker=broker) == []
    assert lq.klines("000000.SZ", prefer="bridge", broker=broker) == []


def test_close_nonpositive_rows_dropped(aidata):
    aidata["klines"]["600519.SH"] = [
        {"date": "20260914", "close": 0},                 # 未生成 bar
        {"date": "20260915", "close": 1.5, "open": 1, "high": 2, "low": 1,
         "volume": 1, "amount": 1}]
    bars = lq.klines("600519.SH")
    assert len(bars) == 1 and bars[0]["date"] == "2026-09-15"


def test_batch_aidata_then_bridge_backfill(aidata):
    aidata["batch"] = {"600519.SH": [
        {"date": "20260915", "close": 1272.75, "open": 1, "high": 1, "low": 1,
         "volume": 1, "amount": 1}]}
    broker = _FakeBroker(klines={"600817.SH": [
        {"date": "20260915", "close": 9.33, "open": 1, "high": 1, "low": 1,
         "volume": 1, "amount": 1}]})
    out = lq.klines_batch(["600519.SH", "600817.SH"], broker=broker)
    assert set(out) == {"600519.SH", "600817.SH"}     # 批量命中 + 桥补齐
    assert out["600817.SH"][-1]["close"] == 9.33


def test_quote_price_key_probe(aidata):
    # AiData 快照字段名随版本不同：按候选键探测
    aidata["quote"]["600519.SH"] = {"LastPrice": "1272.75", "Other": 1}
    q = lq.quote("600519.SH")
    assert q["close"] == 1272.75 and q["src"] == "aidata"


def test_quote_bridge_fallback(aidata):
    aidata["up"] = False
    broker = _FakeBroker(quote={"600519.SH": {"close": 1260.0}})
    q = lq.quote("600519.SH", broker=broker)
    assert q["close"] == 1260.0 and q["src"] == "bridge"


def test_quote_empty_when_all_fail(aidata):
    aidata["up"] = False
    assert lq.quote("600519.SH", broker=_FakeBroker(error=RuntimeError("x"))) == {}


def test_norm_date_forms():
    assert lq.norm_date("20260914") == "2026-09-14"
    assert lq.norm_date("2026-09-14") == "2026-09-14"
    assert lq.norm_date("") == ""


def test_last_close_uses_klines(aidata):
    aidata["klines"]["600519.SH"] = [
        {"date": "20260914", "close": 1.0, "open": 1, "high": 1, "low": 1,
         "volume": 1, "amount": 1},
        {"date": "20260915", "close": 2.0, "open": 1, "high": 1, "low": 1,
         "volume": 1, "amount": 1}]
    assert lq.last_close("600519.SH") == 2.0
    assert lq.last_close("000000.SZ") == 0.0