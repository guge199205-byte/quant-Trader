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


@pytest.fixture(autouse=True)
def _isolate_production_logs(monkeypatch, tmp_path):
    """成交流水必须落 tmp：reconcile 的 log_line 默认写真实
    logs/live_trade_YYYYMMDD.jsonl（2026-09-11 实录：pytest 跑一轮就把合成
    fill_confirm/fill_expire 混进当日生产流水，fill_recorded 还会据此误判）。

    账本/委托/事件/锁的隔离已由 tests/conftest.py 全局兜底（同日实录：只隔离
    日志、漏了 live_ledger.LEDGER_FILE → 每跑一次扣真实持仓 100 股），这里只
    保留本模块最直接的断言面。"""
    import live_trade_picks

    monkeypatch.setattr(live_trade_picks, "LOG_DIR", tmp_path / "logs")
    # 正例：本模块多处直接调 reconcile，显式再钉一遍账本路径
    import live_ledger

    monkeypatch.setattr(live_ledger, "LEDGER_FILE", tmp_path / "live_ledger.json")


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
    monkeypatch.setattr(H, "update_state", lambda *a, **kw: {})
    monkeypatch.setattr(H, "check_volatility", lambda broker, positions: None)
    monkeypatch.setattr(sys, "argv", ["live_hourly_analysis.py", "--record-only"])

    assert H.main() == 0

    pos = json.loads(ledger_file.read_text(encoding="utf-8"))["agents"][AGENT]["positions"][CODE]
    assert pos["volume"] == 400                  # 卖出成交 100 股已补记
    assert [r for r in logged if r.get("mode") == "fill_confirm"]


# ---------- 3) 写者互斥：对账的整表回写不能吞掉网络窗口里的新增（审查 HIGH B）----------
# reconcile 是「读 pending → 查桥（网络往返）→ 整表写回」；add_pending / record_event
# 是不持锁的读-改-写。哨兵在那一两秒的网络窗口里刚下完单、正要挂 pending，回写用
# 的是**查桥之前**的旧快照 → 新单被整表覆盖冲掉：成交从此不进账本（账外单），
# 且它连在途表都不在，inflight 闸门也拦不住下一笔重复卖出。

def test_add_pending_in_network_window_survives_writeback(monkeypatch, tmp_path):
    """对账查桥期间哨兵挂上的新在途单，必须活到对账结束之后。"""
    import threading
    import time

    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "reconcile.lock")

    worker = {}

    class Broker:
        def get_orders(self):
            # 网络往返窗口：另一个进程（哨兵）刚下完单、正在挂 pending
            worker["t"] = threading.Thread(target=live_fills.add_pending, args=(
                "T2002", AGENT, "600309.SH", "sell", 100, 15.0, NOW.isoformat()))
            worker["t"].start()
            time.sleep(0.2)          # 给 add_pending 起跑的时间（旧代码此刻落盘）
            return []                # 桥查不到 T1001（当日未成交）

    live_fills.reconcile(Broker(), now=NOW)
    worker["t"].join(5)

    ids = sorted(p["order_id"] for p in json.loads(pending.read_text(encoding="utf-8")))
    assert ids == ["T1001", "T2002"]       # 旧的在途单保留 + 窗口里新增的没被冲掉


def test_add_pending_waits_while_another_process_reconciles(monkeypatch, tmp_path):
    """锁被别的进程占着时 add_pending 必须等——不能"先写进去再被整表覆盖"。"""
    import threading

    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    lock = tmp_path / "reconcile.lock"
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", lock)

    holder = open(lock, "a+")
    fcntl.flock(holder, fcntl.LOCK_EX)
    t = threading.Thread(target=live_fills.add_pending, args=(
        "T3003", AGENT, CODE, "sell", 100, 17.0, NOW.isoformat()))
    try:
        t.start()
        t.join(0.5)
        assert t.is_alive()                # 还在等锁：没有抢先落盘
        assert not (tmp_path / "pending.json").exists()
    finally:
        holder.close()                     # 释放 → 新单落盘
    t.join(5)
    assert not t.is_alive()
    assert [p["order_id"] for p in live_fills.load_pending()] == ["T3003"]


