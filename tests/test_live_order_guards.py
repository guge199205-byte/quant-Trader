"""下单路径的生产化护栏（2026-09-11 移植 quantmind QMT 真单链路压测结论）。

quantmind 2026-09-10~11 对真账户压测（40327478）得到的确定性结论与在本仓的落点：

  1. 卖出保护价（跌停价）报单 = 扫单成交：挂 ¥2.21（跌停）成交 ¥2.34——挂单价只是
     「愿卖的最低」，成交按盘口买一。故止损卖出报跌停价：只要盘口有买盘一定成交，
     且不会真按跌停价卖出。原来的「现价×0.99」在近跌停时会报出**低于跌停价**的
     不合法价（本仓桥容忍，真柜台可能废单）。
  2. 跌停封死不是「跳过」，是**挂跌停价排队** + 如实告警（无买盘本来也卖不掉）。
  3. 幂等委托号：崩溃重试复用同一 plan_id，桥内去重（否则崩溃一次就重复卖一笔）。
  4. 在途闸门：同标的同方向已有未确认委托 → 不再下第二笔。
  5. 提交前价格保护带：报价超出涨跌停带（含 2% 容差）→ 拒单并告警，防程序 bug
     把离谱价格当市价单打出去；**取不到行情/市价本身在带外（新股等）则放行**。
  6. 未成交/部分成交/收盘未了结 → 需要人工知晓的事件（此前只写 jsonl，零告警）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_order_guards.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills as F  # noqa: E402
import live_price_watch as W  # noqa: E402
from ashare_rules import protect_sell_price  # noqa: E402

CN = W.CN_TZ
AGENT = "deepseek-v4-pro"
PREV = 17.22          # 昨收（福恩 001312.SZ）
LIMIT_DOWN = 15.50    # 17.22 × 0.9 = 15.498 → 四舍五入 15.50


class FakeBroker:
    """只记录下单调用；get_orders 用于 duplicate 后的委托号回捞。"""

    def __init__(self, orders=None, result=None):
        self.sold = []
        self._orders = orders or []
        self._result = result or {"order_id": "T1001", "status": "submitted",
                                  "message": "已受理"}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price,
                          "plan_id": plan_id})
        return dict(self._result)

    def get_orders(self, stock_code="", cancelable_only=False):
        return [o for o in self._orders if not stock_code
                or o.get("stock_code") == stock_code]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 pending/事件/日志文件；返回 (broker, rule, events读取器)。"""
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)

    def events():
        try:
            return json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
        except OSError:
            return {}
    broker = FakeBroker()
    rule = {"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None,
            "pct": 0.5, "reason": "测试", "created_ts": "2026-09-11T09:35:00+08:00"}
    return broker, rule, events


def _sell(broker, rule, price, prev=PREV, avail=1000, **kw):
    return W._execute_sell(broker, AGENT, rule, price, prev, "stop_loss", avail, **kw)


# ---------- 1. 保护价（止损卖出报跌停价） ----------

def test_stop_sell_reports_limit_down_protection_price(env):
    """常规盘中止损：报跌停保护价（不是现价-1%）——有买盘必成交，且报价一定合法。"""
    broker, rule, _ = env
    assert _sell(broker, rule, 17.50) is True
    assert broker.sold[0]["price"] == LIMIT_DOWN == protect_sell_price("001312.SZ", PREV)


def test_limit_down_day_still_places_queued_order(env):
    """跌停封死：不再「跳过保留」，而是挂跌停价排队 + 留一条告警事件。"""
    broker, rule, events = env
    px = 15.49   # 已跌破跌停价（-10.05%）
    assert _sell(broker, rule, px) is True
    assert broker.sold[0]["price"] == LIMIT_DOWN
    assert "protect_queue" in json.dumps(events(), ensure_ascii=False)


def test_prev_missing_falls_back_to_minus_one_pct(env):
    """昨收缺失（K 线不足/脏数据）：降级回现价-1% 并留痕，不臆造保护价。"""
    broker, rule, _ = env
    assert _sell(broker, rule, 17.50, prev=0) is True
    assert broker.sold[0]["price"] == round(17.50 * 0.99, 2)


# ---------- 2. 在途闸门（不重复下单） ----------

def test_inflight_sell_blocks_second_order(env):
    """同标的有在途卖单 → 本轮不下单、条件位保留（防崩溃重试/多轮重复卖）。"""
    broker, rule, _ = env
    F.add_pending("T1", AGENT, "001312.SZ", "sell", 100, 15.50,
                  "2026-09-11T10:00:00+08:00")
    assert _sell(broker, rule, 17.50) is False
    assert broker.sold == []
    assert F.inflight("001312.SZ", "sell")


def test_inflight_other_side_does_not_block(env):
    """买方向的在途单不该挡住卖出。"""
    broker, rule, _ = env
    F.add_pending("T1", AGENT, "001312.SZ", "buy", 100, 15.50,
                  "2026-09-11T10:00:00+08:00")
    assert _sell(broker, rule, 17.50) is True
    assert len(broker.sold) == 1


# ---------- 3. 幂等委托号（崩溃重试不重复下单） ----------

def test_plan_id_stable_for_same_rule_and_differs_per_rule(env):
    """同一条件位（重启重试）plan_id 不变；规则刷新（新 created_ts）换号。"""
    _, rule, _ = env
    a = W._watch_plan_id(AGENT, rule)
    assert a == W._watch_plan_id(AGENT, rule)
    rule2 = dict(rule, created_ts="2026-09-11T10:35:00+08:00")
    assert W._watch_plan_id(AGENT, rule2) != a
    rule3 = dict(rule, code="600309.SH")
    assert W._watch_plan_id(AGENT, rule3) != a


