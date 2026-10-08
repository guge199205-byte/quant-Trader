"""手工下单路径（live_trade_picks，操作员 CLI）的两条生产口径。

2026-09-11 前 sell_flow/buy 段都是「桥受理即记账」，且按**委托限价/决策价**入账：

  - 按委托限价即刻扣减持仓、释放额度 —— 没成交（跌停封死/超时未撮合）也照样记，
    账本与真实持仓脱节，之后所有分账额度/杠杆/闸门都建立在假账上；
  - 与自动路径（哨兵/整点轮/09:35）不一致：那三条早已 wait_fill → add_pending →
    reconcile 兜底，只有手工路径还在记「委托价成交」。

现在手工路径也走同一口径：等真实成交回报按 filled_price/filled_volume 记账，
未确认成交挂 pending 由 reconcile 兜底；已有在途卖单时**提示**操作员（人工路径
以操作员意图为准，不做硬拦——自动路径才是硬拦）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_trade_picks_manual.py -q
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills  # noqa: E402
import live_trade_picks as P  # noqa: E402

CODE = "001312.SZ"
AGENT = "deepseek-v4-pro"


class _Resp:
    def json(self):
        return {"status": "ok", "tdx_connected": True}


class FakeBroker:
    """桥桩：健康检查/持仓/日K/下单/当日委托。"""

    bridge_url = "http://127.0.0.1:8550"

    def __init__(self, avail=500, result=None):
        self._avail = avail
        self._result = result or {"order_id": "T1001", "status": "submitted"}
        self.sold = []

    def _account_query(self):
        return {"asset": {"asset": 200000.0, "cash": 50000.0},
                "positions": [{"stock_code": CODE, "total_volume": self._avail,
                               "available_volume": self._avail}]}

    def get_klines(self, code, interval="daily", **kw):
        # 昨收 17.22 / 现价 17.50（volume 非 0：否则 compute_order 判「停牌」）
        return [{"close": 17.22, "volume": 1000000},
                {"close": 17.50, "volume": 1200000}]

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price})
        return dict(self._result)

    def get_orders(self, stock_code="", cancelable_only=False):
        return []


@pytest.fixture
def manual(monkeypatch, tmp_path):
    """把盘面/账本/在途单全换成桩，只测记账时机与提示。返回记录容器。"""
    import requests
    from agent_tools.datasources import tdx_aidata

    rec = {"ledger": [], "saved": 0, "pending": [], "wallet": 0.0, "logs": []}
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending_file)
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp())
    monkeypatch.setattr(tdx_aidata, "available", lambda: False)   # 行情走桥回退

    ledger = {"agents": {AGENT: {"positions": {CODE: {"volume": 500, "cost_price": 16.0}}}}}
    monkeypatch.setattr(P, "load_ledger", lambda: ledger)
    # log_line 默认落 logs/live_trade_*.jsonl —— 那是生产告警面（alert.sh 按它判
    # 「最近一笔成功成交」），测试写入会掩盖真实停滞，一律换桩
    monkeypatch.setattr(P, "log_line", rec["logs"].append)
    monkeypatch.setattr(P, "find_holder", lambda led, code: AGENT)
    monkeypatch.setattr(P, "agent_remaining", lambda led, agent: rec["wallet"])
    monkeypatch.setattr(P, "save_ledger", lambda led: rec.__setitem__("saved", rec["saved"] + 1))

    def _record_sell(led, agent, code, vol, price, ts):
        rec["ledger"].append((agent, code, vol, price))
        return led

    def _record_buy(led, agent, code, vol, price, ts):
        rec["ledger"].append((agent, code, vol, price))
        return led

    monkeypatch.setattr(P, "record_sell", _record_sell)
    monkeypatch.setattr(P, "record_buy", _record_buy)

    def _add_pending(order_id, agent, code, side, volume, price, ts,
                     protect=False, recorded=0):
        # 与真实 add_pending 同口径：空委托号不写假在途单（只落 untracked_order 事件）
        # （recorded/protect 本路径用不到，保留形参以免签名漂移时静默 TypeError）
        if not order_id:
            return
        rec["pending"].append({"order_id": order_id, "agent": agent, "code": code,
                               "side": side, "volume": volume, "price": price})

    monkeypatch.setattr(live_fills, "add_pending", _add_pending)
    rec["path"] = pending_file
    return rec


def _args(**kw):
    base = {"execute": True, "sell_all": True, "sell_codes": None, "sell_pct": 1.0}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _fill(volume, price):
    return {"order_id": "T1001", "status": "part_filled",
            "filled_volume": volume, "filled_price": price}


# ---------- 记账时机：按真实成交，不按委托限价 ----------

def test_sell_flow_records_real_fill_price(manual, monkeypatch):
    """成交 500 股 @17.30：账本按真实成交价，不按限价 ¥17.33（现价 -1%）。"""
    broker = FakeBroker()
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(500, 17.30))

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    # 17.50×0.99 = 17.325 → HALF_UP 到分 = 17.33（2026-09-21 审查 MEDIUM-5：旧实现
    # 用内建 round() 走二进制银行家舍入得 17.32，与哨兵/整点轮的交易所口径不一致）
    assert broker.sold[0]["price"] == 17.33          # 委托限价（现价 17.50 -1%）
    assert manual["ledger"] == [(AGENT, CODE, 500, 17.30)]   # 记账用成交价
    assert manual["pending"] == []
    assert manual["saved"] == 1


def test_sell_flow_pends_when_not_filled(manual, monkeypatch):
    """限价没成交（wait_fill 超时）→ 不记假账，挂 pending 交 reconcile 兜底。"""
    broker = FakeBroker()
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: None)

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert manual["ledger"] == []                    # 关键：没成交就不动账本
    assert manual["saved"] == 0
    assert manual["pending"] == [{"order_id": "T1001", "agent": AGENT, "code": CODE,
                                  "side": "sell", "volume": 500, "price": 17.33}]


def test_sell_flow_partial_fill_records_filled_only(manual, monkeypatch):
    """部分成交 200/500：只按已成交部分记账（其余由 reconcile 按后续增量补记）。"""
    broker = FakeBroker()
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(200, 17.28))

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert manual["ledger"] == [(AGENT, CODE, 200, 17.28)]


# ---------- 在途卖单提示 ----------

def test_sell_flow_warns_on_inflight_sell(manual, monkeypatch, capsys):
    """同代码已有在途卖单：提示操作员（人工路径不硬拦）。"""
    import live_fills as lf

    manual["path"].write_text(json.dumps([
        {"order_id": "T9001", "code": CODE, "side": "sell", "volume": 500}]),
        encoding="utf-8")
    broker = FakeBroker()
    monkeypatch.setattr(lf, "wait_fill", lambda b, oid, **kw: _fill(500, 17.30))

    P.sell_flow(broker, _args(), manual["logs"].append)

    out = capsys.readouterr().out
    assert "已有在途卖单未确认" in out
    assert len(broker.sold) == 1                     # 提示不拦截：操作员要卖就卖


def test_sell_flow_no_warning_when_nothing_inflight(manual, monkeypatch, capsys):
    broker = FakeBroker()
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(500, 17.30))

    P.sell_flow(broker, _args(), manual["logs"].append)

    assert "已有在途卖单未确认" not in capsys.readouterr().out


def test_sell_flow_untracked_code_no_pending(manual, monkeypatch, capsys):
    """账本里没有持有该代码的 agent（手工仓位）→ 不记账也不挂 pending（无处可记）。"""
    monkeypatch.setattr(P, "find_holder", lambda led, code: None)
    broker = FakeBroker()
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: None)

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert manual["ledger"] == [] and manual["pending"] == []
    assert "无分账 agent 持有该代码" in capsys.readouterr().out


def test_sell_flow_no_order_id_warns_instead_of_claiming_pending(manual, monkeypatch, capsys):
    """桥回 200 但无委托号 → 不谎报「已挂 pending」（没号根本挂不上，成交跟踪不了）。"""
    broker = FakeBroker(result={"order_id": "", "status": "unknown",
                                "message": "桥未返回委托号（受理状态未知）"})
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: None)

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    out = capsys.readouterr().out
    assert "桥未返回委托号" in out and "已挂 pending" not in out
    assert manual["pending"] == []


# ---------- 买入路径（main()）：同一记账口径 ----------

class FakeBuyBroker(FakeBroker):
    def __init__(self, cash=50000.0):
        super().__init__(avail=0)
        self._cash = cash
        self.bought = []

    def _account_query(self):
        return {"asset": {"asset": 200000.0, "cash": self._cash}, "positions": []}

    def buy(self, sig, date, code, vol, price=None, plan_id=None):
        self.bought.append({"code": code, "volume": vol, "price": price})
        return {"order_id": "B1", "status": "submitted"}


@pytest.fixture
def buy_env(manual, monkeypatch, tmp_path):
    """买入主流程桩：选股 subprocess / 桥 / agent 名单 / 账本全换掉。"""
    import subprocess
    from agent_tools.datasources import tdx_aidata

    broker = FakeBuyBroker()
    picks = [{"code": CODE, "name": "福恩股份", "side": "BUY", "score": 0.9}]
    monkeypatch.setattr(P, "TdxBridgeBroker", lambda: broker)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: types.SimpleNamespace(
        stdout=json.dumps(picks), returncode=0))
    monkeypatch.setattr(tdx_aidata, "available", lambda: False)
    monkeypatch.setattr(P, "enabled_agents", lambda: [AGENT])
    monkeypatch.setattr(P, "agent_used", lambda led, agent: 0.0)
    monkeypatch.setattr(sys, "argv", ["live_trade_picks.py", "--execute"])
    manual["wallet"] = 100000.0          # 该 agent 剩余额度（manual fixture 的口径）
    return manual, broker


def test_buy_records_real_fill_price(buy_env, monkeypatch):
    """买入成交 500 股 @17.35：账本按成交价，不按决策价（现价 17.50）。"""
    rec, broker = buy_env
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(500, 17.35))

    P.main()

    assert len(broker.bought) == 1
    assert rec["ledger"] == [(AGENT, CODE, 500, 17.35)]
    assert rec["pending"] == [] and rec["saved"] == 1


def test_buy_pends_when_not_filled(buy_env, monkeypatch):
    """买入未确认成交 → 不记假账，挂 pending 由 reconcile 兜底。"""
    rec, broker = buy_env
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: None)

    P.main()

    assert len(broker.bought) == 1
    assert rec["ledger"] == [] and rec["saved"] == 0
    assert rec["pending"][0]["side"] == "buy" and rec["pending"][0]["agent"] == AGENT


# ---------- 板块口径：跌停判据 / 手数 / 限价带 ----------

class BoardBroker(FakeBroker):
    """按给定板块标的与昨收/现价返回盘面的桥桩。"""

    def __init__(self, code, prev_close, price, avail=500):
        super().__init__(avail=avail)
        self._code, self._prev, self._price = code, prev_close, price

    def _account_query(self):
        return {"asset": {"asset": 200000.0, "cash": 50000.0},
                "positions": [{"stock_code": self._code, "total_volume": self._avail,
                               "available_volume": self._avail}]}

    def get_klines(self, code, interval="daily", **kw):
        return [{"close": self._prev, "volume": 1000000},
                {"close": self._price, "volume": 1200000}]


def test_sell_flow_chinext_minus_12pct_is_not_limit_down(manual, monkeypatch, capsys):
    """创业板 ±20%：-12% 不是跌停。原 `chg <= -9.9` 硬编码会把可卖的仓位误判成
    「跌停卖不出」而跳过——止损单在 -10%~-20% 区间永远发不出去。"""
    code = "300777.SZ"
    broker = BoardBroker(code, prev_close=20.00, price=17.60)      # -12.0%
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(500, 17.55))

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert len(broker.sold) == 1 and broker.sold[0]["code"] == code
    assert "跌停" not in capsys.readouterr().out


def test_sell_flow_star_odd_lot_sold_at_once(manual, monkeypatch):
    """科创板 250 股：1 股递增、剩余不足 200 会成「只能一次性卖」的碎股 →
    一次性全清 250 股。原 `int(avail*pct/100)*100` 只会卖 200，剩 50 股烂在账户。"""
    code = "688111.SH"
    broker = BoardBroker(code, prev_close=20.00, price=20.40, avail=250)
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(250, 20.35))

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert broker.sold[0]["volume"] == 250


def test_sell_flow_limit_clamped_into_band_near_limit_down(manual, monkeypatch):
    """近跌停（-9.5%，主板）：现价 -1% = ¥17.92 已跌破跌停价 ¥18.00 —— 桥的
    本地价格保护带会直接拒单。限价钳到跌停价（=当日最激进合法报价）。"""
    from ashare_rules import protect_sell_price

    broker = BoardBroker(CODE, prev_close=20.00, price=18.10)      # -9.5%，未跌停
    monkeypatch.setattr(live_fills, "wait_fill", lambda b, oid, **kw: _fill(500, 18.05))

    assert P.sell_flow(broker, _args(), manual["logs"].append) == 0

    assert protect_sell_price(CODE, 20.00) == 18.00
    assert broker.sold[0]["price"] == 18.00

