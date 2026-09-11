"""分钟级价格哨兵的三道新护栏（2026-09-10 实录）。

当日事故链：桥「假活」——行情通道正常，account/query 却返回 asset=0 / positions=[]：

  1) 09:30 哨兵把空快照当成「已不在持仓中」，把 v4-pro(001312.SZ)、glm(600309.SH)
     的真实止损位当垃圾清掉——持仓裸奔一整天；
  2) 同一批规则里，复盘同步把『涨到17.90减50%』写成 stop_loss，现价 17.50 在阈值
     下方 → 开盘即「反向假触发」（前一交易日 18.10/80.00 已演过一次）；
  3) 11:13 又把上证指数的低吸观察价 49.50 当止损触发，账户空仓 → 白触发。

本文件测三道闸：
  - 账户快照退化（资产<=0）→ 整轮跳过，条件位一条不消费；
  - `avail is None` 时先查台账，真实持仓的规则绝不误删；
  - 方向自洽：止损须低于昨收、止盈须高于昨收，标错的按触发语义翻转。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_watch_guards.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_price_watch as W  # noqa: E402

CN = W.CN_TZ
NOW = datetime(2026, 9, 11, 10, 0, 0, tzinfo=CN)   # 盘中，非收盘集合竞价
PREV = 17.22                                        # 昨收（福恩 09-10 收盘）
AGENT = "deepseek-v4-pro"
HELD = {"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None,
        "move_stop": None, "pct": 0.5, "reason": "测试"}
HEALTHY = {"asset": {"asset": 300000.0, "cash": 1000.0},
           "positions": [{"stock_code": "001312.SZ", "available_volume": 100}]}
MISSING = {"asset": {"asset": 300000.0, "cash": 1000.0},
           "positions": [{"stock_code": "600000.SH", "available_volume": 100}]}
FAKE_ALIVE = {"asset": {"asset": 0.0, "cash": 0.0}, "positions": []}


class _FakeBroker:
    def __init__(self, acct):
        self._acct = acct

    def _account_query(self):
        return self._acct

    def sell(self, *a, **kw):
        raise AssertionError("退化快照/保留规则时不该真的下单")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """真实读写临时 watch 文件 + 桩掉外部依赖。

    返回 (records, watch_path)；records["sell"] 记录真实下单调用，
    records["prices"] 是 (现价, 昨收) 序列——多轮测试可依次出价。
    """
    rec = {"sell": [], "prices": [(17.50, PREV)], "rules": [dict(HELD)],
           "execute_sell": W._execute_sell}          # 原函数：测 avail=None 台账交叉那组要用
    wf = tmp_path / "live_watch.json"

    def _write():
        wf.write_text(json.dumps({AGENT: rec["rules"]}, ensure_ascii=False), encoding="utf-8")

    rec["write"] = _write
    _write()
    monkeypatch.setattr(W, "WATCH_FILE", wf)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)

    def _price(broker, code):
        px = rec["prices"][0] if len(rec["prices"]) == 1 else rec["prices"].pop(0)
        return px

    monkeypatch.setattr(W, "_last_price", _price)

    def _sell(broker, agent, rule, price, prev, trig, avail, **kw):
        rec["sell"].append((agent, rule["code"], trig, price, avail))
        return True

    monkeypatch.setattr(W, "_execute_sell", _sell)

    import live_fills
    import live_hourly_analysis

    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    # 在途单/委托事件都隔离到 tmp：测试不得读写真实 data/（会污染生产告警面）
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)
    return rec, wf


def _saved(wf) -> list:
    return json.loads(wf.read_text(encoding="utf-8")).get(AGENT) or []


# ---------- 闸一：账户快照退化（桥假活）----------

def test_fake_alive_snapshot_skips_round_and_keeps_rules(env):
    """资产 0 + 空持仓（桥假活）→ 整轮跳过：无下单，条件位原样保留。

    事故当日：止损 16.6 的条件位在现价 16.50 下本该触发，空快照直接把它判成
    「已不在持仓中」销毁——修复后应一条不消费、文件不动。
    """
    rec, wf = env
    rec["prices"] = [(16.50, PREV)]

    fired = W.run_watch(_FakeBroker(FAKE_ALIVE), now=NOW)

    assert fired == 0
    assert rec["sell"] == []
    assert _saved(wf) == [HELD]


def test_healthy_snapshot_still_triggers(env):
    """对照组：资产正常的快照下同一条件位照常触发（护栏不误伤正常链路）。"""
    rec, wf = env
    rec["prices"] = [(16.50, PREV)]

    fired = W.run_watch(_FakeBroker(HEALTHY), now=NOW)

    assert fired == 1
    assert rec["sell"] == [(AGENT, "001312.SZ", "stop_loss", 16.50, 100)]


# ---------- 闸二：avail is None 时与台账交叉 ----------

def test_missing_position_kept_when_ledger_still_holds(env, monkeypatch):
    """快照里没有该持仓、但台账仍持有 → 保留条件位（疑似账号掉线，不能当已清仓）。"""
    rec, wf = env
    rec["prices"] = [(16.50, PREV)]
    monkeypatch.setattr(W, "_execute_sell", rec["execute_sell"])   # 走真实分支
    monkeypatch.setattr(W, "_ledger_holds", lambda agent, code: True)

    fired = W.run_watch(_FakeBroker(MISSING), now=NOW)

    assert fired == 0
    assert rec["sell"] == []
    saved = _saved(wf)
    assert len(saved) == 1 and saved[0]["stop_loss"] == 16.6


def test_missing_position_discarded_when_ledger_agrees(env, monkeypatch):
    """台账也不持有（真已清仓，如 09-09 卖掉的 600362.SH）→ 照旧作废。"""
    rec, wf = env
    rec["prices"] = [(16.50, PREV)]
    monkeypatch.setattr(W, "_execute_sell", rec["execute_sell"])   # 走真实分支
    monkeypatch.setattr(W, "_ledger_holds", lambda agent, code: False)

    fired = W.run_watch(_FakeBroker(MISSING), now=NOW)

    assert fired == 1
    assert rec["sell"] == []
    assert _saved(wf) == []


def test_ledger_holds_reads_real_ledger(tmp_path, monkeypatch):
    """_ledger_holds 直连台账：持仓→True；无该代码→False；台账读不到→保守 True。"""
    import live_ledger

    p = tmp_path / "live_ledger.json"
    p.write_text(json.dumps({"version": 1, "agents": {
        AGENT: {"positions": {"001312.SZ": {"volume": 1100}}}}}), encoding="utf-8")
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", p)

    assert W._ledger_holds(AGENT, "001312.SZ") is True
    assert W._ledger_holds(AGENT, "600000.SH") is False
    assert W._ledger_holds("no-such-agent", "001312.SZ") is True    # 未知 → 保守保留

    monkeypatch.setattr(live_ledger, "LEDGER_FILE", tmp_path / "缺失.json")
    assert W._ledger_holds(AGENT, "001312.SZ") is True


# ---------- 闸三：方向自洽（止损<昨收<止盈）----------

def test_stop_above_prev_flipped_to_take_profit(env):
    """『涨到17.90减50%』被写成 stop_loss：现价 17.50 < 17.90 → 不再反向假触发。"""
    rec, wf = env
    rec["rules"] = [{"code": "001312.SZ", "stop_loss": 17.9, "take_profit": None,
                     "move_stop": None, "pct": 0.5, "reason": "涨到17.90减50%"}]
    rec["write"]()
    rec["prices"] = [(17.50, PREV)]

    fired = W.run_watch(_FakeBroker(HEALTHY), now=NOW)

    assert fired == 0                       # 修复前：17.50 <= 17.90 → 假触发卖出
    assert rec["sell"] == []
    r = _saved(wf)[0]
    assert r["take_profit"] == 17.9 and r["stop_loss"] is None


def test_take_profit_below_prev_flipped_to_stop_loss(env):
    """止盈位写在昨收下方（该跌不涨的位）→ 按止损执行，破位能卖出去。"""
    rec, wf = env
    rec["rules"] = [{"code": "001312.SZ", "stop_loss": None, "take_profit": 16.0,
                     "move_stop": None, "pct": 0.5, "reason": "回落到16.0减半"}]
    rec["write"]()
    rec["prices"] = [(15.90, PREV)]

    fired = W.run_watch(_FakeBroker(HEALTHY), now=NOW)

    assert fired == 1
    assert rec["sell"] == [(AGENT, "001312.SZ", "stop_loss", 15.90, 100)]
    assert _saved(wf) == []                 # 触发后消费


def test_move_stop_above_prev_not_flipped(env):
    """move_stop 上移的止损可以高于昨收（浮盈锁利）——不能被当成方向标错。"""
    rec, wf = env
    rec["rules"] = [{"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None,
                     "move_stop": 17.5, "pct": 0.5, "reason": "测试"}]
    rec["write"]()
    rec["prices"] = [(17.60, PREV), (17.45, PREV)]

    W.run_watch(_FakeBroker(HEALTHY), now=NOW)          # 第 1 轮：只上移，不触发
    assert rec["sell"] == []
    r = _saved(wf)[0]
    assert r["stop_loss"] == 17.5 and r["take_profit"] is None

    fired = W.run_watch(_FakeBroker(HEALTHY), now=NOW)  # 第 2 轮：回落到 17.45 触发
    assert fired == 1
    assert rec["sell"] == [(AGENT, "001312.SZ", "stop_loss", 17.45, 100)]


def test_two_sided_rule_drops_wrong_side(env):
    """止损止盈都在昨收同侧 → 丢掉标错的一侧，正确的一侧照常值守。"""
    rec, wf = env
    rec["rules"] = [{"code": "001312.SZ", "stop_loss": 18.0, "take_profit": 19.0,
                     "move_stop": None, "pct": 1.0, "reason": "测试"}]
    rec["write"]()
    rec["prices"] = [(17.50, PREV)]

    W.run_watch(_FakeBroker(HEALTHY), now=NOW)

    r = _saved(wf)[0]
    assert r["stop_loss"] is None and r["take_profit"] == 19.0
    assert rec["sell"] == []
