"""委托事件读侧口径单测（scripts/order_events.py）。

背景（2026-09-20）：周五两个止损卖单失败事件在周六/周日午夜被反复重播。
根因一：alert.sh 的按日去重键是裸 `date +%F`（主机 JST，北京 23:00 就翻日）；
根因二：事件写上去之后没有任何「已解决」出口，只能等下一次写入时的 24h 惰性清理——
周末没有写入，事件就永远在告警面上。本模块是读侧唯一口径：
  - 保留窗口与写侧同一常量（EVENT_KEEP_H，单一出处）；
  - 人工 ack（resolved_* 审计字段，flock 原子写）；
  - 卖域事件在账本已无该持仓时自动失效（保守：任一 agent 还持有就继续告警）。
告警路径（active_events/order_event_lines）**只读**，绝不写数据文件。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_order_events.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import order_events as OE  # noqa: E402

BJ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 20, 14, 0, tzinfo=BJ)

EV_ID = "unfilled:300408.SZ:2026-09-18"


def _bj(*args):
    return datetime(*args, tzinfo=BJ)


def _ev(ts="2026-09-20T10:22:03.240684+08:00", kind="unfilled", code="300408.SZ",
        side="sell", msg="卖未成交", alert=True, **kw):
    return {"ts": ts, "kind": kind, "code": code, "side": side,
            "msg": msg, "alert": alert, **kw}


def _ledger(codes=("300408.SZ",), agent="deepseek-v4-pro"):
    return {"version": 1, "applied_fills": [],
            "agents": {agent: {"positions": {c: {"volume": 100} for c in codes}}}}


def _write(tmp_path, doc, name="live_order_events.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ---------- 告警面：哪些事件还要打扰人 ----------

def test_active_lines_sorted_oldest_first():
    """两条在窗口内的告警事件 → 按时间升序输出「id|文案」。"""
    doc = {"b:001312.SZ:2026-09-20": _ev(ts="2026-09-20T13:05:00+08:00",
                                         code="001312.SZ", msg="后"),
           "a:600309.SH:2026-09-20": _ev(ts="2026-09-20T10:05:00+08:00",
                                         code="600309.SH", msg="先")}

    lines = OE.order_event_lines(doc, now=NOW)

    assert lines == ["a:600309.SH:2026-09-20|先", "b:001312.SZ:2026-09-20|后"]


def test_quiet_and_blank_msg_events_never_alert():
    """alert=False（留痕）和空文案的事件永远不上告警面。"""
    doc = {"q:x:2026-09-19": _ev(ts="2026-09-19T14:05:00+08:00", alert=False),
           "e:x:2026-09-19": _ev(ts="2026-09-19T14:05:00+08:00", msg="   ")}

    assert OE.order_event_lines(doc, now=NOW) == []
    assert OE.evaluate(doc["q:x:2026-09-19"], ledger=None, now=NOW) == OE.QUIET


def test_expired_events_stop_alerting():
    """超过 EVENT_KEEP_H（与写侧同一窗口）→ 不再告警，状态可审计为「已过期」。"""
    old = _ev(ts="2026-09-18T10:22:00+08:00")          # 49.6h 前
    edge = _ev(ts="2026-09-19T13:59:00+08:00")         # 24h01m 前 → 也已过期
    fresh = _ev(ts="2026-09-19T15:00:00+08:00")        # 23h 前 → 仍在窗口内

    assert OE.evaluate(old, ledger=None, now=NOW) == OE.EXPIRED
    assert OE.evaluate(edge, ledger=None, now=NOW) == OE.EXPIRED
    assert OE.evaluate(fresh, ledger=None, now=NOW) == OE.ALERT
    assert OE.order_event_lines({"o": old, "f": fresh}, now=NOW) == ["f|卖未成交"]


def test_unparseable_ts_kept_alerting():
    """时间戳读不出来 → 判不了年龄，宁可继续告警（与写侧「不删」同向）。"""
    assert OE.evaluate(_ev(ts="什么时候"), ledger=None, now=NOW) == OE.ALERT


def test_acked_events_stop_alerting():
    """人工 ack 过的事件（resolved_ts）从告警面消失。"""
    ev = _ev(resolved_ts="2026-09-19T09:00:00+08:00", resolved_by="zbox",
             resolved_reason="已手工卖出")

    assert OE.evaluate(ev, ledger=None, now=NOW) == OE.RESOLVED
    assert OE.order_event_lines({"x": ev}, now=NOW) == []


# ---------- 自动解决：卖域 + 账本已无持仓 ----------

def test_autoresolve_only_when_ledger_no_longer_holds():
    """同一事件：账本仍持有 → 继续告警；账本已无该标的 → 自动失效。"""
    ev = _ev()

    assert OE.evaluate(ev, ledger=_ledger(("300408.SZ",)), now=NOW) == OE.ALERT
    assert OE.evaluate(ev, ledger=_ledger(("600176.SH",)), now=NOW) == OE.AUTO


def test_autoresolve_requires_usable_ledger():
    """账本缺档/结构不认识 → 禁用自动解决（宁多扰勿漏报）。"""
    ev = _ev()

    assert OE.evaluate(ev, ledger=None, now=NOW) == OE.ALERT
    assert OE.evaluate(ev, ledger={}, now=NOW) == OE.ALERT
    assert OE.evaluate(ev, ledger={"agents": {}}, now=NOW) == OE.ALERT
    assert OE.evaluate(ev, ledger={"version": 1, "agents": {}}, now=NOW) == OE.AUTO


def test_autoresolve_never_for_buy_or_manual_verify_kinds():
    """买入侧不自动解决；「账本不可信」类（no_order_id/outcome_unknown）不自动解决。"""
    assert OE.evaluate(_ev(side="buy"), ledger=_ledger(("600176.SH",)), now=NOW) == OE.ALERT
    assert OE.evaluate(_ev(kind="no_order_id", side=""), ledger=_ledger(("600176.SH",)),
                       now=NOW) == OE.ALERT
    assert OE.evaluate(_ev(kind="outcome_unknown"), ledger=_ledger(("600176.SH",)),
                       now=NOW) == OE.ALERT
    assert OE.evaluate(_ev(kind="untracked_order", side=""), ledger=_ledger(("600176.SH",)),
                       now=NOW) == OE.ALERT


def test_dup_unresolved_autoresolves_despite_empty_side():
    """dup_unresolved 的 side 是空串（实录 300408），但语义就是卖出——持仓没了即失效。"""
    ev = _ev(kind="dup_unresolved", side="")

    assert OE.evaluate(ev, ledger=_ledger(("600176.SH",)), now=NOW) == OE.AUTO
    assert OE.evaluate(ev, ledger=_ledger(("300408.SZ",)), now=NOW) == OE.ALERT
    # refire / sell_failed 同为卖域
    assert OE.evaluate(_ev(kind="refire", side="sell"), ledger=_ledger(()), now=NOW) == OE.AUTO
    assert OE.evaluate(_ev(kind="sell_failed", side=""), ledger=_ledger(()), now=NOW) == OE.AUTO


def test_holdings_codes_counts_positive_volume_only():
    """volume=0 的残留条目不算持仓；多个 agent 取并集。"""
    led = {"version": 1, "agents": {
        "a": {"positions": {"600176.SH": {"volume": 0}, "300408.SZ": {"volume": 100}}},
        "b": {"positions": {"002074.SZ": {"volume": 300}}}}}

    assert OE.holdings_codes(led) == {"300408.SZ", "002074.SZ"}


# ---------- 告警路径只读 ----------

def test_read_path_never_writes_file(tmp_path):
    """active/lines 是纯读：数据文件字节不变（triage 写入只发生在 ack）。"""
    p = _write(tmp_path, {EV_ID: _ev()})
    before = p.read_bytes()

    OE.order_event_lines(json.loads(before), ledger=_ledger(()), now=NOW)
    OE.active_events(json.loads(before), ledger=_ledger(()), now=NOW)

    assert p.read_bytes() == before


# ---------- ack：人工出口 + 审计 ----------

def test_ack_writes_audit_fields(tmp_path):
    p = _write(tmp_path, {EV_ID: _ev(), "other:x:2026-09-19": _ev(code="x")})

    out = OE.ack(p, [EV_ID], by="zbox", reason="已手工卖出", now=NOW)

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert out == {"acked": [EV_ID], "already": [], "missing": []}
    assert doc[EV_ID]["resolved_ts"] == "2026-09-20T14:00:00+08:00"
    assert doc[EV_ID]["resolved_by"] == "zbox"
    assert doc[EV_ID]["resolved_reason"] == "已手工卖出"
    assert "resolved_ts" not in doc["other:x:2026-09-19"]
    assert not (tmp_path / "live_order_events.json.tmp").exists()


def test_ack_is_idempotent_and_keeps_first_resolution(tmp_path):
    """重复 ack 不改写首次结论（审计完整性）。"""
    p = _write(tmp_path, {EV_ID: _ev()})

    OE.ack(p, [EV_ID], by="first", reason="第一次", now=NOW)
    out = OE.ack(p, [EV_ID], by="second", reason="第二次", now=NOW)

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert out == {"acked": [], "already": [EV_ID], "missing": []}
    assert doc[EV_ID]["resolved_by"] == "first"


def test_ack_appends_durable_audit_log(tmp_path):
    """事件记录会随保留窗口被清掉 → 签收事实追加到只增不删的 ack.jsonl。"""
    p = _write(tmp_path, {EV_ID: _ev()})

    OE.ack(p, [EV_ID], by="zbox", reason="已手工卖出", now=NOW)

    log = tmp_path / OE.ACK_LOG_NAME
    line = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert line["id"] == EV_ID and line["by"] == "zbox"
    assert line["acked_ts"] == "2026-09-20T14:00:00+08:00"
    assert line["event"]["resolved_by"] == "zbox"      # 事件快照一起留档

    OE.ack(p, [EV_ID], by="second", reason="重复", now=NOW)   # 幂等：不再追加
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_ack_missing_id_reported_and_file_untouched(tmp_path):
    p = _write(tmp_path, {EV_ID: _ev()})
    before = p.read_bytes()

    out = OE.ack(p, ["不存在的id"], by="zbox", reason="x", now=NOW)

    assert out == {"acked": [], "already": [], "missing": ["不存在的id"]}
    assert p.read_bytes() == before


def test_ack_tolerates_missing_file(tmp_path):
    """文件不存在（新主机）→ 全部按 missing 报，不抛异常。"""
    out = OE.ack(tmp_path / "nope.json", [EV_ID], by="zbox", reason="x", now=NOW)

    assert out["missing"] == [EV_ID]


# ---------- 与写侧共用同一条保留窗口 ----------

def test_prune_doc_matches_write_side_semantics():
    """写侧惰性清理 = prune_doc：超窗删除、不可解析保留（与 live_fills 同口径）。"""
    doc = {"old": _ev(ts="2026-09-18T10:00:00+08:00"),
           "keep": _ev(ts="2026-09-19T20:00:00+08:00"),
           "bad": _ev(ts="乱七八糟")}

    assert set(OE.prune_doc(doc, NOW)) == {"keep", "bad"}


def test_keep_window_single_source_with_live_fills():
    """读侧窗口必须就是写侧窗口（单一出处）：live_fills 从本模块导入常量。"""
    import live_fills as LF

    assert LF.EVENT_KEEP_H == OE.EVENT_KEEP_H


def test_lock_path_matches_live_fills():
    """ack 的互斥锁必须与 record_event 用同一把锁，否则读-改-写会互相覆盖。

    live_fills 的锁就在它的事件文件旁边（同目录同约定）；order_events 按同一
    规则推导 → 生产路径 data/live_order_events.json 上两者是同一个文件。
    （这里用 live_fills 的常量而不是拼生产路径：测试里 conftest 会把数据目录
    重定向到 tmp_path。）
    """
    import live_fills as LF

    assert LF.RECONCILE_LOCK_FILE.parent == Path(LF.EVENTS_FILE).parent
    assert OE.lock_path_for(LF.EVENTS_FILE) == LF.RECONCILE_LOCK_FILE


# ---------- 容错 & CLI ----------

def test_load_doc_tolerates_garbage(tmp_path):
    assert OE.load_doc(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{不是 json", encoding="utf-8")
    assert OE.load_doc(bad) == {}
    lst = tmp_path / "list.json"
    lst.write_text("[1,2]", encoding="utf-8")
    assert OE.load_doc(lst) == {}


def test_cli_list_prints_status_buckets(tmp_path, capsys):
    p = _write(tmp_path, {
        EV_ID: _ev(),
        "expired:x:2026-09-17": _ev(ts="2026-09-17T10:00:00+08:00", code="x"),
        "acked:x:2026-09-19": _ev(ts="2026-09-19T10:00:00+08:00", code="x",
                                  resolved_ts="2026-09-19T11:00:00+08:00",
                                  resolved_by="zbox", resolved_reason="核对完毕"),
        "quiet:x:2026-09-19": _ev(ts="2026-09-19T10:00:00+08:00", code="x", alert=False)})

    rc = OE.main(["list", str(p), "--now", NOW.isoformat()])
    out = capsys.readouterr().out

    assert rc == 0
    assert EV_ID in out and "告警" in out
    assert "已过期" in out and "已签收" in out
    assert "留痕 1" in out


def test_cli_ack_end_to_end(tmp_path, capsys):
    p = _write(tmp_path, {EV_ID: _ev()})

    assert OE.main(["ack", str(p), EV_ID, "--by", "cli", "--reason", "测试",
                    "--now", NOW.isoformat()]) == 0
    assert "已签收 1" in capsys.readouterr().out
    assert json.loads(p.read_text(encoding="utf-8"))[EV_ID]["resolved_by"] == "cli"

    assert OE.main(["ack", str(p), "没这个id", "--now", NOW.isoformat()]) == 1
    assert "不存在" in capsys.readouterr().err


def test_cli_usage_error():
    assert OE.main([]) == 2
    assert OE.main(["unknown"]) == 2