def test_record_event_waits_while_another_process_reconciles(monkeypatch, tmp_path):
    """事件同理：持锁期间的写必须排队，否则会与对账的事件读-改-写互相覆盖。"""
    import threading

    events = tmp_path / "events.json"
    monkeypatch.setattr(live_fills, "EVENTS_FILE", events)
    lock = tmp_path / "reconcile.lock"
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", lock)

    holder = open(lock, "a+")
    fcntl.flock(holder, fcntl.LOCK_EX)
    t = threading.Thread(target=live_fills.record_event,
                         args=("unfilled", CODE, "对账写者"), kwargs={"now": NOW})
    try:
        t.start()
        t.join(0.5)
        assert t.is_alive() and not events.exists()
    finally:
        holder.close()
    t.join(5)
    assert not t.is_alive()
    assert json.loads(events.read_text(encoding="utf-8"))


# ---------- 4) 锁文件不可用：区分「抢占」与「打不开」（审查 MEDIUM C）----------
# 老实现把两者混在一个 OSError 里：磁盘/权限问题导致锁文件建不出来时，reconcile
# 静默 yield False → **每轮都跳过对账**（成交永远不补记），而 docstring 写的却是
# 「退回无锁旧行为」。抢占（别人正在对账）才该跳过；打不开要按无锁继续并告警。

def test_reconcile_proceeds_lockless_when_lock_file_unusable(monkeypatch, tmp_path, capsys):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "lockdir")
    (tmp_path / "lockdir").mkdir()          # open(目录, "a+") → IsADirectoryError

    calls = []

    class Broker:
        def get_orders(self):
            calls.append(1)
            return [{"order_id": "T1001", "status": "filled", "filled_volume": 100,
                     "filled_price": 17.5, "total_volume": 100, "stock_code": CODE}]

    assert live_fills.reconcile(Broker(), now=NOW) == 1
    assert calls == [1]                     # 锁建不出来也要照常对账
    assert live_fills.load_pending() == []  # 终态：补记后移除
    assert "锁文件" in capsys.readouterr().err


# ---------- 5) 隔夜过期单：未记账的必须告警（order-path 盘点 ③ 残留）----------
# 桥的委托接口不保留历史：昨天挂的 pending 今天查不到。老实现只写一行
# fill_expire 日志就把它删了——若那笔单昨天其实成交了（进程在收盘前掉线，
# 没人观察到终态），成交就成了**账外单**：账本与账户永久不一致且零告警。

def test_expired_pending_with_unrecorded_volume_raises_alerting_event(monkeypatch, tmp_path):
    pending = tmp_path / "pending.json"
    entry = _pending_entry()
    entry["ts"] = "2026-09-10T14:30:00+08:00"          # 昨天的单
    pending.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "reconcile.lock")
    events = tmp_path / "events.json"
    monkeypatch.setattr(live_fills, "EVENTS_FILE", events)

    class Broker:
        def get_orders(self):
            return []                                   # 昨日委托桥已不保留

    assert live_fills.reconcile(Broker(), now=NOW) == 0
    assert live_fills.load_pending() == []              # 过期清理照旧
    ev = json.loads(events.read_text(encoding="utf-8"))[f"fill_expire:{CODE}:2026-09-11"]
    assert ev["alert"] is True                          # 未记账 → alert.sh 必须打扰人
    assert "未记账" in ev["msg"] and "核对" in ev["msg"]


def test_expired_pending_fully_recorded_stays_silent(monkeypatch, tmp_path):
    """成交已全部记账的过期单只是清理：留痕但不告警（防天天误报训练麻木）。"""
    pending = tmp_path / "pending.json"
    entry = _pending_entry()
    entry["ts"] = "2026-09-10T14:30:00+08:00"
    entry["volume_recorded"] = entry["volume"]          # 100/100 已记账
    pending.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "reconcile.lock")
    events = tmp_path / "events.json"
    monkeypatch.setattr(live_fills, "EVENTS_FILE", events)

    class Broker:
        def get_orders(self):
            return []

    assert live_fills.reconcile(Broker(), now=NOW) == 0
    ev = json.loads(events.read_text(encoding="utf-8"))[f"fill_expire:{CODE}:2026-09-11"]
    assert ev["alert"] is False


