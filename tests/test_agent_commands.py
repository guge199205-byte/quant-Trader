"""agent_commands（QQ 用户指令闸门）单元测试：全程无网络、无真实下单。

覆盖：指令读写/损坏文件容错、halt_buy 冻结与 resume 抵消、24h 过期、
强制清仓的执行路径（T+1/跌停/在途/无持仓分支）与消费标记。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pathlib import Path

import agent_commands as ac
import live_ledger  # noqa: E402
import live_fills  # noqa: E402


@pytest.fixture
def cmds_file(monkeypatch, tmp_path):
    f = tmp_path / "commands.json"
    monkeypatch.setattr(ac, "COMMANDS_FILE", f)
    return f


def _write(cmds_file, cmds):
    cmds_file.write_text(json.dumps({"version": 1, "commands": cmds},
                                    ensure_ascii=False), encoding="utf-8")


def _cmd(**kw):
    base = {"id": "t1", "ts": "2026-09-14T15:00:00+08:00", "ts_raw": None,
            "agent": "deepseek-v4-pro", "op": "halt_buy", "code": None,
            "note": "测试指令", "active": True}
    base.update(kw)
    return base


CN = ZoneInfo("Asia/Shanghai")
# 连续竞价窗口内的固定时间（2026-09-21 周一 10:00）。审查 HIGH-1 给
# apply_forced_sells 加了时段闸门后，卖出路径测试必须传窗口内时间，否则全体
# 走 skip:非连续竞价时段 分支（跑不真路径）。
NOW = datetime(2026, 9, 21, 10, 0, tzinfo=CN)


def test_load_missing_file_returns_empty(cmds_file):
    assert ac.load_commands() == []


def test_load_corrupt_file_returns_empty(cmds_file):
    cmds_file.write_text("{broken json", encoding="utf-8")
    assert ac.load_commands() == []


def test_has_halt_buy_true_for_matching_agent(cmds_file):
    _write(cmds_file, [_cmd(agent="deepseek-v4-pro", op="halt_buy")])
    assert ac.has_halt_buy("deepseek-v4-pro") is True
    assert ac.has_halt_buy("glm-5.3-flash") is False


def test_resume_buy_cancels_halt(cmds_file):
    _write(cmds_file, [
        _cmd(id="h1", agent="deepseek-v4-pro", op="halt_buy"),
        _cmd(id="r1", agent="deepseek-v4-pro", op="resume_buy"),
    ])
    assert ac.has_halt_buy("deepseek-v4-pro") is False


def test_inactive_and_other_agent_ignored(cmds_file):
    _write(cmds_file, [
        _cmd(id="h1", agent="glm-5.3-flash", op="halt_buy", active=False),
        _cmd(id="h2", agent="other", op="halt_buy"),
    ])
    assert ac.has_halt_buy("glm-5.3-flash") is False
    assert ac.has_halt_buy("deepseek-v4-pro") is False


def test_sell_command_expired_after_24h(cmds_file):
    old = (datetime.now() - timedelta(hours=25)).timestamp()
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ", ts_raw=old)])
    assert ac.active_for("deepseek-v4-pro") == []


def test_apply_forced_sells_skips_no_position(cmds_file, monkeypatch):
    _write(cmds_file, [_cmd(id="s1", op="sell", code="999999.SZ")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {}}}})
    out = ac.apply_forced_sells(None, "deepseek-v4-pro", NOW)
    assert out == "skip:999999.SZ:无持仓"
    # 执行后 consume → 不再重复
    assert ac.active_for("deepseek-v4-pro") == []


def test_apply_forced_sells_skips_t1_today_buy(cmds_file, monkeypatch):
    today = NOW.strftime("%Y-%m-%d")
    _write(cmds_file, [_cmd(id="s1", op="sell", code="603678.SH")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {"603678.SH": {"volume": 100, "buy_ts": today + "T09:40:00+08:00"}}}}})
    out = ac.apply_forced_sells(None, "deepseek-v4-pro", NOW)
    assert out == "skip:603678.SH:今日买入T+1不可卖"


class _FakeBrokerOk:
    def get_klines(self, code, interval="daily", **kw):
        return [{"date": "2026-09-11", "close": 17.0, "open": 17.0},
                {"date": "2026-09-14", "close": 17.5, "open": 17.0}]

    def sell(self, *a, **k):
        return {"order_id": "CMD001", "status": "submitted"}


def test_apply_forced_sells_executes_and_consumes(cmds_file, monkeypatch):
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {"001312.SZ": {"volume": 800,
                                    "buy_ts": "2026-09-08T09:37:00+08:00"}}}}})
    recorded = {}

    def fake_pending(order_id, agent, code, side, vol, price, ts, **kw):
        recorded.update(order_id=order_id, agent=agent, code=code, side=side,
                        vol=vol, price=price, **kw)

    monkeypatch.setattr(live_fills, "add_pending", fake_pending)

    out = ac.apply_forced_sells(_FakeBrokerOk(), "deepseek-v4-pro", NOW)
    assert out.startswith("done:001312.SZx")
    assert recorded["order_id"] == "CMD001"
    assert recorded["side"] == "sell"
    # 2026-09-18：提交时刻不再推 QQ（成交由 reconcile 补记时推）；
    # 规则理由随在途单带上，成交通知沿用
    assert recorded["reason"].startswith("用户指令清仓")
    # 消费了 → 同一指令不再触发
    assert ac.active_for("deepseek-v4-pro") == []


def test_apply_forced_sells_skips_inflight(cmds_file, monkeypatch):
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {"001312.SZ": {"volume": 800,
                                    "buy_ts": "2026-09-08T09:37:00+08:00"}}}}})
    monkeypatch.setattr(live_fills, "inflight", lambda code, side, **k: [{"order_id": "X"}])
    out = ac.apply_forced_sells(_FakeBrokerOk(), "deepseek-v4-pro", NOW)
    assert out.startswith("skip:001312.SZ:在途卖单")


def test_apply_forced_sells_skips_limit_down(cmds_file, monkeypatch):
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {"001312.SZ": {"volume": 800,
                                    "buy_ts": "2026-09-08T09:37:00+08:00"}}}}})

    class _BrokerLimitDown:
        def get_klines(self, code, interval="daily", **kw):
            return [{"close": 19.45}, {"close": 17.5}]  # 较昨收 -10.03% 跌停

    out = ac.apply_forced_sells(_BrokerLimitDown(), "deepseek-v4-pro", NOW)
    assert out.startswith("skip:001312.SZ:跌停")


def test_apply_forced_sells_blocked_outside_auction_window(cmds_file, monkeypatch):
    """审查 HIGH-1（2026-09-21）：非连续竞价时段不下单，指令保留不静默作废。

    盘后/午休下真单会被柜台判废（09-18 600176 形态：桥回「提交成功」的假象，
    随后 fill_abort rejected）。同口径也拦在 live_llm_trade 的调用顺序上。
    """
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ")])
    monkeypatch.setattr(live_ledger, "load_ledger", lambda: {"agents": {"deepseek-v4-pro": {
        "positions": {"001312.SZ": {"volume": 800,
                                    "buy_ts": "2026-09-08T09:37:00+08:00"}}}}})
    broker = _FakeBrokerOk()
    calls = []
    monkeypatch.setattr(broker, "sell", lambda *a, **k: calls.append(a))

    out = ac.apply_forced_sells(broker, "deepseek-v4-pro",
                                datetime(2026, 9, 21, 15, 10, tzinfo=CN))
    assert out == "skip:001312.SZ:非连续竞价时段"
    assert calls == []
    assert ac.active_for("deepseek-v4-pro") != []   # 指令保留待下一交易时段


def test_list_cli_shows_status(cmds_file, capsys):
    _write(cmds_file, [_cmd(id="s1", op="sell", code="001312.SZ", note="清仓老仓")])
    import sys

    sys.argv = ["agent_commands.py", "list"]
    ac.main()
    out = capsys.readouterr().out
    assert "deepseek-v4-pro" in out and "清仓老仓" in out