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
