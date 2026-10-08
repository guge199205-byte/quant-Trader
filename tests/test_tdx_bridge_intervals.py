"""桥的周期契约（P1）：不支持的周期必须**抛错**，不能静默返回空。

实测形状：桥对 `1m/5m/15m/30m` 返回 `ErrorId=0` + `Value` 空数组 —— 成功码配空
数据。于是「周期不支持」和「这只票停牌」在下游长得一模一样：`get_klines` 返回
`[]`，调用方以为「今天没数据」。`get_klines(...)` 的 docstring 写了「分钟线不
支持」，但代码对未知周期**原样透传**，把桥的静默空变成了调用方的静默空。

契约（按周期分级）：
  * `daily`/`weekly` → 走白名单；**空结果合法**（停牌/退市/新股，返回 []）；
  * 已知分钟周期 → 抛 `BrokerError`，且**一个字都不发**（不发请求就没限流代价）；
  * 未知周期字符串 → 同样抛错，不再透传给桥。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))

from agent_tools.brokers.tdx_bridge import BrokerError, TdxBridgeBroker  # noqa: E402


@pytest.fixture
def broker(monkeypatch):
    """显式传 config：conftest 收集期会把真实 .env 灌进 os.environ（真桥地址+令牌）。"""
    b = TdxBridgeBroker({"bridge_url": "http://127.0.0.1:1", "token": "test-token"})
    # 断线自动发现会真扫 /24 局域网（tests/test_bridge_discovery.py 同款封堵）
    monkeypatch.setattr(b, "_discover_or_none", lambda: None, raising=False)
    return b


@pytest.fixture
def sent(monkeypatch):
    """拦截出站请求并记账 —— 用来断言「压根没发」。"""
    import requests

    calls: list = []

    def _post(url, **kw):
        calls.append((url, kw))
        raise AssertionError("不该发起请求")

    monkeypatch.setattr(requests, "post", _post)
    return calls


MINUTE_INTERVALS = ["1m", "5m", "10m", "15m", "30m", "60m", "1h", "min1", "1min"]


class TestUnsupportedIntervals:
    @pytest.mark.parametrize("interval", MINUTE_INTERVALS)
    def test_minute_intervals_raise_without_calling_out(self, broker, sent, interval):
        with pytest.raises(BrokerError, match="分钟"):
            broker.get_klines("600519.SH", interval=interval)
        assert sent == [], f"{interval} 仍发起了桥请求"

    def test_unknown_interval_string_also_raises(self, broker, sent):
        """未知周期以前是原样透传给桥（`{...}.get(interval, interval)`）→ 静默空。"""
        with pytest.raises(BrokerError):
            broker.get_klines("600519.SH", interval="2h")
        assert sent == []

    def test_error_names_a_working_alternative(self, broker, sent):
        """报错必须可行动：说清桥不给什么、该走哪条路。"""
        with pytest.raises(BrokerError) as ei:
            broker.get_klines("600519.SH", interval="5m", count=12)
        msg = str(ei.value)
        assert "5m" in msg and "1d" in msg, f"提示没给出可用周期: {msg}"

    def test_get_quote_never_routes_through_a_minute_period(self, broker):
        """get_quote 是 daily 的薄封装 —— 别让它被周期改动误伤。"""
        assert broker.get_klines.__defaults__[2] == "daily"


class TestSupportedIntervals:
    def _reply(self, monkeypatch, value_for=None):
        import requests

        payload = {"success": True, "result": {
            "ErrorId": 0, "Value": {"600519.SH": value_for or {
                "Close": [1.0, 2.0], "Date": ["20260921", "20260922"],
                "Open": [1.0, 1.5], "High": [1.2, 2.2], "Low": [0.9, 1.4],
                "Volume": [10, 20], "Amount": [100, 200]}}}}

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        def _post(url, **kw):
            _post.last = (url, kw)
            return _R()

        _post.last = None
        monkeypatch.setattr(requests, "post", _post)
        return _post

    def test_daily_payload_shape_unchanged(self, broker, monkeypatch):
        post = self._reply(monkeypatch)
        bars = broker.get_klines("600519.SH", interval="daily", count=2)
        assert bars == [
            {"date": "20260921", "open": 1.0, "high": 1.2, "low": 0.9,
             "close": 1.0, "volume": 10, "amount": 100},
            {"date": "20260922", "open": 1.5, "high": 2.2, "low": 1.4,
             "close": 2.0, "volume": 20, "amount": 200},
        ]
        params = post.last[1]["json"]["params"]
        assert params["period"] == "1d" and params["stock_list"] == ["600519.SH"]

    def test_weekly_maps_to_1w(self, broker, monkeypatch):
        post = self._reply(monkeypatch)
        broker.get_klines("600519.SH", interval="weekly", count=2)
        assert post.last[1]["json"]["params"]["period"] == "1w"

    def test_empty_daily_is_legal_not_an_error(self, broker, monkeypatch):
        """日K 空 = 停牌/退市/新股 —— 合法返回 []，绝不能抛。"""
        self._reply(monkeypatch, value_for={"Close": [], "Date": []})
        assert broker.get_klines("600519.SH", interval="daily") == []

    def test_default_interval_is_daily(self, broker, monkeypatch):
        post = self._reply(monkeypatch)
        broker.get_klines("600519.SH")
        assert post.last[1]["json"]["params"]["period"] == "1d"