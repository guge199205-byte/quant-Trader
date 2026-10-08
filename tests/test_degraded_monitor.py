"""桥断降级监控单测（2026-09-15）：快照缓存 / 去重 / 三处降级消费点。

全程 mock 数据源，无网络；验证「桥断时只告警、绝不执行任何下单调用」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import live_account_cache as lac
import leverage_guard as lg
import live_hourly_analysis as lha
import live_price_watch as lpw


# ---------- 快照缓存 ----------


@pytest.fixture
def cache_file(monkeypatch, tmp_path):
    f = tmp_path / "broker_account_cache.json"
    monkeypatch.setattr(lac, "CACHE_FILE", f)
    monkeypatch.setattr(lac, "_NOTIFY_STATE", tmp_path / "degraded_notify_state.json")
    monkeypatch.setattr(lac, "ROOT", tmp_path)
    return f


def test_snapshot_roundtrip_and_age(cache_file):
    lac.save_snapshot({"asset": {"cash": 100.0}, "positions": [
        {"stock_code": "600000.SH", "total_volume": 100}]})
    snap = lac.load_snapshot()
    assert snap["asset"]["cash"] == 100.0
    assert snap["age_min"] < 1


def test_snapshot_expired_returns_none(cache_file):
    lac.save_snapshot({"asset": {}, "positions": []})
    d = json.loads(cache_file.read_text())
    d["ts"] = time.time() - 300 * 60     # 5 小时前
    cache_file.write_text(json.dumps(d))
    assert lac.load_snapshot(max_age_min=240) is None


def test_snapshot_positions_parse(cache_file):
    lac.save_snapshot({"asset": {}, "positions": [
        {"stock_code": "600817.SH", "total_volume": 1000, "available_volume": 600}]})
    p = lac.snapshot_positions(lac.load_snapshot())
    assert p["600817.SH"] == {"volume": 1000, "available": 600}


def test_ledger_positions_t1_approx(cache_file):
    logs = cache_file.parent / "logs"
    logs.mkdir(exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    (logs / "live_ledger.json").write_text(json.dumps({"agents": {
        "A": {"positions": {
            "600817.SH": {"volume": 500, "buy_ts": f"{today}T10:00:00+08:00"},  # 今日买入
            "600000.SH": {"volume": 300, "buy_ts": "2026-09-10T10:00:00+08:00"},
        }}}}))
    p = lac.ledger_positions()
    assert p["600817.SH"]["available"] == 0      # T+1 近似
    assert p["600000.SH"]["available"] == 300


def test_notify_throttled_dedup(cache_file, monkeypatch):
    sent = []
    monkeypatch.setattr("push_notify.notify", lambda t, c: sent.append(t))
    lac.notify_throttled("k1", "标题", "内容", gap_min=30)
    lac.notify_throttled("k1", "标题", "内容", gap_min=30)
    assert sent == ["标题"]                       # 30 分钟内只发一次
    lac.notify_throttled("k2", "标题2", "内容")
    assert sent == ["标题", "标题2"]              # 不同 key 不受影响


# ---------- 杠杆守护降级 ----------


def _fake_snap(tmp_path, cash, code_vol):
    monkeypatch_snap = {"ts": time.time(), "asset": {"cash": cash},
                        "positions": [{"stock_code": c, "total_volume": v,
                                       "available_volume": v}
                                      for c, v in code_vol.items()]}
    return monkeypatch_snap


def test_guard_degraded_alerts_on_excess_leverage(cache_file, monkeypatch):
    # Arrange：快照现金 -20 万（融资）+ 1 万股 × AiData 价 30 → 杠杆 3.0 超限
    monkeypatch.setattr(lac, "load_snapshot",
                        lambda max_age_min=240: _fake_snap(cache_file, -200000.0,
                                                           {"600817.SH": 10000}))
    monkeypatch.setattr(lac, "snapshot_positions",
                        lambda s: {"600817.SH": {"volume": 10000, "available": 10000}})
    monkeypatch.setattr(lg, "lq_quote", lambda code, **k: {"close": 30.0})
    monkeypatch.setattr(lg, "load_ledger", lambda: {"agents": {}})
    alerts = []
    monkeypatch.setattr(lac, "notify_throttled",
                        lambda key, t, c, gap_min=30: alerts.append((key, t, c)))

    # Act
    rc = lg._degraded_check(lg.now_cn())

    # Assert：告警发出、且全程没有任何下单动作
    assert rc == 0
    assert len(alerts) == 1 and alerts[0][0] == "leverage"
    assert "3.00×" in alerts[0][2] or "全账户杠杆" in alerts[0][2]


def test_guard_degraded_no_alert_when_normal(cache_file, monkeypatch):
    monkeypatch.setattr(lac, "load_snapshot",
                        lambda max_age_min=240: _fake_snap(cache_file, 500000.0,
                                                           {"600817.SH": 10000}))
    monkeypatch.setattr(lac, "snapshot_positions",
                        lambda s: {"600817.SH": {"volume": 10000, "available": 10000}})
    monkeypatch.setattr(lg, "lq_quote", lambda code, **k: {"close": 9.0})
    monkeypatch.setattr(lg, "load_ledger", lambda: {"agents": {}})
    alerts = []
    monkeypatch.setattr(lac, "notify_throttled",
                        lambda key, t, c, gap_min=30: alerts.append(key))
    assert lg._degraded_check(lg.now_cn()) == 0
    assert alerts == []


def test_guard_degraded_skips_without_data(cache_file, monkeypatch):
    monkeypatch.setattr(lac, "load_snapshot", lambda max_age_min=240: None)
    monkeypatch.setattr(lac, "ledger_positions", lambda agent=None: {})
    monkeypatch.setattr(lg, "load_ledger", lambda: {"agents": {}})
    assert lg._degraded_check(lg.now_cn()) == 0     # 无数据 → 静默跳过


# ---------- 净值采样降级估算 ----------


def test_equity_estimate_from_snapshot(cache_file, monkeypatch):
    monkeypatch.setattr(lac, "load_snapshot",
                        lambda max_age_min=240: _fake_snap(cache_file, 100000.0,
                                                           {"600817.SH": 1000}))
    monkeypatch.setattr(lac, "snapshot_positions",
                        lambda s: {"600817.SH": {"volume": 1000, "available": 0}})
    monkeypatch.setattr(lha, "lq_quote",
                        lambda code, **k: {"close": 9.5})
    asset, cash = lha._degraded_asset_estimate()
    assert asset == pytest.approx(100000.0 + 1000 * 9.5)
    assert cash == 100000.0


def test_equity_estimate_none_without_snapshot(cache_file, monkeypatch):
    monkeypatch.setattr(lac, "load_snapshot", lambda max_age_min=240: None)
    assert lha._degraded_asset_estimate() == (0.0, 0.0)


def test_equity_estimate_skips_when_no_prices(cache_file, monkeypatch):
    monkeypatch.setattr(lac, "load_snapshot",
                        lambda max_age_min=240: _fake_snap(cache_file, 100000.0,
                                                           {"600817.SH": 1000}))
    monkeypatch.setattr(lac, "snapshot_positions",
                        lambda s: {"600817.SH": {"volume": 1000, "available": 0}})
    monkeypatch.setattr(lha, "lq_quote", lambda code, **k: {})   # 行情全取不到
    assert lha._degraded_asset_estimate() == (0.0, 0.0)          # 宁缺毋滥


# ---------- 哨兵降级巡检 ----------


def test_watch_degraded_alerts_on_trigger(monkeypatch, tmp_path):
    monkeypatch.setattr(lpw, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lac, "ledger_positions",
                        lambda agent=None: {"600817.SH": {"volume": 500}})
    # 日K最后一根必须是**今天**（2026-09-21 审查 MEDIUM-4 加的闸门：源回退到不含
    # 今日 bar 的日K时，昨收价会凭空触发告警）
    monkeypatch.setattr(lpw, "lq_klines",
                        lambda code, **k: [{"date": lpw.now_cn().strftime("%F"),
                                            "close": 9.0,
                                            "open": 0, "high": 0, "low": 0,
                                            "volume": 0, "amount": 0}])
    alerts = []
    monkeypatch.setattr(lac, "notify_throttled",
                        lambda key, t, c, gap_min=30: alerts.append((key, c)))
    rules = {"A": [{"code": "600817.SH", "stop_loss": 10.0, "reason": "贴成本防守"}]}

    lpw._degraded_trigger_check(rules)

    assert len(alerts) == 1
    assert "跌破止损 ¥10.00" in alerts[0][1]
    assert "未执行" in alerts[0][1]


def test_watch_degraded_skips_stale_bar_date(monkeypatch, tmp_path):
    """MEDIUM-4 回归（2026-09-21 审查）：日K不是今天 → 不算触发、不告警。

    桥断降级时行情来自兜底源，可能只给到昨收 bar；拿它当现价会把「昨天收盘已经
    低于止损位」当成今天的触发（假告警，且引向错误的人工处置）。"""
    monkeypatch.setattr(lpw, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lac, "ledger_positions",
                        lambda agent=None: {"600817.SH": {"volume": 500}})
    monkeypatch.setattr(lpw, "lq_klines",
                        lambda code, **k: [{"date": "2026-09-15", "close": 9.0}])
    alerts = []
    monkeypatch.setattr(lac, "notify_throttled",
                        lambda key, t, c, gap_min=30: alerts.append((key, c)))
    rules = {"A": [{"code": "600817.SH", "stop_loss": 10.0, "reason": "贴成本防守"}]}

    lpw._degraded_trigger_check(rules)

    assert alerts == []


def test_watch_degraded_throttles_next_run(monkeypatch, tmp_path):
    monkeypatch.setattr(lpw, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lac, "ledger_positions",
                        lambda agent=None: {"600817.SH": {"volume": 500}})
    monkeypatch.setattr(lpw, "lq_klines",
                        lambda code, **k: [{"date": lpw.now_cn().strftime("%F"),
                                            "close": 9.0}])
    calls = []
    monkeypatch.setattr(lac, "notify_throttled",
                        lambda key, t, c, gap_min=30: calls.append(key))
    rules = {"A": [{"code": "600817.SH", "stop_loss": 10.0}]}

    lpw._degraded_trigger_check(rules)
    lpw._degraded_trigger_check(rules)      # 5 分钟内第二轮 → 落盘节流挡住

    assert len(calls) == 1


def test_watch_degraded_ignores_non_held(monkeypatch, tmp_path):
    monkeypatch.setattr(lpw, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lac, "ledger_positions", lambda agent=None: {})
    called = []
    monkeypatch.setattr(lpw, "lq_klines", lambda code, **k: called.append(code) or [])
    monkeypatch.setattr(lac, "notify_throttled", lambda *a, **k: None)
    lpw._degraded_trigger_check({"A": [{"code": "600817.SH", "stop_loss": 10.0}]})
    assert called == []                      # 不持有 → 连行情都不查