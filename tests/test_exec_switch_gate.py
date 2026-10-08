"""自动执行总开关（configs/intraday_exec.json {"enabled": ...}）必须罩住所有无人值守下单路径。

2026-09-11 盘点（与 order-path 清单对账）：哨兵 live_price_watch 与整点轮受开关约束
（关掉=「只打印不真卖」/dry-run），但有三条 **cron 无人值守路径**漏了开关：

  - leverage_guard（分钟强平守护）：开关关掉后仍会真下强平卖单；
  - replay_deferred（分钟延期单重放）：只查 after_hours 键，不看 enabled，
    开关关掉后仍会重放延期卖单；
  - live_llm_trade（09:35 开盘调仓，crontab 恒定传 --execute）：命令行显式参数
    一律压过开关 → 总闸对它形同虚设。这条路径还能**买入**，是三条里最重的。

总开关的语义是「本机自动下单总闸」——关掉它就不能有任何自动路径真下单
（人工 CLI live_trade_picks 的 --execute 是操作员显式动作，不在此列）。
三条路径都照哨兵口径：只报不卖 / 保留延期单 / 降级 dry-run，等开关打开或过期作废。
人工对 live_llm_trade 的显式覆盖走 --force（工具内单测覆盖）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_exec_switch_gate.py -q
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


# ---------- 分钟强平守护 leverage_guard ----------

@pytest.fixture
def guard(monkeypatch, tmp_path, pending_file):
    """leverage_guard.main 全桩：账户/行情/账本换掉，只测开关接线。

    杠杆构造：虚拟现金 -20 万（已用 30 万 / 额度 10 万）、持仓 10000 股 @30
    → 市值 30 万、权益 10 万、1.5× 上限 15 万 → 应强平 5000 股。
    """
    import leverage_guard as L
    import live_hourly_analysis

    sold, pending = [], []

    class FakeBroker:
        def _account_query(self):
            return {"positions": [{"stock_code": CODE, "available_volume": 10000}]}

        def get_quote(self, code, date=""):
            return {"close": 30.0}

        def get_klines(self, code, interval="daily", **kw):
            return [{"close": 29.0}, {"close": 30.0}]

        def sell(self, sig, date, code, vol, price=None, plan_id=None):
            sold.append({"code": code, "volume": vol, "price": price})
            return {"order_id": "T7001", "status": "submitted"}

    import agent_tools.brokers.tdx_bridge as tb

    ledger = {"agents": {AGENT: {"virtual_cash": -200000.0,
                                 "positions": {CODE: {"volume": 10000,
                                                      "cost_price": 10.0}}}}}
    monkeypatch.setattr(tb, "TdxBridgeBroker", FakeBroker)
    monkeypatch.setattr(live_hourly_analysis, "market_data_stale", lambda broker: False)
    # LEVERAGE_MAX 会被 configs/risk_budget.json 在 import 时覆盖（2026-09-18 实录：
    # 档位改 1.2× 后本用例按 1.5× 的算术断言全错）——用例口径写死 1.5，与生产档位解耦。
    monkeypatch.setattr(live_hourly_analysis, "LEVERAGE_MAX", 1.5)
    monkeypatch.setattr(L, "LEVERAGE_MAX", 1.5)
    monkeypatch.setattr(L, "now_cn", lambda: NOW)
    monkeypatch.setattr(L, "in_trading_window", lambda now: True)
    monkeypatch.setattr(L, "load_ledger", lambda: ledger)
    monkeypatch.setattr(L, "add_pending", lambda *a, **kw: pending.append(a))
    return types.SimpleNamespace(module=L, sold=sold, pending=pending)


def test_guard_switch_off_reports_but_does_not_sell(guard, monkeypatch, capsys):
    """总开关关 → 强平守护只报不卖（与哨兵同口径），也不挂 pending。"""
    monkeypatch.setattr(guard.module, "intraday_exec_enabled", lambda: False)

    assert guard.module.main() == 0

    assert guard.sold == []
    assert guard.pending == []
    out = capsys.readouterr().out
    assert "自动执行开关已关" in out and "只报不卖" in out


def test_guard_switch_on_sells(guard, monkeypatch):
    """对照组：开关开 → 正常强平（5000 股 @ 现价 -2%），成交进 pending 待对账。"""
    monkeypatch.setattr(guard.module, "intraday_exec_enabled", lambda: True)

    assert guard.module.main() == 0

    assert len(guard.sold) == 1
    assert guard.sold[0]["code"] == CODE and guard.sold[0]["volume"] == 5000
    assert guard.sold[0]["price"] == pytest.approx(29.4)      # 30 × 0.98
    assert guard.pending and guard.pending[0][1] == AGENT


# ---------- 分钟延期单重放 replay_deferred ----------

@pytest.fixture
def replay(monkeypatch, tmp_path, pending_file):
    """replay_deferred.main 全桩：桥/账本/在途单换掉，只测开关接线。"""
    import replay_deferred as R
    import live_hourly_analysis

    sold, saved = [], []

    class FakeBroker:
        def _account_query(self):
            return {"positions": [{"stock_code": CODE, "available_volume": 500}]}

        def get_klines(self, code, interval="daily", **kw):
            return [{"close": 17.22}, {"close": 17.50}]

        def sell(self, sig, date, code, vol, price=None, plan_id=None):
            sold.append({"code": code, "volume": vol, "price": price})
            return {"order_id": "T9001", "status": "submitted"}

    import agent_tools.brokers.tdx_bridge as tb

    entry = {"agent": AGENT, "code": CODE, "side": "sell", "volume": 100,
             "reason": "测试", "ts": NOW.isoformat()}
    monkeypatch.setattr(tb, "TdxBridgeBroker", FakeBroker)
    monkeypatch.setattr(R, "now_cn", lambda: NOW)
    monkeypatch.setattr(R, "in_trading_window", lambda now: True)
    monkeypatch.setattr(R, "after_hours_window", lambda now: False)
    monkeypatch.setattr(live_hourly_analysis, "market_data_stale", lambda broker: False)
    monkeypatch.setattr(R, "load_ledger", lambda: {"deferred": [dict(entry)]})
    monkeypatch.setattr(R, "load_deferred", lambda ledger: [dict(entry)])
    monkeypatch.setattr(R, "add_pending", lambda *a, **kw: None)
    monkeypatch.setattr(R, "clear_deferred", lambda ledger, *a: dict(ledger))
    monkeypatch.setattr(R, "save_ledger", lambda ledger: saved.append(ledger))
    return types.SimpleNamespace(module=R, sold=sold, saved=saved, entry=entry)


def test_replay_switch_off_keeps_entry_without_selling(replay, monkeypatch, capsys):
    """总开关关 → 延期卖单保留不重放（不真下单，也不清延期）。"""
    monkeypatch.setattr(replay.module, "intraday_exec_enabled", lambda: False)

    assert replay.module.main() == 0

    assert replay.sold == []
    assert replay.saved == []          # 延期原样保留，无需回写
    out = capsys.readouterr().out
    assert "自动执行开关已关" in out and "保留" in out


def test_replay_switch_on_replays(replay, monkeypatch, capsys):
    """对照组：开关开 → 照常重放（护栏不误伤正常链路）。"""
    monkeypatch.setattr(replay.module, "intraday_exec_enabled", lambda: True)

    assert replay.module.main() == 0

    assert len(replay.sold) == 1
    assert replay.sold[0]["code"] == CODE and replay.sold[0]["volume"] == 100
    assert replay.saved                     # 重放成功 → 清延期回写账本
    assert "自动执行开关已关" not in capsys.readouterr().out


# ---------- 09:35 开盘调仓 live_llm_trade（cron 恒传 --execute）----------

@pytest.fixture
def llm_state(monkeypatch, tmp_path):
    """state 文件隔离到 tmp：真实 logs/live_llm_trade_state.json 是 10:00「主入口
    未收尾」告警与日报的判据，测试写入会篡改当日真实执行记录。"""
    import live_llm_trade as L

    monkeypatch.setattr(L, "STATE_FILE", tmp_path / "llm_state.json")
    return tmp_path / "llm_state.json"


def test_llm_trade_switch_off_downgrades_execute(llm_state, monkeypatch, capsys):
    """总开关关 → --execute 降级为 dry-run（09:35 的买入路径不再下单）。"""
    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)
    args = types.SimpleNamespace(execute=True, force=False)

    assert L._apply_exec_switch(args) is True
    assert args.execute is False
    assert "降级为 dry-run" in capsys.readouterr().out


def test_llm_trade_switch_off_writes_state_note(llm_state, monkeypatch):
    """降级同日落 state.note 标记：alert_checks 据此不报「主入口未收尾」误报；
    且 ok≠True、orders_attempted 非真 → 总闸恢复后当日 catch-up 仍能补跑。"""
    import json

    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)
    args = types.SimpleNamespace(execute=True, force=False)

    assert L._apply_exec_switch(args) is True

    st = json.loads(llm_state.read_text(encoding="utf-8"))
    assert st["note"] == L.EXEC_SWITCH_OFF_NOTE
    assert st["ok"] is None and not st.get("orders_attempted")


def test_llm_trade_switch_off_does_not_mask_same_day_failure(llm_state, monkeypatch, capsys):
    """当日已有真执行记录（下过单/失败过）时，关闸的 note 不得覆盖它。

    2026-09-11 审查 MEDIUM：09:35 真跑过并失败（ok=False、orders_attempted=True），
    10:05 补跑时若总闸被关，`_apply_exec_switch` 会把 state 改写成
    note=exec_switch_off → alert_checks 认定为「预期行为」静默 → 当天那次真失败
    从告警面上消失（t1/t2 都不报）。
    """
    import json

    import live_hourly_analysis
    import live_llm_trade as L

    day = L.now_cn().date().isoformat()
    llm_state.write_text(json.dumps({"day": day, "ok": False, "orders_attempted": True,
                                     "note": "crashed: bridge_account_query"}),
                         encoding="utf-8")
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)
    args = types.SimpleNamespace(execute=True, force=False)

    assert L._apply_exec_switch(args) is True     # 仍然降级 dry-run（不真下单）

    st = json.loads(llm_state.read_text(encoding="utf-8"))
    assert st["ok"] is False and st["note"] == "crashed: bridge_account_query"
    assert st["orders_attempted"] is True
    assert "保留" in capsys.readouterr().out


def test_llm_trade_switch_off_writes_note_when_no_real_run_yet(llm_state, monkeypatch):
    """对照组：当天还没真跑过（老记录是别日的/dry-run 残留）→ 照常写标记。"""
    import json

    import live_hourly_analysis
    import live_llm_trade as L

    llm_state.write_text(json.dumps({"day": "2026-09-08", "ok": False,
                                     "orders_attempted": True}), encoding="utf-8")
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)

    assert L._apply_exec_switch(types.SimpleNamespace(execute=True, force=False)) is True

    st = json.loads(llm_state.read_text(encoding="utf-8"))
    assert st["note"] == L.EXEC_SWITCH_OFF_NOTE and st["ok"] is None
    assert not st.get("orders_attempted")


def test_llm_trade_switch_on_keeps_execute(llm_state, monkeypatch, capsys):
    """对照组：开关开 → --execute 原样保留，不打字也不落状态。"""
    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)
    args = types.SimpleNamespace(execute=True, force=False)

    assert L._apply_exec_switch(args) is False
    assert args.execute is True
    assert capsys.readouterr().out == ""
    assert not llm_state.exists()


def test_llm_trade_force_overrides_switch(monkeypatch):
    """人工 --force 显式覆盖总闸（仅手动；crontab 不得使用）。"""
    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)
    args = types.SimpleNamespace(execute=True, force=True)

    assert L._apply_exec_switch(args) is False
    assert args.execute is True


def test_llm_trade_dry_run_untouched(monkeypatch, capsys):
    """本来就没 --execute（dry-run 演练）→ 助手不碰也不打字。"""
    import live_llm_trade as L

    args = types.SimpleNamespace(execute=False, force=False)

    assert L._apply_exec_switch(args) is False
    assert args.execute is False
    assert capsys.readouterr().out == ""
