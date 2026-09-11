"""在途委托闸门：同一代码已有未确认委托时，任何执行路径都不许再下一单。

2026-09-11 实录的两处真实竞态（量化生产口径，与 quantmind QMT 侧同源）：
  1) 09:35 主入口的卖出 wait_fill 超时 → 挂 pending；整点轮/哨兵若只看
     「自己的调仓意图」不看 pending，就会对同一代码再下第二笔卖单；
  2) LLM 决策耗时以分钟计，期间分钟哨兵可能刚止损卖出同一代码 ——
     决策时刻的持仓快照已过期，执行前必须重新读在途集。

口径统一在 live_fills.inflight_codes（一次读盘，避免逐条判定放大磁盘 IO）：
  - live_hourly_analysis.execute_intraday_decision：校验时 + 执行前各刷一次
  - live_llm_trade._run：决策校验时 + reconcile 之后各刷一次
  - replay_deferred.main：重放前一次
  - live_price_watch._execute_sell：单条 inflight(code, "sell")

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_inflight_gate.py -q
"""
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills  # noqa: E402

CN = live_fills.CN_TZ
CODE = "001312.SZ"
AGENT = "deepseek-v4-pro"
NOW = datetime(2026, 9, 11, 10, 30, 0, tzinfo=CN)


@pytest.fixture
def pending_file(tmp_path, monkeypatch):
    """在途单文件隔离到 tmp（真实 data/ 是生产告警面，测试不得触碰）。"""
    p = tmp_path / "pending.json"
    monkeypatch.setattr(live_fills, "PENDING_FILE", p)
    return p


def _write_pending(path: Path, *entries) -> None:
    path.write_text(json.dumps(list(entries), ensure_ascii=False), encoding="utf-8")


# ---------- 口径本身 ----------

def test_inflight_codes_splits_by_side(pending_file):
    _write_pending(pending_file,
                   {"code": CODE, "side": "sell", "order_id": "T1"},
                   {"code": "600309.SH", "side": "buy", "order_id": "T2"},
                   {"code": "600362.SH", "side": "sell", "order_id": "T3"})

    assert live_fills.inflight_codes("sell") == {CODE, "600362.SH"}
    assert live_fills.inflight_codes("buy") == {"600309.SH"}
    assert live_fills.inflight_codes() == {CODE, "600309.SH", "600362.SH"}
    assert live_fills.inflight(CODE, "sell")[0]["order_id"] == "T1"
    assert live_fills.inflight(CODE, "buy") == []


def test_inflight_codes_empty_when_no_pending(pending_file):
    assert live_fills.inflight_codes("sell") == set()


# ---------- 延期单重放（cron 每分钟）----------

@pytest.fixture
def replay(monkeypatch, tmp_path, pending_file):
    """replay_deferred.main 全链路桩：桥/账本/在途单文件都换掉，只测闸门接线。"""
    import replay_deferred as R

    sold = []
    saved = []

    class FakeBroker:
        def _account_query(self):
            return {"positions": [{"stock_code": CODE, "available_volume": 500}]}

        def get_klines(self, code, interval="daily", **kw):
            return [{"close": 17.22}, {"close": 17.50}]

        def sell(self, sig, date, code, vol, price=None, plan_id=None):
            sold.append({"code": code, "volume": vol, "price": price})
            return {"order_id": "T9001", "status": "submitted"}

    import agent_tools.brokers.tdx_bridge as tb
    import live_hourly_analysis

    monkeypatch.setattr(tb, "TdxBridgeBroker", FakeBroker)
    monkeypatch.setattr(R, "now_cn", lambda: NOW)
    monkeypatch.setattr(R, "in_trading_window", lambda now: True)
    monkeypatch.setattr(R, "after_hours_window", lambda now: False)
    monkeypatch.setattr(live_hourly_analysis, "market_data_stale", lambda broker: False)

    deferred_entry = {"agent": AGENT, "code": CODE, "side": "sell", "volume": 100,
                      "reason": "测试", "ts": NOW.isoformat()}
    monkeypatch.setattr(R, "load_ledger", lambda: {"deferred": [dict(deferred_entry)]})
    monkeypatch.setattr(R, "load_deferred", lambda ledger: [dict(deferred_entry)])
    monkeypatch.setattr(R, "add_pending", lambda *a, **kw: None)
    monkeypatch.setattr(R, "clear_deferred", lambda ledger, *a: dict(ledger))
    monkeypatch.setattr(R, "save_ledger", lambda ledger: saved.append(ledger))
    return types.SimpleNamespace(module=R, sold=sold, saved=saved,
                                 entry=deferred_entry)


