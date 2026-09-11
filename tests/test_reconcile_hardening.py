"""reconcile 的两处生产化加固（2026-09-11 order-path 盘点 ①3 / ③3）。

1) 跨进程互斥：哨兵、整点轮、每分钟 --record-only 采样都由 cron 在**整分钟边界**
   触发，reconcile 是「读 pending → 算成交增量 → 写账本 → 写回 pending」的
   读-改-写。没有互斥时两个实例基于同一份旧 volume_recorded 各补记一次 →
   同一笔成交在分账账本上翻倍（账本虚增成交量/现金，之后所有额度与闸门全错）。

2) 口径漂移：live_fills 模块 docstring 一直写「调度入口（整点/哨兵/record-only）
   开头调 reconcile」，实际 record-only 段（每分钟跑）从没调过 → 在途单最长压到
   下一整点才补记。这里把 wiring 也钉住：record-only 必须真的把成交补进账本。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_reconcile_hardening.py -q
"""
import fcntl
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills  # noqa: E402

CN = live_fills.CN_TZ
NOW = datetime(2026, 9, 11, 10, 30, 0, tzinfo=CN)
CODE = "001312.SZ"
AGENT = "deepseek-v4-pro"


def _pending_entry():
    return {"order_id": "T1001", "agent": AGENT, "code": CODE, "side": "sell",
            "volume": 100, "price": 17.0, "volume_recorded": 0, "ts": NOW.isoformat()}


# ---------- 1) 跨进程互斥 ----------

def test_reconcile_skips_while_another_process_holds_the_lock(monkeypatch, tmp_path):
    """锁被占（另一个 cron 实例正在对账）→ 本轮直接跳过，连桥都不查。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")
    lock = tmp_path / "reconcile.lock"
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", lock)

    calls = []

    class Broker:
        def get_orders(self):
            calls.append(1)
            return []

    holder = open(lock, "a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert live_fills.reconcile(Broker(), now=NOW) == 0
        assert calls == []                       # 锁被占：不读桥、不写账本
    finally:
        holder.close()
    assert live_fills.reconcile(Broker(), now=NOW) == 0
    assert calls == [1]                          # 锁释放后可进入


# ---------- 2) record-only 每分钟兜底确实在调 reconcile ----------

def test_record_only_path_books_fills(monkeypatch, tmp_path):
    """在途单成交 → --record-only 跑一轮就补进账本（不再压到下一整点）。"""
    import agent_tools.brokers.tdx_bridge as tb
    import live_hourly_analysis as H
    import live_ledger
    import live_trade_picks

    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text(json.dumps({"agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {CODE: {"volume": 500, "cost_price": 16.0}}}}}),
        encoding="utf-8")
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", ledger_file)

    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "reconcile.lock")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")

    logged = []
    # log_line 默认落 logs/live_trade_*.jsonl —— 那是生产告警面（alert.sh 按它判
    # 「最近一笔成功成交」），测试写入会掩盖真实停滞，一律换桩
    monkeypatch.setattr(live_trade_picks, "log_line", logged.append)

    class Broker:
        def _account_query(self):
            return {"asset": {"asset": 200000.0, "cash": 50000.0},
                    "positions": [{"stock_code": CODE, "total_volume": 500,
                                   "available_volume": 500}]}

        def get_orders(self):
            return [{"order_id": "T1001", "status": "filled", "filled_volume": 100,
                     "filled_price": 17.5, "order_price": 17.0, "total_volume": 100,
                     "stock_code": CODE}]

    monkeypatch.setattr(tb, "TdxBridgeBroker", Broker)
    monkeypatch.setattr(H, "now_cn", lambda: NOW)
    monkeypatch.setattr(H, "in_trading_window", lambda now: True)
    monkeypatch.setattr(H, "record_window", lambda now: True)
    monkeypatch.setattr(H, "record_equity", lambda *a, **kw: None)
    monkeypatch.setattr(H, "load_state", lambda: {})
    monkeypatch.setattr(H, "last_good_sample_ts", lambda st: NOW.isoformat())
    monkeypatch.setattr(H, "missed_sample_minutes", lambda last, now: 0)
    monkeypatch.setattr(H, "save_state", lambda st: None)
    monkeypatch.setattr(H, "check_volatility", lambda broker, positions: None)
    monkeypatch.setattr(sys, "argv", ["live_hourly_analysis.py", "--record-only"])

    assert H.main() == 0

    pos = json.loads(ledger_file.read_text(encoding="utf-8"))["agents"][AGENT]["positions"][CODE]
    assert pos["volume"] == 400                  # 卖出成交 100 股已补记
    assert [r for r in logged if r.get("mode") == "fill_confirm"]
