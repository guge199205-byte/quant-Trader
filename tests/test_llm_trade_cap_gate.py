"""09:35 主入口（live_llm_trade._run）的买入执行段接线（2026-09-18 评审 M-4）。

两条执行路径共用 buy_gate/position_cap_reason 纯函数，但**接线各写一份**
（本仓库三次「同规则两处复制→漂移」教训的形态）。整点轮路线由
test_hourly_exec_settle 覆盖；本文件给 09:35 路线补上同款：
单票集中度闸在该路径为**成本口径**（equity = 虚拟现金 + 持仓成本，与它自己的
杠杆闸同基），超限拦截、限内放行，且拦截有人可见的打印。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_llm_trade_cap_gate.py -q
"""
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
BUY_CODE = "600362.SH"
NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()


class FakeBroker:
    def __init__(self, oid="B9001"):
        self.orders = []
        self.oid = oid

    def buy(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("buy", code, vol, price))
        return {"order_id": self.oid, "status": "submitted", "message": "提交交易成功！"}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("sell", code, vol, price))
        return {"order_id": self.oid, "status": "submitted"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """全外部依赖打桩：账户/持仓行/池/提示词/LLM/桥行情与在途集，其余走真实代码。"""
    import agent_commands
    import live_breaker
    import live_hourly_analysis as H
    import live_llm_trade as LT
    import live_prompt_context as PC

    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(LT, "STATE_FILE", tmp_path / "state.json")

    monkeypatch.setattr(LT, "now_cn", lambda: NOW)
    monkeypatch.setattr(H, "now_cn", lambda: NOW)   # drop_stale_decisions 用 H 自己的时钟
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(live_breaker, "check_and_trip", lambda *a, **k: (False, ""))
    monkeypatch.setattr(H, "daily_buy_codes", lambda agent, day=None: set())
    monkeypatch.setattr(H, "append_log", lambda *a, **k: None)
    monkeypatch.setattr(agent_commands, "apply_forced_sells", lambda *a, **k: None)
    monkeypatch.setattr(agent_commands, "has_halt_buy", lambda *a, **k: False)
    monkeypatch.setattr(LT, "inflight_codes", lambda side: set())
    monkeypatch.setattr(LT, "reconcile", lambda *a, **k: [])
    monkeypatch.setattr(LT, "log_line", lambda *a, **k: None)
    monkeypatch.setattr(PC, "filter_affordable", lambda pool, budget: (pool, []))
    monkeypatch.setattr(LT, "build_prompt", lambda *a, **k: "P")
    monkeypatch.setattr(LT, "pool_rows", lambda *a, **k: [])
    monkeypatch.setattr(LT, "lq_klines_batch", lambda codes, **k: {
        c: [{"close": 9.8, "volume": 1000}, {"close": 10.0, "volume": 1000}] for c in codes})
    monkeypatch.setattr(LT, "lq_klines", lambda code, **k: [
        {"close": 9.8, "volume": 1000}, {"close": 10.0, "volume": 1000}])
    monkeypatch.setattr(LT, "lq_quote", lambda *a, **k: {"close": 10.0})
    # 09:35 路径的档位常量（隔离线上配置；_apply_risk_budget 在 import 时已跑过）
    monkeypatch.setattr(LT, "PER_STOCK_PCT", 0.2)
    monkeypatch.setattr(LT, "MAX_NEW_BUYS", 3)
    monkeypatch.setattr(LT, "LEVERAGE_MAX", 1.5)

    def _run_with(ledger_doc, hold_volume):
        L.save_ledger(ledger_doc)
        broker = FakeBroker()
        monkeypatch.setattr(LT, "_query_account_with_retry", lambda: (
            broker, {"asset": {"asset": 100_000.0, "cash": 50_000.0},
                     "positions": [{"code": BUY_CODE, "total_volume": hold_volume}]}))
        monkeypatch.setattr(LT, "holding_rows", lambda broker, positions: [
            {"code": BUY_CODE, "name": "江西铜业", "volume": hold_volume,
             "cost": 10.0, "price": 10.0, "pnl_pct": 0.0, "day_chg": 1.0,
             "avail": hold_volume}])
        monkeypatch.setattr(LT, "load_pool_meta", lambda top=20: (
            [{"code": BUY_CODE, "name": "江西铜业"}], {"direction": "—"}, {}))
        monkeypatch.setattr(LT, "decide_with_retry", lambda prompt, agent: (
            [{"action": "buy", "code": BUY_CODE, "pct": 0.2, "reason": "加仓"}],
            "content", None, None))
        monkeypatch.setattr(LT, "wait_fill", lambda *a, **k: {
            "order_id": "B9001", "status": "submitted",
            "filled_volume": broker.orders[-1][2], "filled_price": 10.1})
        args = SimpleNamespace(execute=True, agents=AGENT, top=20, catch_up=False)
        return broker, LT._run(args)

    return SimpleNamespace(run_with=_run_with, LT=LT, AGENT=AGENT)


def _ledger(volume):
    return {"version": 1, "agents": {AGENT: {
        "virtual_cash": 100_000.0,
        "positions": {BUY_CODE: {"volume": volume, "cost_price": 10.0,
                                 "buy_ts": TS, "last_ts": TS}}}}}


def test_cap_blocks_oversized_add_on_0935_path(env, monkeypatch, capsys):
    """已有成本 3 万、权益 13 万、上限 22%（≈2.86 万）→ 再加 ~1.9 万必须被拦。"""
    monkeypatch.setattr(env.LT, "MAX_POS_PCT", 0.22)
    broker, rc = env.run_with(_ledger(3000), hold_volume=3000)

    assert rc == 0 and broker.orders == []               # 一单没下
    assert "单票集中度超限" in capsys.readouterr().out     # 拦截有人可见


def test_cap_allows_within_limit_add_on_0935_path(env, monkeypatch):
    """对照组：小仓位加仓（1 千成本，上限 22%≈2.2 万）→ 放行并真实下单。"""
    monkeypatch.setattr(env.LT, "MAX_POS_PCT", 0.22)
    broker, rc = env.run_with(_ledger(100), hold_volume=100)

    assert rc == 0
    assert [o[0] for o in broker.orders] == ["buy"]
    pos = L.load_ledger()["agents"][AGENT]["positions"][BUY_CODE]
    assert pos["volume"] > 100                            # 加仓已记账
