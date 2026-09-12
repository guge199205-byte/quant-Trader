"""下单回报的账务收口：部分成交的余量必须继续被跟踪（2026-09-12 P0-4）。

`wait_fill` 只要见到「有成交」就返回（`live_fills.py` 的 while 循环），于是
「100/300 成交、委托仍在途」这种最典型的部分成交下，旧代码把 100 记完就再也不管
剩下 200 股：

  - 委托不在 pending → reconcile 永远补不到 → 日后那 200 股成交是**账外成交**
    （账本与账户永久不一致，额度/闸门/告警全建立在假账上）；
  - 若 wait_fill 拿到的是终态 partial_cancelled，挂 pending 没有意义（下一轮
    reconcile 立刻移除），必须当场如实告知「剩余没卖出去」。

同时修一个会**重复记账**的坑：下单路径已内联记过成交后再挂 pending，若不带
「已记账量」，reconcile 的增量 = 桥 filled − 0 会把同一笔再记一次
（`record_sell` 量不足时直接删持仓 + 多记现金，见 live_ledger）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_fill_remainder.py -q
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
import live_ledger as L  # noqa: E402

CN = F.CN_TZ
NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()
CODE = "001312.SZ"
AGENT = "deepseek-v4-pro"


def _seed_ledger(volume: int = 1000) -> None:
    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {CODE: {"volume": volume, "cost_price": 16.0,
                             "buy_ts": TS, "last_ts": TS}}}}})


def _pos(code: str = CODE) -> dict:
    return L.load_ledger()["agents"][AGENT]["positions"][code]


def _events() -> dict:
    try:
        return json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    except OSError:
        return {}


# ---------- add_pending 的「已记账量」 ----------

def test_add_pending_carries_already_recorded_volume():
    """下单路径已内联记过 100 股 → pending 必须带着这个数字走。"""
    F.add_pending("T1", AGENT, CODE, "sell", 300, 15.5, TS, recorded=100)
    F.add_pending("T2", AGENT, CODE, "sell", 300, 15.5, TS)

    assert [p["volume_recorded"] for p in F.load_pending()] == [100, 0]


def test_reconcile_after_inline_fill_books_only_the_delta(monkeypatch):
    """内联记账 + recorded → reconcile 只补记增量，绝不重复记账。"""
    _seed_ledger()
    F.record_inline_fill(AGENT, CODE, "sell", 100, 17.0, "T1", TS)
    F.add_pending("T1", AGENT, CODE, "sell", 300, 15.5, TS, recorded=100)

    class Broker:
        def __init__(self, fv):
            self.fv = fv

        def get_orders(self):
            return [{"order_id": "T1", "status": "submitted", "filled_volume": self.fv,
                     "filled_price": 17.2, "total_volume": 300, "stock_code": CODE}]

    F.reconcile(Broker(100), now=NOW)           # 桥没多成交：一股都不许再记
    assert _pos()["volume"] == 900

    F.reconcile(Broker(160), now=NOW)           # 又多成交 60：只记 60
    assert _pos()["volume"] == 840
    assert L.load_ledger()["applied_fills"]["T1"]["filled"] == 160


# ---------- 内联记账与 reconcile 同锁同标记 ----------

def test_record_inline_fill_books_and_marks_applied():
    """内联记账写 applied_fills 幂等标记（与持仓变更同一次原子写）。

    少了它：记账后进程崩在 add_pending 之前 → 下一轮 reconcile 从 0 起算增量 →
    同一笔成交记两次。卖出也不许动余仓成本（成本基准是已完成 feed 的盈亏口径）。
    """
    _seed_ledger()

    cost = F.record_inline_fill(AGENT, CODE, "sell", 100, 17.0, "T1", TS)

    assert cost == 16.0                          # 记账前的成本价（日志用）
    assert _pos()["volume"] == 900 and _pos()["cost_price"] == 16.0
    applied = L.load_ledger()["applied_fills"]["T1"]
    assert applied["filled"] == 100 and applied["ts"] == "2026-09-12"


# ---------- settle_place_fill：四条下单路径共用的收口 ----------

def test_partial_fill_attaches_pending_for_remainder():
    """非终态部分成交 → 余量挂 pending（recorded=已记账量）继续跟踪。"""
    _seed_ledger()

    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5,
                              {"order_id": "T1", "status": "submitted",
                               "filled_volume": 100, "filled_price": 17.0}, ts=TS)

    assert rec["filled"] == 100 and rec["remaining"] == 200 and rec["pending"] is True
    assert _pos()["volume"] == 900
    p = F.load_pending()[0]
    assert p["order_id"] == "T1" and p["volume"] == 300 and p["volume_recorded"] == 100


def test_terminal_partial_fill_does_not_attach_pending_and_alerts():
    """终态 partial_cancelled → 不挂 pending（挂了也会被下一轮 reconcile 移除），
    当场落告警事件写明余量：剩余当日大概率补不上，人工要知道。"""
    _seed_ledger()

    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5,
                              {"order_id": "T1", "status": "partial_cancelled",
                               "filled_volume": 100, "filled_price": 17.0}, ts=TS)

    assert rec["filled"] == 100 and rec["pending"] is False and rec["remaining"] == 200
    assert _pos()["volume"] == 900
    assert F.load_pending() == []
    ev = [e for e in _events().values() if e["kind"] == "unfilled"]
    assert ev and ev[0]["alert"] is True and "100/300" in ev[0]["msg"]


def test_unresolved_fill_attaches_pending_quietly():
    """wait_fill 超时（返回 None）→ 照旧挂 pending（recorded=0）；
    限价单挂队是常态，不打扰人。"""
    _seed_ledger()

    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5, None, ts=TS)

    assert rec["filled"] == 0 and rec["pending"] is True and rec["remaining"] == 300
    assert F.load_pending()[0]["volume_recorded"] == 0
    assert _events() == {}


def test_full_fill_attaches_nothing_and_records_nothing():
    """满额成交 → 不挂 pending、不落事件（调用方照常写 fill_confirm 日志）。"""
    _seed_ledger()

    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5,
                              {"order_id": "T1", "status": "filled", "filled_volume": 300,
                               "filled_price": 17.0}, ts=TS)

    assert rec["filled"] == 300 and rec["pending"] is False and rec["remaining"] == 0
    assert F.load_pending() == [] and _events() == {}
    assert _pos()["volume"] == 700


def test_terminal_zero_fill_writes_abort_line_to_trade_jsonl(monkeypatch):
    """终态零成交（001312 报废单形态）→ 成交流水补一行 fill_abort。

    否则这次下单尝试只在账本外留了条中文事件：trade jsonl 里没有痕迹，
    decision_track.backfill 看不到「卖出决策被废单」的历史（2026-09-12 审查 LOW）。"""
    import live_trade_picks as TP

    _seed_ledger()
    monkeypatch.setattr(TP, "now_cn", lambda: NOW)

    rec = F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5,
                              {"order_id": "T1", "status": "rejected",
                               "filled_volume": 0, "filled_price": 0}, ts=TS)

    assert rec["filled"] == 0 and rec["terminal"] == "rejected"
    path = TP.LOG_DIR / f"live_trade_{NOW:%Y%m%d}.jsonl"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    ab = [r for r in rows if r.get("mode") == "fill_abort"]
    assert ab and ab[0]["code"] == CODE and ab[0]["side"] == "sell"
    assert ab[0]["filled"] == 0 and ab[0]["remaining"] == 300
    assert ab[0]["status"] == "rejected"
    # 事件面照旧（人读与告警上报）
    assert [e for e in _events().values() if e["kind"] == "unfilled"]


def test_untracked_order_flagged_and_no_fake_pending():
    """桥没回委托号 → untracked=True（调用方据此挡重复单）+ untracked_order 事件，
    绝不写一条 reconcile 追不了的假在途单。"""
    _seed_ledger()

    rec = F.settle_place_fill(None, AGENT, CODE, "sell", 300, 15.5,
                              {"status": "submitted", "filled_volume": 0}, ts=TS)

    assert rec["untracked"] is True and rec["pending"] is False
    assert rec["remaining"] == 300
    assert F.load_pending() == []
    ev = [e for e in _events().values() if e["kind"] == "untracked_order"]
    assert ev and ev[0]["alert"] is True and CODE in ev[0]["msg"]


def test_fill_price_override_is_used_for_booking():
    """坏 tick 护栏的成果要用上：调用方传入的 fill_price 覆盖桥回报价。"""
    _seed_ledger()

    F.settle_place_fill("T1", AGENT, CODE, "sell", 300, 15.5,
                        {"order_id": "T1", "status": "filled", "filled_volume": 100,
                         "filled_price": 999.0}, fill_price=17.0, ts=TS)

    assert L.load_ledger()["agents"][AGENT]["virtual_cash"] == 100000.0 + 100 * 17.0


def test_buy_partial_fill_marks_and_tracks_remainder():
    """买入同口径：部分成交记 100，余量挂 pending 跟踪。"""
    L.save_ledger({"version": 1, "agents": {}})

    rec = F.settle_place_fill("B1", AGENT, "600362.SH", "buy", 200, 10.0,
                              {"order_id": "B1", "status": "submitted",
                               "filled_volume": 100, "filled_price": 10.0}, ts=TS)

    assert rec["filled"] == 100 and rec["pending"] is True
    assert _pos("600362.SH")["volume"] == 100
    assert L.load_ledger()["applied_fills"]["B1"]["filled"] == 100
    assert F.load_pending()[0]["volume_recorded"] == 100
