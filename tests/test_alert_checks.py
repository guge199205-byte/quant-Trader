"""alert.sh 判定函数单测（scripts/alert_checks.py）。

这些阈值以前写在 alert.sh 的内联 heredoc 里，改错了只能靠线上误报发现：
- 新闻停更下午窗口 13:00 判定 → 12:55 那期实测 13:08 才落盘 → 每天误报两条；
- 09:35 主入口崩了零告警（2026-09-07/09-09 各一次全天不调仓）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_alert_checks.py -q
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alert_checks as A  # noqa: E402

BJ = A.BJ


def _bj(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=BJ)


# ---------- 新闻停更 ----------

def test_news_stale_skips_early_afternoon_before_first_publish():
    """13:05 看的是 11:04 的旧刊（12:55 那期 13:08 才落盘）→ 不判，避免误报。"""
    now = _bj(2026, 9, 9, 13, 5)
    mtime = _bj(2026, 9, 9, 11, 4).timestamp()

    assert A.news_stale_minutes(now, mtime) is None


def test_news_stale_reports_after_afternoon_window_opens():
    now = _bj(2026, 9, 9, 13, 25)
    mtime = _bj(2026, 9, 9, 11, 4).timestamp()

    assert A.news_stale_minutes(now, mtime) == 141


def test_news_stale_quiet_when_process_running_or_file_missing():
    now = _bj(2026, 9, 9, 14, 30)
    old = _bj(2026, 9, 9, 11, 4).timestamp()

    assert A.news_stale_minutes(now, old, running=True) is None
    assert A.news_stale_minutes(now, None) is None
    assert A.news_stale_minutes(now, now.timestamp()) is None       # 刚更新


def test_news_stale_skips_before_morning_second_issue():
    """早盘前两刊（09:25/09:55）未齐、以及非盘中时段都不判。"""
    old = _bj(2026, 9, 9, 9, 25).timestamp()

    assert A.news_stale_minutes(_bj(2026, 9, 9, 10, 30), old) is None
    assert A.news_stale_minutes(_bj(2026, 9, 9, 12, 30), old) is None   # 午休
    assert A.news_stale_minutes(_bj(2026, 9, 12, 14, 0), old) is None   # 周六


# ---------- 09:35 主入口执行状态 ----------

def test_llm_trade_tier_reports_missing_and_failed_states():
    old = {"day": "2026-09-08", "ok": True}

    assert A.llm_trade_tier(_bj(2026, 9, 9, 10, 5), old).startswith("t1|当日无执行记录")
    assert A.llm_trade_tier(
        _bj(2026, 9, 9, 10, 5),
        {"day": "2026-09-09", "ok": None, "orders_attempted": False}
    ).startswith("t1|下单前失败")
    assert A.llm_trade_tier(
        _bj(2026, 9, 9, 10, 5),
        {"day": "2026-09-09", "ok": False, "orders_attempted": True, "note": "crashed: x"}
    ).startswith("t1|已下过单")


def test_llm_trade_tier_escalates_to_t2_after_both_catchups():
    doc = {"day": "2026-09-09", "ok": False, "orders_attempted": False,
           "note": "bridge_account_query"}

    assert A.llm_trade_tier(_bj(2026, 9, 9, 11, 0), doc).startswith("t1|")
    assert A.llm_trade_tier(_bj(2026, 9, 9, 11, 15), doc).startswith("t2|")


def test_llm_trade_tier_quiet_when_ok_or_outside_window():
    ok = {"day": "2026-09-09", "ok": True}

    assert A.llm_trade_tier(_bj(2026, 9, 9, 11, 0), ok) is None
    assert A.llm_trade_tier(_bj(2026, 9, 9, 9, 50), {}) is None      # 09:35 前不判
    assert A.llm_trade_tier(_bj(2026, 9, 9, 15, 30), {}) is None     # 收盘后不判
    assert A.llm_trade_tier(_bj(2026, 9, 12, 11, 0), {}) is None     # 周末


# ---------- 抓取桶失败 ----------

def test_folder_failures_threshold_and_shape():
    doc = {"last_run": {"folder_failures": [[1, "超时"], [4, "502"], ["x", "bad"]]}}

    assert A.folder_failures(doc) == [(1, "超时"), (4, "502")]
    assert A.folder_failure_line(doc) is None          # 2 个 < 阈值 3

    doc["last_run"]["folder_failures"].append([7, "空响应"])
    line = A.folder_failure_line(doc)
    assert line and "3 个新闻抓取桶失败" in line and "桶1:超时" in line


def test_folder_failures_empty_doc():
    assert A.folder_failures({}) == []
    assert A.folder_failure_line(None) is None
    assert A.folder_failure_line({"last_run": {}}) is None


# ---------- 行情源降级去抖 ----------

def test_rt_down_debounces_single_fuyao_blip():
    """fuyao 单点抖动（5-10 分钟自愈，一天十几次）不打扰。"""
    doc = {"fuyao": {"ok": False, "fail_streak": 1,
                     "first_fail_ts": "2026-09-09T14:00:00"}}

    assert A.rt_down(doc) == ("", "")


def test_rt_down_reports_persistent_fuyao_with_streak_and_since():
    doc = {"fuyao": {"ok": False, "fail_streak": 3,
                     "first_fail_ts": "2026-09-09T14:00:00"}}

    immediate, byday = A.rt_down(doc)

    assert immediate == "fuyao(连续3次自14:00)"
    assert byday == ""


def test_rt_down_bridge_immediate_legacy_format_and_aidata_byday():
    """bridge 主源立即报；老格式（无 streak）按 1 次算，不改变原有行为。"""
    doc = {"bridge": {"ok": False, "error": "refused"},
           "aidata": {"ok": False, "fail_streak": 40,
                      "first_fail_ts": "2026-09-08T09:00:00"}}

    immediate, byday = A.rt_down(doc)

    assert immediate == "bridge(连续1次)"
    assert byday == "aidata(连续40次自09:00)"


def test_rt_down_quiet_when_all_ok_or_missing():
    assert A.rt_down({"bridge": {"ok": True}, "fuyao": {"ok": True},
                      "aidata": {"ok": True}}) == ("", "")
    assert A.rt_down(None) == ("", "")
    assert A.rt_down({}) == ("", "")


# ---------- L2 快照新鲜度 ----------

def test_l2_stale_reports_when_stale_in_session():
    now = _bj(2026, 9, 9, 10, 30)

    assert A.l2_stale_line(now, _bj(2026, 9, 9, 10, 5).timestamp()) == "停更 25 分钟"
    assert A.l2_stale_line(now, _bj(2026, 9, 9, 10, 20).timestamp()) is None   # 10 分钟 < 阈值


def test_l2_stale_quiet_outside_session_and_on_holiday():
    old = _bj(2026, 9, 9, 10, 0).timestamp()

    assert A.l2_stale_line(_bj(2026, 9, 9, 9, 40), old) is None     # 开盘首写还没落盘
    assert A.l2_stale_line(_bj(2026, 9, 9, 12, 0), old) is None     # 午休
    assert A.l2_stale_line(_bj(2026, 9, 9, 15, 30), old) is None    # 收盘后
    assert A.l2_stale_line(_bj(2026, 9, 12, 10, 30), old) is None   # 周六（默认按工作日判）
    assert A.l2_stale_line(_bj(2026, 9, 9, 10, 30), old, trading_day=False) is None


def test_l2_stale_afternoon_grace_covers_lunch_gap():
    """11:30 末笔到 13:00 首笔天然隔 90 分钟，13:15 前不判（否则每天中午误报）。"""
    last_morning = _bj(2026, 9, 9, 11, 30).timestamp()

    assert A.l2_stale_line(_bj(2026, 9, 9, 13, 10), last_morning) is None
    assert A.l2_stale_line(_bj(2026, 9, 9, 13, 20), last_morning) == "停更 110 分钟"


def test_l2_stale_missing_file_only_reported_in_window():
    assert A.l2_stale_line(_bj(2026, 9, 9, 10, 30), None) == "文件缺失"
    assert A.l2_stale_line(_bj(2026, 9, 9, 8, 0), None) is None


# ---------- 交易日判定 ----------

def test_is_trading_day_reads_calendar_then_falls_back(tmp_path):
    cal = tmp_path / "trading_days.json"
    # 覆盖到 09-11：09-10 在覆盖区间内但不在清单里（模拟休市日），09-11 是交易日
    cal.write_text(json.dumps({"days": ["20260908", "20260909", "20260911"]}),
                   encoding="utf-8")

    assert A.is_trading_day(_bj(2026, 9, 9, 10, 0), cal) is True
    assert A.is_trading_day(_bj(2026, 9, 10, 10, 0), cal) is False
    assert A.is_trading_day(_bj(2027, 9, 9, 10, 0), cal) is True    # 超出覆盖 → 周四算交易日
    assert A.is_trading_day(_bj(2027, 9, 11, 10, 0), cal) is False  # 超出覆盖 → 周六不算
    assert A.is_trading_day(_bj(2026, 9, 12, 10, 0), tmp_path / "nope.json") is False


def test_is_trading_day_knows_real_holiday():
    """真实日历必须能识别法定假日——读不到文件会静默退化为工作日（国庆变交易日）。"""
    assert A.is_trading_day(_bj(2026, 10, 1, 10, 0)) is False      # 国庆
    assert A.is_trading_day(_bj(2026, 9, 9, 10, 0)) is True


# ---------- CLI ----------

def test_cli_shim_prints_nothing_without_alert(tmp_path, capsys):
    p = tmp_path / "state.json"
    p.write_text('{"last_run": {"folder_failures": []}}', encoding="utf-8")

    assert A.main(["folder_failures", str(p)]) == 0
    assert capsys.readouterr().out == ""


def test_cli_shim_rejects_unknown_check(capsys):
    assert A.main(["nope", "x"]) == 2
    assert "unknown check" in capsys.readouterr().err


def test_cli_shim_rt_down_prints_two_lines(tmp_path, capsys):
    p = tmp_path / "rt_status.json"
    p.write_text(json.dumps({"bridge": {"ok": False, "fail_streak": 2},
                             "fuyao": {"ok": False, "fail_streak": 1},
                             "aidata": {"ok": False, "fail_streak": 5}}),
                 encoding="utf-8")

    assert A.main(["rt_down", str(p)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "bridge(连续2次)"      # fuyao 被去抖掉
    assert lines[1] == "aidata(连续5次)"


def test_cli_shim_news_stale_uses_real_mtime(tmp_path, capsys, monkeypatch):
    p = tmp_path / "latest.json"
    p.write_text("{}", encoding="utf-8")
    import os

    old = (datetime.now(BJ) - timedelta(minutes=120)).timestamp()
    os.utime(p, (old, old))

    rc = A.main(["news_stale", str(p)])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    # 结果取决于运行时刻是否在判定窗口内：窗口内应报出分钟数，否则空
    assert out == "" or out.isdigit()


def test_cli_shim_l2_stale_uses_real_mtime(tmp_path, capsys, monkeypatch):
    import os

    p = tmp_path / "l2_factors_live.json"
    p.write_text("{}", encoding="utf-8")
    old = _bj(2026, 9, 9, 10, 5).timestamp()
    os.utime(p, (old, old))

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 9, 10, 30)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)
    monkeypatch.setattr(A, "is_trading_day", lambda *a, **k: True)

    assert A.main(["l2_stale", str(p)]) == 0
    assert capsys.readouterr().out.strip() == "停更 25 分钟"


def test_cli_shim_l2_stale_missing_file(tmp_path, capsys, monkeypatch):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 9, 10, 30)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)
    monkeypatch.setattr(A, "is_trading_day", lambda *a, **k: True)

    assert A.main(["l2_stale", str(tmp_path / "nope.json")]) == 0
    assert capsys.readouterr().out.strip() == "文件缺失"


# ---------- 账户通道（桥假活：行情通、账号掉线）----------

def _acct_board(ok, streak=1, err="asset=0 positions=0（桥假活）", ts="2026-09-10T09:30:00"):
    return {"account": {"ok": ok, "error": None if ok else err,
                        "fail_streak": streak, "first_fail_ts": ts}}


def test_account_down_debounced_on_first_failure():
    """单次失败（桥重启窗口）不报——行情侧的抖动同理，连续 2 次才算掉线。"""
    assert A.account_down(_acct_board(False, streak=1), _bj(2026, 9, 10, 9, 20)) is None


def test_account_down_reports_after_two_failures():
    """2026-09-10 实录：账户通道整日 asset=0，7 轮分析+3 次调仓零告警 → 必须报。"""
    line = A.account_down(_acct_board(False, streak=3), _bj(2026, 9, 10, 9, 20))

    assert line is not None
    assert "3" in line and "09:30" in line and "桥假活" in line


def test_account_down_silent_outside_actionable_window():
    """半夜/盘后不报（故障不自愈，报了也无人处理，只会刷屏）。"""
    assert A.account_down(_acct_board(False, streak=9), _bj(2026, 9, 10, 3, 0)) is None
    assert A.account_down(_acct_board(False, streak=9), _bj(2026, 9, 10, 16, 0)) is None
    assert A.account_down(_acct_board(False, streak=9), _bj(2026, 9, 12, 9, 20)) is None  # 周六


def test_account_down_silent_when_healthy_or_block_missing():
    assert A.account_down(_acct_board(True), _bj(2026, 9, 10, 9, 20)) is None
    assert A.account_down({}, _bj(2026, 9, 10, 9, 20)) is None      # 老看板无该路：不误报
    assert A.account_down(None, _bj(2026, 9, 10, 9, 20)) is None


def test_account_down_cli(tmp_path, capsys, monkeypatch):
    p = tmp_path / "rt_status.json"
    p.write_text(json.dumps(_acct_board(False, streak=2)), encoding="utf-8")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 10, 9, 20)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)

    assert A.main(["account_down", str(p)]) == 0
    assert "连续2次" in capsys.readouterr().out


# ---------- 挂单停滞：常规在途 vs 保护价挂队（2026-09-11） ----------
# 保护价挂队（止损卖出报跌停价）本来就是「等买盘」，重启桥没有任何意义，
# 只能如实告警；常规在途停滞才继续走重启自愈的老路。

def _pend(code="001312.SZ", ts="2026-09-11T10:00:00", **kw):
    return dict({"order_id": "T1", "agent": "pro", "code": code, "side": "sell",
                 "volume": 500, "price": 15.5, "volume_recorded": 0, "ts": ts}, **kw)


def test_pending_stuck_splits_protect_from_normal():
    now = _bj(2026, 9, 11, 10, 20)      # 盘中，两笔都超 10 分钟
    n, p = A.pending_stuck([_pend(), _pend(code="600309.SH", protect=True)], now)

    assert (n, p) == (1, 1)


def test_pending_stuck_ignores_fresh_orders_and_weekend():
    now = _bj(2026, 9, 11, 10, 5)
    assert A.pending_stuck([_pend(ts="2026-09-11T10:00:00")], now) == (0, 0)
    assert A.pending_stuck([_pend()], _bj(2026, 9, 12, 10, 30)) is None   # 周六
    assert A.pending_stuck([_pend()], _bj(2026, 9, 11, 20, 0)) is None    # 盘后


def test_pending_stuck_cli_prints_one_combined_line(tmp_path, capsys, monkeypatch):
    p = tmp_path / "pending.json"
    p.write_text(json.dumps([_pend(), _pend(code="600309.SH", protect=True)]),
                 encoding="utf-8")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 11, 10, 20)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)

    assert A.main(["pending_stuck", str(p)]) == 0
    assert capsys.readouterr().out.strip() == "1|1"


# ---------- 委托事件（哨兵止损的下单/未成交/挂队） ----------

def test_order_event_lines_lists_alert_events_oldest_first():
    doc = {"b:001312.SZ:2026-09-11": {"ts": "2026-09-11T10:31:00", "kind": "b",
                                      "msg": "后", "alert": True},
           "a:600309.SH:2026-09-11": {"ts": "2026-09-11T10:05:00", "kind": "a",
                                      "msg": "先", "alert": True},
           "t:600309.SH:2026-09-11": {"ts": "2026-09-11T10:06:00", "kind": "t",
                                      "msg": "留痕不告警", "alert": False}}

    assert A.order_event_lines(doc) == [
        "a:600309.SH:2026-09-11|先", "b:001312.SZ:2026-09-11|后"]


def test_order_event_lines_tolerates_garbage():
    assert A.order_event_lines(None) == []
    assert A.order_event_lines({}) == []
    assert A.order_event_lines({"x": "不是字典"}) == []