def test_replay_deferred_keeps_entry_when_inflight_sell(replay, pending_file, capsys):
    """同代码已有在途卖单（整点轮/哨兵刚下的）→ 延期单原样保留，不重复下单。"""
    _write_pending(pending_file, {"code": CODE, "side": "sell", "order_id": "T1001",
                                  "agent": AGENT, "volume": 100})

    assert replay.module.main() == 0

    assert replay.sold == []                      # 关键：桥上一单没发
    assert "已有在途卖单未确认，保留延期" in capsys.readouterr().out


def test_replay_deferred_replays_when_no_inflight(replay, pending_file, capsys):
    """对照组：无在途卖单 → 照常重放（护栏不误伤正常链路）。"""
    assert replay.module.main() == 0

    assert len(replay.sold) == 1
    assert replay.sold[0]["code"] == CODE and replay.sold[0]["volume"] == 100


def test_replay_deferred_inflight_other_side_does_not_block(replay, pending_file):
    """在途的是买单，不拦卖单（方向不同不构成重复下单）。"""
    _write_pending(pending_file, {"code": CODE, "side": "buy", "order_id": "T1002"})

    assert replay.module.main() == 0

    assert len(replay.sold) == 1


# ---------- 09:35 主入口（LLM 决策）----------

@pytest.fixture
def llm_trade(monkeypatch, tmp_path, pending_file):
    """live_llm_trade._run 的 dry-run 全桩：决策校验段不碰网络、不落真日志。"""
    import live_breaker
    import live_hourly_analysis
    import live_prompt_context
    import symbol_policy
    import live_llm_trade as L

    positions = [{"stock_code": CODE, "total_volume": 100, "available_volume": 100,
                  "cost_price": 16.0, "last_price": 17.5}]
    holding = {"code": CODE, "name": "福恩股份", "volume": 100, "cost": 16.0,
               "price": 17.5, "pnl_pct": 9.4, "day_chg": 1.6, "avail": 100}
    ledger = {"agents": {AGENT: {"positions": {CODE: {"volume": 100, "cost_price": 16.0}}}}}

    class FakeBroker:
        def _account_query(self):
            return {"asset": {"asset": 200000.0, "cash": 50000.0},
                    "positions": positions}

        def sell(self, *a, **kw):
            raise AssertionError("dry-run/闸门命中时不该下单")

    monkeypatch.setattr(L, "_query_account_with_retry",
                        lambda *a, **kw: (FakeBroker(), FakeBroker()._account_query()))
    monkeypatch.setattr(L, "holding_rows", lambda broker, pos: [dict(holding)])
    monkeypatch.setattr(L, "load_pool", lambda top=20: ([{"code": CODE, "name": "福恩股份"}], {}))
    monkeypatch.setattr(L, "load_ledger", lambda: json.loads(json.dumps(ledger)))
    monkeypatch.setattr(L, "agent_remaining", lambda ledger, agent: 100000.0)
    monkeypatch.setattr(L, "agent_virtual_cash", lambda ledger, agent: 100000.0)
    monkeypatch.setattr(L, "build_prompt", lambda *a, **kw: "PROMPT")
    monkeypatch.setattr(L, "parse_decision", lambda text: [
        {"action": "sell", "code": CODE, "pct": 0.5, "reason": "止盈"}])
    monkeypatch.setattr(live_breaker, "check_and_trip", lambda agent, persist=True: (False, ""))
    monkeypatch.setattr(symbol_policy, "load_policy_with_risk", lambda: None)
    monkeypatch.setattr(live_prompt_context, "filter_affordable",
                        lambda pool, budget: (pool, []))
    monkeypatch.setattr(live_hourly_analysis, "call_llm", lambda prompt, agent: ("RAW", None))
    monkeypatch.setattr(live_hourly_analysis, "daily_buy_codes", lambda agent, *a, **kw: set())
    monkeypatch.setattr(live_hourly_analysis, "append_log",
                        lambda *a, **kw: tmp_path / "chat.log")
    monkeypatch.setattr(live_fills, "reconcile", lambda broker: 0)
    return L


def test_llm_trade_dry_run_skips_inflight_sell(llm_trade, pending_file, capsys):
    """在途卖单未确认 → 同一代码的卖出决策在校验段就被跳过（不再下单/不再报单）。"""
    _write_pending(pending_file, {"code": CODE, "side": "sell", "order_id": "T1001",
                                  "agent": AGENT, "volume": 500})

    args = types.SimpleNamespace(execute=False, agents=AGENT, top=20)

    assert llm_trade._run(args) == 0

    assert "已有在途卖单未确认，跳过" in capsys.readouterr().out


def test_llm_trade_dry_run_passes_without_inflight(llm_trade, pending_file, capsys):
    """对照组：无在途卖单 → 决策正常进入卖出清单（dry-run 不真下单）。"""
    args = types.SimpleNamespace(execute=False, agents=AGENT, top=20)

    assert llm_trade._run(args) == 0

    out = capsys.readouterr().out
    assert "已有在途卖单未确认" not in out
    assert "📉" in out
