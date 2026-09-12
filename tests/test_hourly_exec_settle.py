"""整点轮执行路径的成交收口接线（2026-09-12 P0-4）。

`execute_intraday_decision` 的卖出/买入两条路径接到 `live_fills.settle_place_fill`
后，本文件用 mock 桥验证接线本身（收口逻辑的单元测试在 test_fill_remainder.py）：

  - 部分成交（100/300、委托仍在途）→ 已成交部分记账、余量挂 pending 且
    `volume_recorded=100`（reconcile 只补增量，绝不重复记账）；
  - 执行列表与日志带的量是**实际成交量**，且日志带 `remaining`；
  - wait_fill 超时（None）→ 照旧挂 pending、recorded=0；
  - 买入同口径。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_hourly_exec_settle.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_fills as F  # noqa: E402
import live_ledger as L  # noqa: E402

CN = F.CN_TZ
AGENT = "deepseek-v4-pro"
SELL_CODE = "001312.SZ"
BUY_CODE = "600362.SH"
NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()


class FakeBroker:
    """日线两根（涨跌 2%，非涨跌停、有量）；下单只回委托号，成交由 wait_fill 桩给。

    oid=None 模拟桥不回委托号（不可跟踪的委托）。
    """

    def __init__(self, oid="T8001"):
        self.orders = []
        self.oid = oid

    def get_klines(self, code, interval="daily", **kw):
        px = 10.0 if code == BUY_CODE else 17.5
        return [{"close": round(px * 0.98, 2), "volume": 1000},
                {"close": px, "volume": 1000}]

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("sell", code, vol, price))
        return {"order_id": self.oid, "status": "submitted"}

    def buy(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("buy", code, vol, price))
        return {"order_id": self.oid, "status": "submitted"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """账本/在途/事件/日志全隔离到 tmp；桥外依赖（熔断、当天开仓）显式放行。"""
    import live_breaker
    import live_hourly_analysis as H
    import live_trade_picks as TP

    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(TP, "LOG_DIR", tmp_path / "logs")

    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {
            SELL_CODE: {"volume": 300, "cost_price": 16.0, "buy_ts": TS, "last_ts": TS},
            BUY_CODE: {"volume": 100, "cost_price": 10.0, "buy_ts": TS, "last_ts": TS},
        }}}})

    monkeypatch.setattr(H, "now_cn", lambda: NOW)
    monkeypatch.setattr(live_breaker, "check_and_trip", lambda *a, **k: (False, ""))
    monkeypatch.setattr(H, "daily_buy_codes", lambda agent, day=None: set())
    monkeypatch.setattr("time.sleep", lambda s: None)   # 桥限流 1s 不必真睡

    lines = []
    monkeypatch.setattr(TP, "log_line", lambda doc: lines.append(doc))
    return SimpleNamespace(H=H, lines=lines)


def _holdings():
    return [{"code": SELL_CODE, "name": "福恩股份", "volume": 300, "cost": 16.0,
             "price": 17.5, "pnl_pct": 9.4, "day_chg": 1.6, "avail": 300},
            {"code": BUY_CODE, "name": "江西铜业", "volume": 100, "cost": 10.0,
             "price": 10.0, "pnl_pct": 0.0, "day_chg": 2.0, "avail": 100}]


def _wait_fill_returns(fill):
    return lambda *a, **kw: fill


# ---------- 卖出 ----------

def test_sell_partial_fill_tracks_remainder(env, monkeypatch):
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(
        {"order_id": "T8001", "status": "submitted",
         "filled_volume": 100, "filled_price": 17.0}))
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "t"}],
        _holdings(), 50000.0, dry_run=False)

    # 执行列表带的是实际成交量（旧代码同）——关键是账与余量
    assert out == [{"action": "sell", "code": SELL_CODE, "volume": 100,
                    "price": 17.0, "reason": "t"}]
    pos = L.load_ledger()["agents"][AGENT]["positions"][SELL_CODE]
    assert pos["volume"] == 200 and pos["cost_price"] == 16.0
    pend = F.load_pending()
    assert len(pend) == 1
    assert pend[0]["volume"] == 300 and pend[0]["volume_recorded"] == 100
    # 日志写明余量（人读）；applied_fills 让 reconcile 只补增量
    fill_line = next(d for d in env.lines if d.get("fill"))
    assert fill_line["volume"] == 100 and fill_line["remaining"] == 200
    assert fill_line["cost_price"] == 16.0
    assert L.load_ledger()["applied_fills"]["T8001"]["filled"] == 100


def test_sell_wait_fill_timeout_keeps_old_pending_semantics(env, monkeypatch):
    """超时（None）→ 挂 pending、recorded=0（尚无成交回报，旧口径不变）。"""
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(None))
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "t"}],
        _holdings(), 50000.0, dry_run=False)

    assert out == []
    pend = F.load_pending()
    assert pend[0]["volume"] == 300 and pend[0]["volume_recorded"] == 0
    assert L.load_ledger()["agents"][AGENT]["positions"][SELL_CODE]["volume"] == 300
    assert [d for d in env.lines if d.get("pending")]


def test_sell_full_fill_attaches_no_pending(env, monkeypatch):
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(
        {"order_id": "T8001", "status": "filled",
         "filled_volume": 300, "filled_price": 17.5}))
    broker = FakeBroker()

    env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "t"}],
        _holdings(), 50000.0, dry_run=False)

    assert F.load_pending() == []
    # 清仓 → record_sell 把持仓整条移除（与账本口径一致）
    assert SELL_CODE not in (L.load_ledger()["agents"][AGENT]["positions"])


def test_sell_without_pct_sells_full_position(env, monkeypatch):
    """模型说「卖出」但漏了比例（pct_given=False）→ 按清仓执行，不是算 0 股跳过。"""
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(
        {"order_id": "T8001", "status": "filled",
         "filled_volume": 300, "filled_price": 17.5}))
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 0.0, "pct_given": False,
          "reason": "清仓"}],
        _holdings(), 50000.0, dry_run=False)

    assert broker.orders[0][2] == 300                      # 全量下单
    assert out[0]["volume"] == 300


def test_sell_explicit_zero_pct_stays_noop(env, monkeypatch):
    """明说 pct=0（不表达卖出量）→ 不下单（缺比例才是清仓口径）。"""
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(None))
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 0.0, "pct_given": True,
          "reason": "看看"}],
        _holdings(), 50000.0, dry_run=False)

    assert out == [] and broker.orders == []


def test_dirty_pct_sell_is_skipped_with_event(env, monkeypatch):
    """脏 pct（"0.3股"）≠ 没给比例：跳过 + 落 pct_unparsed 事件，绝不按清仓执行。

    旧口径「把想减 30% 执行成清仓」是 3.3 倍超卖（2026-09-12 审查 HIGH-2）。"""
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(None))
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 0.0, "pct_given": False,
          "pct_bad_raw": "'0.3股'", "reason": "减三成"}],
        _holdings(), 50000.0, dry_run=False)

    assert out == [] and broker.orders == []
    assert L.load_ledger()["agents"][AGENT]["positions"][SELL_CODE]["volume"] == 300
    ev = [e for e in json.loads(F.EVENTS_FILE.read_text(encoding="utf-8")).values()
          if e["kind"] == "pct_unparsed"]
    assert ev and ev[0]["alert"] is False and "0.3股" in ev[0]["msg"]


def test_nan_pct_sell_is_skipped_with_event_not_liquidated(env, monkeypatch):
    """`pct: NaN`（JSON 字面量，模型可产出）→ 跳过 + pct_unparsed 留痕，绝不整仓卖出。

    2026-09-12 终审 HIGH：NaN 被判 missing → sell_fraction 返 1.0 → 全量下单。
    方向与脏值停手原则相反且不可逆。"""
    from live_prompt_context import parse_intraday_decision

    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(None))
    broker = FakeBroker()
    decisions = parse_intraday_decision(
        '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": NaN}]}')

    out = env.H.execute_intraday_decision(broker, AGENT, decisions, _holdings(),
                                          50000.0, dry_run=False)

    assert out == [] and broker.orders == []
    assert L.load_ledger()["agents"][AGENT]["positions"][SELL_CODE]["volume"] == 300
    ev = [e for e in json.loads(F.EVENTS_FILE.read_text(encoding="utf-8")).values()
          if e["kind"] == "pct_unparsed"]
    assert ev and ev[0]["alert"] is False


def test_dry_run_dirty_pct_writes_no_event(env, monkeypatch):
    """dry-run 不得改动任何持久状态（2026-09-12 审查 LOW）：跳过文案照打（演练要看
    得出），但事件面不落盘——演练条目混进真实委托事件流，事后对账分不清。"""
    broker = FakeBroker()

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 0.0, "pct_given": False,
          "pct_bad_raw": "'0.3股'", "reason": "减三成"}],
        _holdings(), 50000.0, dry_run=True)

    assert out == [] and broker.orders == []
    assert not F.EVENTS_FILE.exists() or not [
        e for e in json.loads(F.EVENTS_FILE.read_text(encoding="utf-8")).values()
        if e["kind"] == "pct_unparsed"]


def test_untracked_order_blocks_duplicate_in_same_round(env, monkeypatch):
    """桥没回委托号 → 这笔单 reconcile 追不了：同轮第二条同 code 卖出必须被挡住
    （否则缺号 + 模型重复输出 = 重复下单，2026-09-12 审查 LOW-1）。"""
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(None))
    broker = FakeBroker(oid=None)

    env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "a"},
         {"action": "sell", "code": SELL_CODE, "pct": 1.0, "reason": "b"}],
        _holdings(), 50000.0, dry_run=False)

    assert len(broker.orders) == 1        # 第二笔被「执行前已有在途/不可跟踪」挡住
    assert F.load_pending() == []         # 缺号不写假在途单（只落 untracked_order 事件）
    ev = [e for e in json.loads(F.EVENTS_FILE.read_text(encoding="utf-8")).values()
          if e["kind"] == "untracked_order"]
    assert ev


# ---------- 买入 ----------

def test_buy_partial_fill_tracks_remainder(env, monkeypatch):
    monkeypatch.setattr(F, "wait_fill", _wait_fill_returns(
        {"order_id": "B8001", "status": "submitted",
         "filled_volume": 100, "filled_price": 10.0}))
    broker = FakeBroker(oid="B8001")

    out = env.H.execute_intraday_decision(
        broker, AGENT,
        [{"action": "buy", "code": BUY_CODE, "pct": 0.2, "reason": "加仓"}],
        _holdings(), 50000.0, dry_run=False, pool_codes={BUY_CODE})

    wanted = broker.orders[0][2]
    assert wanted > 100                                    # 确实是部分成交场景
    assert out == [{"action": "buy", "code": BUY_CODE, "volume": 100,
                    "price": 10.0, "reason": "加仓"}]
    pos = L.load_ledger()["agents"][AGENT]["positions"][BUY_CODE]
    assert pos["volume"] == 100 + 100 and pos["cost_price"] == 10.0
    pend = F.load_pending()
    assert pend[0]["side"] == "buy" and pend[0]["volume"] == wanted
    assert pend[0]["volume_recorded"] == 100
    fill_line = next(d for d in env.lines if d.get("fill"))
    assert fill_line["volume"] == 100 and fill_line["remaining"] == wanted - 100
