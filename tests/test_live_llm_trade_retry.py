"""09:35 主入口断线重试 + 当日幂等补跑的回归测试。

2026-09-07 / 09-09 实录：`acct = broker._account_query()` 无重试、无兜底，
桥断线（No route to host / Connection refused）直接抛 BrokerError 裸崩退出，
当天 0 笔调仓、日志里只有 traceback、零告警。

这里测的是接线：
  1. `_query_account_with_retry` 每轮新建 broker（桥址自动发现每实例只做一次），
     抛异常**或**资产<=0 都算失败；
  2. `--catch-up` 只在「今天没成功 且 一单都没动过」时才补跑；
  3. 状态合并写不得抹掉 `orders_attempted`（否则补跑会重复下单）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_llm_trade_retry.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_llm_trade as L  # noqa: E402
from agent_tools.brokers.base import BrokerError  # noqa: E402

DAY = "2026-09-09"
NOON = datetime(2026, 9, 9, 10, 5, tzinfo=L.CN_TZ)


# ---------- 查询重试 ----------

def test_query_retry_succeeds_on_second_attempt(monkeypatch):
    """第一次断线、第二次成功：每轮新建 broker，不靠复用实例。"""
    built = []

    class FakeBroker:
        def __init__(self):
            built.append(self)

        def _account_query(self):
            if len(built) == 1:
                raise RuntimeError("Connection refused")
            return {"asset": {"asset": 100_000, "cash": 5_000}, "positions": []}

    monkeypatch.setattr(L, "TdxBridgeBroker", FakeBroker)

    broker, acct = L._query_account_with_retry(attempts=3, sleep=0)

    assert acct["asset"]["asset"] == 100_000
    assert broker is built[-1]
    assert len(built) == 2          # 第二次是新建实例 → 桥址自动发现有机会重跑


def test_query_retry_raises_after_all_attempts(monkeypatch):
    built = []

    class DeadBroker:
        def __init__(self):
            built.append(self)

        def _account_query(self):
            raise RuntimeError("No route to host")

    monkeypatch.setattr(L, "TdxBridgeBroker", DeadBroker)

    with pytest.raises(BrokerError):
        L._query_account_with_retry(attempts=3, sleep=0)

    assert len(built) == 3


def test_query_retry_treats_zero_asset_as_failure(monkeypatch):
    """桥假活：不抛异常但资产为 0（2026-09-04 实录）→ 也要重试。"""
    seq = [{"asset": {"asset": 0}}, {"asset": {"asset": 0}},
           {"asset": {"asset": 123_456}}]

    class HalfDeadBroker:
        def _account_query(self):
            return seq.pop(0)

    monkeypatch.setattr(L, "TdxBridgeBroker", HalfDeadBroker)

    _broker, acct = L._query_account_with_retry(attempts=3, sleep=0)

    assert acct["asset"]["asset"] == 123_456


# ---------- 状态文件合并 ----------

def test_write_state_preserves_orders_attempted(monkeypatch, tmp_path):
    """崩溃收尾写的 ok:false 不得抹掉 orders_attempted（否则补跑会重复下单）。"""
    monkeypatch.setattr(L, "STATE_FILE", tmp_path / "state.json")

    L._write_state(DAY, started_ts="2026-09-09T10:05:00+08:00",
                   orders_attempted=True, ok=None, note="")
    L._write_state(DAY, ok=False, note="crashed: boom")

    d = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert d["orders_attempted"] is True
    assert d["ok"] is False and d["note"] == "crashed: boom"


# ---------- --catch-up 闸门 ----------

@pytest.fixture
def catchup(monkeypatch, tmp_path):
    """把交易日/交易时段/锁/状态文件全换成可控桩，记录 broker 是否被建。"""
    monkeypatch.setattr(L, "LOCK_FILE", tmp_path / "live_llm_trade.lock")
    monkeypatch.setattr(L, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(L, "now_cn", lambda: NOON)

    import trading_cal

    monkeypatch.setattr(trading_cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(trading_cal, "why_not", lambda d: "测试日")

    import live_hourly_analysis

    monkeypatch.setattr(live_hourly_analysis, "in_trading_window", lambda now: True)

    built = []

    class NoBroker:
        def __init__(self):
            built.append("broker")

    monkeypatch.setattr(L, "TdxBridgeBroker", NoBroker)
    return built


def _run_main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["live_llm_trade.py", *argv])
    return L.main()


def test_catchup_skips_when_day_already_ok(catchup, monkeypatch, capsys):
    L.STATE_FILE.write_text(json.dumps({"day": DAY, "ok": True, "note": "acct2_failed"}),
                            encoding="utf-8")

    assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 0

    assert catchup == []                     # 不建 broker
    assert "已成功完成" in capsys.readouterr().out


def test_catchup_skips_when_orders_attempted(catchup, monkeypatch, capsys):
    """动过单就不补跑——重复下单比不调仓更糟。"""
    L.STATE_FILE.write_text(
        json.dumps({"day": DAY, "ok": False, "orders_attempted": True}),
        encoding="utf-8")

    assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 0

    assert catchup == []
    assert "可能重复下单" in capsys.readouterr().out


def test_catchup_skips_outside_trading_window(catchup, monkeypatch, capsys):
    import live_hourly_analysis

    monkeypatch.setattr(live_hourly_analysis, "in_trading_window", lambda now: False)

    assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 0

    assert catchup == []
    assert "不在 A股交易时段" in capsys.readouterr().out


def test_catchup_skips_when_lock_held(catchup, monkeypatch, capsys):
    import fcntl

    fh = open(L.LOCK_FILE, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 模拟另一班正在跑
        assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 0
    finally:
        fh.close()

    assert catchup == []
    assert "已有另一班在跑" in capsys.readouterr().out


def test_catchup_requires_execute(catchup, monkeypatch, capsys):
    assert _run_main(monkeypatch, ["--catch-up"]) == 0

    assert catchup == []
    assert "必须配合 --execute" in capsys.readouterr().out


def test_catchup_proceeds_after_preorder_failure(catchup, monkeypatch):
    """下单前失败（一单没动）→ 允许补跑，进入正常执行路径。"""
    L.STATE_FILE.write_text(
        json.dumps({"day": DAY, "ok": False, "orders_attempted": False,
                    "note": "bridge_account_query"}),
        encoding="utf-8")
    monkeypatch.setattr(L, "_run", lambda args: 42)

    assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 42


def test_catchup_proceeds_when_no_state_for_today(catchup, monkeypatch):
    """当日无状态（09:35 没跑起来）→ 补跑。"""
    L.STATE_FILE.write_text(json.dumps({"day": "2026-09-08", "ok": True}),
                            encoding="utf-8")
    monkeypatch.setattr(L, "_run", lambda args: 7)

    assert _run_main(monkeypatch, ["--execute", "--catch-up"]) == 7
