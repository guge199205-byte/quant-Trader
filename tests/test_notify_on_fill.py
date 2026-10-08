"""成交才推送（2026-09-18 口径）的接线测试。

背景：此前四条下单路径与哨兵在**提交时刻**就推「清仓/买入」——300408、600176
两笔卖单提交时推了「清仓」，随后都被柜台判废（rejected，0 成交）。现在的口径：

  - 真成交（settle 内联记账 / reconcile 迟补记账）→ `_push_fill_notice` 推一条；
  - 零成交（废单/被撤/未成交）→ 不推成功文案；失败原因由委托事件（unfilled…，
    alert=True）经 alert.sh 推送。

conftest 的 `_isolate_production_state` 已把 `live_fills._push_fill_notice` 换成
记录器（`.sent` 列表 / `.original` 原函数），这里直接断言记录。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_fills as F  # noqa: E402
import live_ledger as L  # noqa: E402
import push_notify as pn  # noqa: E402

CN = F.CN_TZ
AGENT = "deepseek-v4-pro"
CODE = "001312.SZ"
NOW = datetime(2026, 9, 18, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()


def _seed_ledger(volume: int = 300) -> None:
    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {CODE: {"volume": volume, "cost_price": 16.0,
                             "buy_ts": "2026-09-10T09:37:00+08:00", "last_ts": TS}}}}})


# ---------- settle_place_fill：成交才推 ----------

def test_settle_full_fill_pushes_once():
    _seed_ledger(300)
    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 17.3,
                              {"order_id": "T1", "status": "filled",
                               "filled_volume": 300, "filled_price": 17.4},
                              reason="到点止盈", source="整点轮")

    assert rec["filled"] == 300 and rec["remaining"] == 0
    sent = F._push_fill_notice.sent
    assert len(sent) == 1
    n = sent[0]
    assert (n["side"], n["code"], n["filled"], n["price"]) == ("sell", CODE, 300, 17.4)
    assert n["agent"] == AGENT and n["order_id"] == "T1"
    assert n["held_before"] == 300        # 记账前持有量（清仓标签用）
    assert n["reason"] == "到点止盈" and n["source"] == "整点轮"
    assert n["remaining"] == 0


def test_settle_partial_fill_pushes_with_remaining():
    _seed_ledger(300)
    F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 17.3,
                        {"order_id": "T1", "status": "submitted",
                         "filled_volume": 100, "filled_price": 17.2})

    sent = F._push_fill_notice.sent
    assert len(sent) == 1
    assert sent[0]["filled"] == 100 and sent[0]["remaining"] == 200


def test_settle_terminal_zero_fill_pushes_nothing():
    _seed_ledger(300)
    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 17.3,
                              {"order_id": "T1", "status": "rejected",
                               "filled_volume": 0})

    assert rec["filled"] == 0 and rec["terminal"] == "rejected"
    assert F._push_fill_notice.sent == []            # 不推成功文案
    ev = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    assert any(e["kind"] == "unfilled" and e["alert"] for e in ev.values())  # 失败进告警面


# ---------- _push_fill_notice：转发口径 ----------

def test_push_fill_notice_maps_kwargs_to_notify_order(monkeypatch):
    calls = []
    monkeypatch.setattr(pn, "notify_order", lambda **kw: calls.append(kw))
    # 恢复被 conftest 替换的原函数
    monkeypatch.setattr(F, "_push_fill_notice", F._push_fill_notice.original)

    F._push_fill_notice(agent=AGENT, code=CODE, side="sell", filled=100, price=17.2,
                        order_id="T1", held_before=300, reason="止损",
                        source="条件位", remaining=200)

    assert len(calls) == 1
    kw = calls[0]
    assert kw["volume"] == 100 and kw["price"] == 17.2 and kw["order_id"] == "T1"
    assert kw["held_before"] == 300 and kw["source"] == "条件位"
    assert "部分成交 100 股" in kw["reason"] and "止损" in kw["reason"]


def test_push_fill_notice_never_raises(monkeypatch):
    def boom(**kw):
        raise RuntimeError("网络断了")

    monkeypatch.setattr(pn, "notify_order", boom)
    monkeypatch.setattr(F, "_push_fill_notice", F._push_fill_notice.original)
    F._push_fill_notice(agent=AGENT, code=CODE, side="buy", filled=100, price=10.0)


# ---------- reconcile：迟补成交也要推 ----------

class _FakeBroker:
    def __init__(self, orders):
        self._orders = orders

    def get_orders(self):
        return self._orders


def test_reconcile_delta_pushes_fill_notice():
    _seed_ledger(300)
    F.add_pending("T9", AGENT, CODE, "sell", 300, 17.0, TS, reason="跌破防守线")
    broker = _FakeBroker([{"order_id": "T9", "side": "sell", "stock_code": CODE,
                           "status": "filled", "filled_volume": 300,
                           "filled_price": 17.35, "total_volume": 300}])

    fills = F.reconcile(broker, now=NOW)

    assert fills == 1
    sent = F._push_fill_notice.sent
    assert len(sent) == 1
    n = sent[0]
    assert (n["filled"], n["price"], n["order_id"]) == (300, 17.35, "T9")
    assert n["held_before"] == 300 and n["reason"] == "跌破防守线"
    assert n["source"] == "对账补记"
    # 账本真的扣了
    pos = L.load_ledger()["agents"][AGENT]["positions"]
    assert CODE not in pos


def test_reconcile_rejected_zero_fill_pushes_no_success():
    _seed_ledger(300)
    F.add_pending("T9", AGENT, CODE, "sell", 300, 17.0, TS)
    broker = _FakeBroker([{"order_id": "T9", "side": "sell", "stock_code": CODE,
                           "status": "rejected", "filled_volume": 0,
                           "total_volume": 300}])

    F.reconcile(broker, now=NOW)

    assert F._push_fill_notice.sent == []
    assert L.load_ledger()["agents"][AGENT]["positions"][CODE]["volume"] == 300
    ev = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    assert any(e["kind"] == "unfilled" for e in ev.values())
