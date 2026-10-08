"""执行时段闸门 + 条件位「重复无法接管」重新布防（2026-09-18）。

两个 2026-09-18 实盘修复的回归测试：
  1. `in_continuous_auction` 边界（9:30-11:30 / 13:00-14:57）；
     整点轮被数据源退避拖到收盘后（14:45 轮 16:08 才执行）时，执行段必须整段
     跳过——600176 的盘后卖单被柜台判废（fill_abort rejected）；
  2. `_execute_sell` 的 dup 分支：桥判重复且回捞不到、账户无冻结（avail>=total）
     持续 ≥3 分钟 → 换 plan 号重新布防；此前只会「保留待人工」，规则卡死一整天
     （300408 实录：止损触发后全天无法成交，持仓无有效止损）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ashare_rules as R  # noqa: E402
import live_fills as F  # noqa: E402
import live_ledger as L  # noqa: E402

CN = F.CN_TZ
AGENT = "deepseek-v4-pro"
SELL_CODE = "001312.SZ"
NOW = datetime(2026, 9, 18, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()


# ---------- 1. 连续竞价窗口 ----------

@pytest.mark.parametrize("hm,expect", [
    ((9, 29), False), ((9, 30), True), ((11, 29), True), ((11, 30), False),
    ((12, 0), False), ((13, 0), True), ((14, 56), True), ((14, 57), False),
    ((15, 0), False), ((15, 20), False), ((8, 0), False),
])
def test_in_continuous_auction_boundaries(hm, expect):
    dt = datetime(2026, 9, 18, hm[0], hm[1], tzinfo=CN)   # 周五
    assert R.in_continuous_auction(dt) is expect


def test_in_continuous_auction_weekend_closed():
    sat = datetime(2026, 9, 19, 10, 30, tzinfo=CN)
    assert R.in_continuous_auction(sat) is False


# ---------- 2. 执行段闸门 ----------

class FakeBroker:
    def __init__(self):
        self.orders = []

    def get_klines(self, code, interval="daily", **kw):
        return [{"close": 16.0}, {"close": 17.5}]

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("sell", code, vol, price))
        return {"order_id": "T9001", "status": "submitted"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    import live_breaker
    import live_hourly_analysis as H
    import live_trade_picks as TP

    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(TP, "LOG_DIR", tmp_path / "logs")
    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {SELL_CODE: {"volume": 300, "cost_price": 16.0,
                                  "buy_ts": "2026-09-10T09:37:00+08:00", "last_ts": TS}}}}})

    monkeypatch.setattr(H, "now_cn", lambda: NOW)
    monkeypatch.setattr(live_breaker, "check_and_trip", lambda *a, **k: (False, ""))
    monkeypatch.setattr(H, "daily_buy_codes", lambda agent, day=None: set())
    monkeypatch.setattr("time.sleep", lambda s: None)
    lines = []
    monkeypatch.setattr(TP, "log_line", lambda doc: lines.append(doc))
    return SimpleNamespace(H=H, lines=lines)


def _holdings():
    return [{"code": SELL_CODE, "name": "福恩股份", "volume": 300, "cost": 16.0,
             "price": 17.5, "pnl_pct": 9.4, "day_chg": 1.6, "avail": 300}]


def test_exec_skipped_outside_window(env, monkeypatch):
    """窗口外（模拟 16:08 的场景）→ 不下任何单、落 exec_window_skip 日志。"""
    monkeypatch.setattr(env.H, "in_continuous_auction", lambda now: False)
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "t"}],
        _holdings(), 50000.0, dry_run=False)

    assert out == [] and broker.orders == []
    assert any(d.get("mode") == "exec_window_skip" for d in env.lines)
    # 账本原样：没有发生任何记账
    assert L.load_ledger()["agents"][AGENT]["positions"][SELL_CODE]["volume"] == 300


def test_exec_proceeds_inside_window(env, monkeypatch):
    """窗口内 → 照常执行（对照组；conftest 默认允许窗口）。"""
    monkeypatch.setattr(F, "wait_fill", lambda *a, **kw: {
        "order_id": "T9001", "status": "filled",
        "filled_volume": 300, "filled_price": 17.4})
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "t"}],
        _holdings(), 50000.0, dry_run=False)

    assert len(broker.orders) == 1 and out and out[0]["volume"] == 300
    assert not any(d.get("mode") == "exec_window_skip" for d in env.lines)


# ---------- 3. 条件位 dup → 重新布防 ----------

class DupBroker:
    """桥对同 plan 号永久判重复，且当日委托列表里查不到可接管的单。"""

    def __init__(self, message="plan 已执行过"):
        self.message = message

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        return {"order_id": "", "status": "duplicate", "message": self.message}

    def get_orders(self):
        return []


def _rule(dup_since=None, plan_seq=0):
    r = {"code": SELL_CODE, "pct": 1.0, "reason": "证据链：跌破防守线",
         "created_ts": "2026-09-18T09:31:00.123456+08:00"}
    if plan_seq:
        r["plan_seq"] = plan_seq
    if dup_since:
        r["dup_since"] = dup_since
    return r


def _events(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    return F.EVENTS_FILE


def test_dup_with_freeze_starts_confirm_timer_only(monkeypatch, tmp_path):
    """首次判重复且无冻结 → 只起计时（dup_since），不立即换号。"""
    from live_price_watch import _execute_sell
    ev = _events(monkeypatch, tmp_path)
    rule = _rule()

    act = _execute_sell(DupBroker(), AGENT, rule, price=10.0, prev=10.0,
                        trig="stop_loss", avail=100, now=NOW, total=100)

    assert act == "keep"
    assert rule.get("dup_since") == NOW.isoformat()
    assert not rule.get("plan_seq")
    # 首次只留「需人工核对」事件，不产生 refire（不换号）
    events = json.loads(ev.read_text(encoding="utf-8"))
    kinds = {e["kind"] for e in events.values()}
    assert kinds == {"dup_unresolved"}


def test_dup_no_freeze_persisting_rearms(monkeypatch, tmp_path):
    """重复+无冻结持续 ≥3 分钟 → 换 plan 号重新布防 + 告警事件。"""
    from live_price_watch import DUP_REARM_SEC, _execute_sell
    ev = _events(monkeypatch, tmp_path)
    rule = _rule(dup_since=(NOW - timedelta(seconds=DUP_REARM_SEC + 60)).isoformat())

    act = _execute_sell(DupBroker(), AGENT, rule, price=10.0, prev=10.0,
                        trig="stop_loss", avail=100, now=NOW, total=100)

    assert act == "keep"
    assert rule.get("plan_seq") == 1 and "dup_since" not in rule
    events = json.loads(ev.read_text(encoding="utf-8"))
    refire = [e for e in events.values() if e["kind"] == "refire"]
    assert refire and refire[0]["alert"] is True


def test_dup_with_frozen_shares_never_rearms(monkeypatch, tmp_path):
    """有股份被冻结（avail < total，在途卖单真在柜台）→ 绝不换号。"""
    from live_price_watch import _execute_sell
    ev = _events(monkeypatch, tmp_path)
    rule = _rule(dup_since=(NOW - timedelta(minutes=10)).isoformat())

    act = _execute_sell(DupBroker(), AGENT, rule, price=10.0, prev=10.0,
                        trig="stop_loss", avail=50, now=NOW, total=100)

    assert act == "keep"
    assert not rule.get("plan_seq")


def test_same_day_direction_duplicate_never_rearms(monkeypatch, tmp_path):
    """「当日已有同方向成交」型重复 = 今天真卖过一笔 → 绝不重新布防。"""
    from live_price_watch import _execute_sell
    ev = _events(monkeypatch, tmp_path)
    rule = _rule(dup_since=(NOW - timedelta(minutes=10)).isoformat())

    act = _execute_sell(DupBroker(message="当日已有同方向成交, 跳过"), AGENT, rule,
                        price=10.0, prev=10.0, trig="stop_loss", avail=100,
                        now=NOW, total=100)

    assert act == "keep"
    assert not rule.get("plan_seq")
    events = json.loads(ev.read_text(encoding="utf-8"))
    assert any(e["kind"] == "dup_unresolved" for e in events.values())