def test_plan_id_passed_to_broker(env):
    broker, rule, _ = env
    _sell(broker, rule, 17.50)
    assert broker.sold[0]["plan_id"] == W._watch_plan_id(AGENT, rule)


def test_duplicate_plan_recovers_order_id_from_today_orders(env):
    """桥回 duplicate（上次已受理、本地没存下）→ 回捞当日委托号补挂 pending，
    否则这笔成交永远进不了分账账本。"""
    broker, rule, _ = env
    broker._result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    broker._orders = [{"order_id": "W777", "stock_code": "001312.SZ", "side": "sell",
                       "order_price": LIMIT_DOWN, "total_volume": 500,
                       "filled_volume": 500, "status": "filled"}]
    assert _sell(broker, rule, 17.50) is True   # 已下过 → 条件位消费
    pend = F.load_pending()
    assert [p["order_id"] for p in pend] == ["W777"]


def test_duplicate_without_recovery_records_event(env):
    """duplicate 但回捞不到委托号：条件位消费（桥已执行过）但必须留告警。"""
    broker, rule, events = env
    broker._result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    assert _sell(broker, rule, 17.50) is True
    assert "dup_unresolved" in json.dumps(events(), ensure_ascii=False)


# ---------- 4. 事件留痕（reconcile 的终态/收盘提醒） ----------

def test_reconcile_records_unfilled_terminal_event(env):
    """委托终态未成交（废单/撤单/过期）→ 事件告警：止损没卖出去必须让人知道。"""
    broker, _, events = env
    F.add_pending("T9", AGENT, "001312.SZ", "sell", 500, LIMIT_DOWN,
                  "2026-09-11T10:00:00+08:00")
    broker._orders = [{"order_id": "T9", "stock_code": "001312.SZ", "side": "sell",
                       "status": "rejected", "filled_volume": 0, "filled_price": 0}]
    F.reconcile(broker)
    assert "unfilled" in json.dumps(events(), ensure_ascii=False)
    assert F.load_pending() == []


def test_close_reminder_recorded_once_for_resting_order(env):
    """14:50 后仍未了结的在途单：提醒「将随日终失效」——一天一次不刷屏。"""
    broker, _, events = env
    F.add_pending("T8", AGENT, "001312.SZ", "sell", 500, LIMIT_DOWN,
                  "2026-09-11T13:00:00+08:00")
    broker._orders = [{"order_id": "T8", "stock_code": "001312.SZ", "side": "sell",
                       "status": "submitted", "filled_volume": 0, "filled_price": 0}]
    now = datetime(2026, 9, 11, 14, 52, tzinfo=CN)
    F.reconcile(broker, now=now)
    F.reconcile(broker, now=now)
    ev = events()
    assert sum(1 for k in ev if k.startswith("close_pending")) == 1


# ---------- 5. 提交前价格保护带（tdx_bridge） ----------

def _bridge(monkeypatch, bars):
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    b = TdxBridgeBroker({"bridge_url": "http://127.0.0.1:1", "token": "x"})
    monkeypatch.setattr(b, "get_klines", lambda *a, **kw: bars)
    sent = {}

    def _post(path, payload, timeout, idempotent=True):
        sent["path"], sent["payload"] = path, payload
        return {"orders": [{"order_id": "X1", "status": "submitted"}]}

    monkeypatch.setattr(b, "_post", _post)
    return b, sent


_BARS = [{"date": "2026-09-10", "close": 10.00},
         {"date": "2026-09-11", "close": 10.05}]


def test_price_band_rejects_absurd_price(monkeypatch):
    """报价远超涨停价（程序/模型 bug 的离谱价）→ 本地拒单，不送到桥。"""
    from agent_tools.brokers.base import BrokerError

    b, sent = _bridge(monkeypatch, _BARS)
    with pytest.raises(BrokerError):
        b.sell(None, None, "600000.SH", 100, price=100.0)
    assert not sent


def test_price_band_allows_protection_price(monkeypatch):
    """跌停保护价（= 当日跌停价）合法放行。"""
    b, sent = _bridge(monkeypatch, _BARS)
    b.sell(None, None, "600000.SH", 100, price=9.00)
    assert sent["payload"]["orders"][0]["price"] == 9.00


def test_price_band_fails_open_when_market_outside_band(monkeypatch):
    """市价本身在带外（新股首日无涨跌幅等，本模块不追踪上市日）→ 规则模型不成立，
    放行不拦（不能因为自家涨跌停口径过窄把正常单拒了）。"""
    b, sent = _bridge(monkeypatch, [{"date": "2026-09-10", "close": 10.00},
                                    {"date": "2026-09-11", "close": 15.00}])
    b.sell(None, None, "600000.SH", 100, price=14.60)
    assert sent["payload"]["orders"][0]["price"] == 14.60


def test_price_band_fails_open_without_klines(monkeypatch):
    """取不到昨收（桥抖动）→ 放行（交易可用性优先，柜台仍兜底）。"""
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    b = TdxBridgeBroker({"bridge_url": "http://127.0.0.1:1", "token": "x"})

    def _boom(*a, **kw):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(b, "get_klines", _boom)
    sent = {}
    monkeypatch.setattr(b, "_post", lambda p, pl, t, idempotent=True: sent.update(
        {"payload": pl}) or {"orders": [{"order_id": "X1", "status": "submitted"}]})
    b.sell(None, None, "600000.SH", 100, price=100.0)
    assert sent["payload"]["orders"][0]["price"] == 100.0


def test_custom_plan_id_used_in_payload(monkeypatch):
    b, sent = _bridge(monkeypatch, _BARS)
    b.sell(None, None, "600000.SH", 100, price=10.00, plan_id="watch-x-1")
    assert sent["payload"]["plan_id"] == "watch-x-1"