# ---------- 6) 记账幂等：账本写了、pending 没写回也不许重复记账（审查 MEDIUM D）----------
# reconcile 是「save_ledger（补记成交）→ save_pending（写回 volume_recorded）」两步，
# 不是一次原子写。中间被 kill（cron 叠跑）、OOM，或 log_line/record_event 落盘抛异常
# （异常直接穿透出 _reconcile_locked，整轮 pending 回写全丢）→ pending 仍是旧
# volume_recorded → 下一轮同一笔成交再记一次：现金多记、持仓多扣，且账本自身看不出
# 异常（静默账实不符，之后所有额度/闸门/告警都建立在假账上）。
# 修法：把「这笔委托已记账的量」写进账本本身（与持仓变更同一次原子落地），
# 下一轮取 pending 与账本标记的较大者作为基准。

def _seed_ledger(tmp_path):
    """账本初值：500 股 @16，虚拟现金 ¥100,000（monkeypatch 由用例自己做）。"""
    return {"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {CODE: {"volume": 500, "cost_price": 16.0,
                             "buy_ts": NOW.isoformat(), "last_ts": NOW.isoformat()}}}}}


class _FilledBroker:
    def get_orders(self):
        return [{"order_id": "T1001", "status": "filled", "filled_volume": 100,
                 "filled_price": 17.5, "order_price": 17.0, "total_volume": 100,
                 "stock_code": CODE}]


@pytest.fixture
def crash_env(monkeypatch, tmp_path):
    """账本 + pending + 锁全部落 tmp；返回 (ledger_file, pending_file)。"""
    import live_ledger

    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text(json.dumps(_seed_ledger(tmp_path), ensure_ascii=False),
                           encoding="utf-8")
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", ledger_file)

    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")
    monkeypatch.setattr(live_fills, "PENDING_FILE", pending)
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "reconcile.lock")
    return ledger_file, pending


def test_crash_between_ledger_and_pending_writeback_does_not_double_book(crash_env):
    """账本已补记、pending 没来得及写回（模拟崩溃）→ 下一轮不得再记一次。"""
    ledger_file, pending = crash_env

    assert live_fills.reconcile(_FilledBroker(), now=NOW) == 1
    booked = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert booked["agents"][AGENT]["positions"][CODE]["volume"] == 400
    assert booked["agents"][AGENT]["virtual_cash"] == 101750.0     # +100×17.5

    # 崩溃窗口：账本已落地，pending 却还是下单时的旧快照
    pending.write_text(json.dumps([_pending_entry()]), encoding="utf-8")

    assert live_fills.reconcile(_FilledBroker(), now=NOW) == 0      # 不再补记
    again = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert again["agents"][AGENT]["positions"][CODE]["volume"] == 400
    assert again["agents"][AGENT]["virtual_cash"] == 101750.0
    assert live_fills.load_pending() == []                          # 终态照常移除


def test_applied_marker_is_scoped_to_today(crash_env):
    """标记只认当日：委托号每日重复，昨天的同号标记不得吞掉今天的成交。"""
    ledger_file, pending = crash_env
    doc = json.loads(ledger_file.read_text(encoding="utf-8"))
    doc["applied_fills"] = {"T1001": {"filled": 100, "ts": "2026-09-10"}}
    ledger_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    assert live_fills.reconcile(_FilledBroker(), now=NOW) == 1      # 昨天的标记失效
    booked = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert booked["agents"][AGENT]["positions"][CODE]["volume"] == 400
    assert booked["applied_fills"] == {"T1001": {"filled": 100, "ts": "2026-09-11"}}


def test_ledger_without_marker_still_books(monkeypatch, tmp_path):
    """存量账本没有标记字段 → 行为与从前一致（不漏记）。"""
    import live_ledger

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

    assert live_fills.reconcile(_FilledBroker(), now=NOW) == 1
    assert json.loads(ledger_file.read_text(encoding="utf-8"))[
        "agents"][AGENT]["positions"][CODE]["volume"] == 400
