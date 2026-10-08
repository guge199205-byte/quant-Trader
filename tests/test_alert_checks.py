"""alert.sh 判定函数单测（scripts/alert_checks.py）。

这些阈值以前写在 alert.sh 的内联 heredoc 里，改错了只能靠线上误报发现：
- 新闻停更下午窗口 13:00 判定 → 12:55 那期实测 13:08 才落盘 → 每天误报两条；
- 09:35 主入口崩了零告警（2026-09-07/09-09 各一次全天不调仓）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_alert_checks.py -q
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alert_checks as A  # noqa: E402

BJ = A.BJ


def _bj(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=BJ)


# ---------- 新闻停更（2026-09-18 重构：成功游标判定） ----------
# 旧实现用 latest.json 的 mtime 判停更：① 任何写动作都能刷新鲜 mtime；② 空窗轮
# 行为不受控；③ pgrep 到进程在跑就豁免（卡死进程反而挡住告警）。09-18 实录：
# 主机冻结打掉 09:25/09:55 两轮，白天引用 14 小时前的分子，上午窗口零告警。
# 新判据读 state.json：last_end（跑满五段且无桶失败才推进的成功游标）判"管线停更"；
# max(last_chief_ok, last_empty) 判"分子无产出"（主编持续截断失败会停更该标记，
# 空窗是合法形态、计入）。返回值带类型前缀 "round|" / "chief|"（alert.sh 按类型+小时去重）。


def _state(last_end="2026-09-09T11:04:00+08:00", **kw):
    return {"last_end": last_end, **kw}


def test_news_freshness_round_dead_reports_last_success_time():
    """09-18 事故重放：10:45 读到昨日 19:05 的游标 → 必须报，且给最近成刊时间。"""
    now = _bj(2026, 9, 18, 10, 45)
    doc = _state(last_end="2026-09-17T19:05:00+08:00")

    out = A.news_freshness(now, doc)

    assert out is not None and out.startswith("round|")
    assert "停更" in out and "09-17 19:05" in out


def test_news_freshness_skips_early_afternoon_before_first_publish():
    """13:05 看的是 11:04 的旧刊（12:55 那期 13:08 才落盘）→ 不判，避免误报。"""
    assert A.news_freshness(_bj(2026, 9, 9, 13, 5), _state()) is None


def test_news_freshness_reports_after_afternoon_window_opens():
    out = A.news_freshness(_bj(2026, 9, 9, 13, 25), _state())

    assert out is not None and out.startswith("round|") and "停更 141 分钟" in out


def test_news_freshness_quiet_when_process_running_or_state_missing():
    old = _state(last_end="2026-09-09T11:04:00+08:00")
    now = _bj(2026, 9, 9, 14, 30)

    assert A.news_freshness(now, old, running=True) is None
    assert A.news_freshness(now, {}) is None                          # 无游标不判
    assert A.news_freshness(now, {"last_end": "坏时间"}) is None
    assert A.news_freshness(now, _state(last_end=now.isoformat())) is None   # 刚更新


def test_news_freshness_quiet_before_morning_window_and_off_days():
    """早盘前两刊（09:25/09:55）未齐、非盘中时段、周末、节假日都不判。"""
    old = _state(last_end="2026-09-09T09:25:00+08:00")

    assert A.news_freshness(_bj(2026, 9, 9, 10, 30), old) is None
    assert A.news_freshness(_bj(2026, 9, 9, 12, 30), old) is None     # 午休
    assert A.news_freshness(_bj(2026, 9, 12, 14, 0), old) is None     # 周六
    # 工作日但非交易日（法定假日）：判定窗口内也不误报
    assert A.news_freshness(_bj(2026, 10, 1, 14, 0), old,
                            trading_day=False) is None


def test_news_freshness_reports_unreadable_state_inside_window():
    """state.json 缺失/损坏 = 判定不可用，本身就是告警（评审 LOW：此前与
    "首部署无游标"同样静默——文件被清/写坏的形态看不见）。窗口外仍不打扰。"""
    out = A.news_freshness(_bj(2026, 9, 9, 14, 30), {}, state_ok=False)

    assert out is not None and out.startswith("round|") and "不可读" in out
    assert A.news_freshness(_bj(2026, 9, 9, 12, 30), {}, state_ok=False) is None
    assert A.news_freshness(_bj(2026, 9, 9, 14, 30), {}, state_ok=False,
                            running=True) is None


def test_news_freshness_reports_future_cursor_and_tolerates_naive_now():
    """游标在将来（时钟回拨/时区写错）会让负分钟数静默吞掉告警 → 如实报出；
    裸 datetime 按北京解释，不许 TypeError。"""
    out = A.news_freshness(_bj(2026, 9, 9, 14, 30),
                           _state(last_end="2026-09-09T15:30:00+08:00"))

    assert out is not None and out.startswith("round|") and "将来" in out
    # naive now：按北京解释（此例游标新鲜 1 小时 → 不报），不得抛异常
    assert A.news_freshness(datetime(2026, 9, 9, 14, 30),
                            _state(last_end="2026-09-09T13:30:00+08:00")) is None


def test_news_freshness_attributes_stall_to_chief_failure():
    """真实构造（评审 M-1）：游标停更且 last_chief_fail 晚于 last_end →
    文案必须指向主编段（主编批量截断是高发形态，不能只让人去查主机/cron）。"""
    now = _bj(2026, 9, 9, 14, 30)
    doc = _state(last_end="2026-09-09T11:04:00+08:00",
                 last_chief_ok="2026-09-09T10:55:00+08:00",
                 last_chief_fail="2026-09-09T12:55:00+08:00")

    out = A.news_freshness(now, doc)

    assert out is not None and out.startswith("chief|")
    assert "主编无产出" in out and "12:55" in out


def test_news_freshness_chief_stall_flagged_when_rounds_advance():
    """防御路径：state 由其他写入方产生（last_end 新鲜但产出标记陈旧）时，
    分子停更仍需告警——当前 news_brief 写不出这种 state（主编失败不推游标），
    但读侧对写入方变体保持鲁棒。"""
    now = _bj(2026, 9, 9, 14, 30)
    doc = _state(last_end="2026-09-09T14:00:00+08:00",
                 last_chief_ok="2026-09-09T11:04:00+08:00")

    out = A.news_freshness(now, doc)

    assert out is not None and out.startswith("chief|")
    assert "无新产出" in out and "11:04" in out


def test_news_freshness_empty_window_counts_as_progress():
    """空窗（无新增新闻）是合法形态：last_empty 新鲜 → 不报主编停更。"""
    now = _bj(2026, 9, 9, 14, 30)
    doc = _state(last_end="2026-09-09T14:00:00+08:00",
                 last_chief_ok="2026-09-09T09:31:00+08:00",
                 last_empty="2026-09-09T14:00:00+08:00")

    assert A.news_freshness(now, doc) is None


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


def test_llm_trade_tier_quiet_when_exec_switch_off():
    """总闸关闭（note=exec_switch_off）→ 当日不调仓是预期行为，t1/t2 都不报。"""
    doc = {"day": "2026-09-09", "ok": None, "orders_attempted": False,
           "note": A.EXEC_SWITCH_OFF_NOTE}

    assert A.llm_trade_tier(_bj(2026, 9, 9, 10, 5), doc) is None
    assert A.llm_trade_tier(_bj(2026, 9, 9, 11, 15), doc) is None


def test_exec_switch_off_note_literal_matches_writer():
    """标记字面量跨模块必须一致：写方 live_llm_trade ↔ 读方 alert_checks。"""
    import live_llm_trade as L

    assert A.EXEC_SWITCH_OFF_NOTE == L.EXEC_SWITCH_OFF_NOTE


def test_llm_trade_tier_other_notes_still_alert():
    """对照组：别的 note（真失败）不受豁免影响。"""
    doc = {"day": "2026-09-09", "ok": None, "orders_attempted": False,
           "note": "bridge_account_query"}

    assert A.llm_trade_tier(_bj(2026, 9, 9, 10, 5), doc).startswith("t1|")


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

    assert A.rt_down(doc) == ("", "", "")


def test_rt_down_reports_persistent_fuyao_with_streak_and_since():
    doc = {"fuyao": {"ok": False, "fail_streak": 3,
                     "first_fail_ts": "2026-09-09T14:00:00"}}

    immediate, byday, key = A.rt_down(doc)

    assert immediate == "fuyao(连续3次自14:00)"
    assert byday == "" and key == ""


def test_rt_down_bridge_immediate_legacy_format_and_aidata_byday():
    """bridge 主源立即报；老格式（无 streak）按 1 次算，不改变原有行为。"""
    doc = {"bridge": {"ok": False, "error": "refused"},
           "aidata": {"ok": False, "fail_streak": 40,
                      "first_fail_ts": "2026-09-08T09:00:00"}}

    immediate, byday, key = A.rt_down(doc)

    assert immediate == "bridge(连续1次)"
    assert byday == "aidata(down 连续40次自09:00)", byday
    assert key == "down", "老看板（无 state）语义上一直是通道 down"


def test_rt_down_reports_a_corrupt_streak_without_inventing_a_number():
    """板子是外部 JSON：`fail_streak` 坏成非数字时，照报故障但**不编计数**。

    照抄 `_as_counter` 的兜底值会在 QQ 上印「连续 1 次」，而真相是「次数读不出来」
    —— 后者往往意味着写侧也坏了，是更强的信号，不能被一个假数字盖掉。
    """
    doc = {"bridge": {"ok": False, "error": "refused", "fail_streak": [1]}}

    immediate, byday, _ = A.rt_down(doc)

    assert immediate == "bridge(连续?次)", immediate
    assert byday == ""


def test_rt_down_quiet_when_all_ok_or_missing():
    assert A.rt_down({"bridge": {"ok": True}, "fuyao": {"ok": True},
                      "aidata": {"ok": True}}) == ("", "", "")
    assert A.rt_down(None) == ("", "", "")
    assert A.rt_down({}) == ("", "", "")


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


def test_l2_minute_line_tolerates_a_bad_typed_streak():
    """降级戳里的计数来自落盘 JSON（data/l2_status.json）——类型坏了要让**检查活着**。

    裸 `int("abc")` 抛出去 → 这条检查 rc≠0 → 它静默失效（现在至少会被 acheck 记一笔，
    但那仍然等于没有这条检查）。戳自称 ok=False 时，唯一可行动的信息是「有降级」，
    计数只是为了防抖：数不出来按「已达阈值」处理，宁报勿哑。
    """
    doc = {"last_cycle_at": "2026-09-22T10:29:00+08:00",
           "capabilities": {"minute_k": {"ok": False, "state": "partial",
                                         "error": "3/10 只持仓没产出行情因子",
                                         "fail_streak": "abc"}}}

    line = A.l2_minute_line(doc, _bj(2026, 9, 22, 10, 30))

    assert line and "3/10" in line, line
    # 判据用了兜底值（宁报勿哑），但**显示不许照抄兜底值**：编一个「连续 2 次」
    # 进 QQ 就是给运维一个假数字。计数读不出来就印 `?`（写侧每轮自愈重写，
    # 所以这通常只坏一轮；真持续坏说明写侧也出问题了——那本身就是信息）。
    assert "连续?次" in line, line


def test_cli_shim_rejects_unknown_check(capsys):
    assert A.main(["nope", "x"]) == 2
    assert "unknown check" in capsys.readouterr().err


def test_cli_shim_rt_down_prints_three_lines(tmp_path, capsys):
    p = tmp_path / "rt_status.json"
    p.write_text(json.dumps({"bridge": {"ok": False, "fail_streak": 2},
                             "fuyao": {"ok": False, "fail_streak": 1},
                             "aidata": {"ok": False, "fail_streak": 5}}),
                 encoding="utf-8")

    assert A.main(["rt_down", str(p)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "bridge(连续2次)"      # fuyao 被去抖掉
    assert lines[1] == "aidata(down 连续5次)"
    assert lines[2] == "down", "第 3 行是按天去重键（alert.sh 拼进 /tmp 文件名）"


def test_cli_shim_news_stale_reads_success_cursor(tmp_path, capsys, monkeypatch):
    """CLI 读 state.json 的成功游标（不再看 latest.json 的 mtime）。"""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_end": "2026-09-09T11:04:00+08:00"}),
                 encoding="utf-8")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 9, 13, 25)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)

    assert A.main(["news_stale", str(p)]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("round|") and "停更 141 分钟" in out


def test_cli_shim_news_stale_missing_state_alerts(tmp_path, capsys, monkeypatch):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 9, 14, 30)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)

    assert A.main(["news_stale", str(tmp_path / "absent.json")]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("round|") and "不可读" in out


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

    assert A.order_event_lines(doc, now=_bj(2026, 9, 11, 12, 0)) == [
        "a:600309.SH:2026-09-11|先", "b:001312.SZ:2026-09-11|后"]


def test_order_event_lines_tolerates_garbage():
    assert A.order_event_lines(None) == []
    assert A.order_event_lines({}) == []
    assert A.order_event_lines({"x": "不是字典"}) == []


def test_order_event_lines_excludes_expired_acked_and_autoresolved():
    """判定语义在 order_events.py（单测见 test_order_events.py），这里钉接线：

    2026-09-18 的两个止损卖单失败事件周五写的，周末没有新写入触不到写侧惰性
    清理，在 09-19/09-20 两个凌晨被重播——过期（24h 窗口与写侧同源）、已签收、
    账本已无持仓的卖域事件都不该再上告警面；持仓仍在的必须保留。
    """
    now = _bj(2026, 9, 20, 14, 0)
    doc = {
        "unfilled:300408.SZ:2026-09-18": {          # 过期（49h 前）
            "ts": "2026-09-18T10:22:03+08:00", "kind": "unfilled", "code": "300408.SZ",
            "side": "sell", "msg": "300408卖未成", "alert": True},
        "unfilled:600176.SH:2026-09-19": {          # 已签收
            "ts": "2026-09-19T16:08:00+08:00", "kind": "unfilled", "code": "600176.SH",
            "side": "sell", "msg": "600176卖未成", "alert": True,
            "resolved_ts": "2026-09-20T09:00:00+08:00", "resolved_by": "zbox"},
        "sell_failed:002074.SZ:2026-09-20": {       # 卖域 + 账本已无持仓 → 自动消解
            "ts": "2026-09-20T10:00:00+08:00", "kind": "sell_failed", "code": "002074.SZ",
            "side": "", "msg": "002074下单失败", "alert": True},
        "unfilled:603213.SH:2026-09-20": {          # 持仓仍在 → 继续告警
            "ts": "2026-09-20T11:00:00+08:00", "kind": "unfilled", "code": "603213.SH",
            "side": "sell", "msg": "603213卖未成", "alert": True},
    }
    ledger = {"version": 1, "agents": {"a": {"positions": {"603213.SH": {"volume": 700}}}}}

    assert A.order_event_lines(doc, ledger=ledger, now=now) == [
        "unfilled:603213.SH:2026-09-20|603213卖未成"]


def test_order_events_cli_uses_ledger_argv(tmp_path, capsys):
    """alert.sh 传 argv[2]=账本：无账本时保守全报；账本已无持仓的卖域事件自动消解。"""
    now = datetime.now(BJ)          # main 内部取真实当前时间，事件必须贴近当下
    events = tmp_path / "live_order_events.json"
    events.write_text(json.dumps({
        "unfilled:600176.SH:今天": {"ts": now.isoformat(), "kind": "unfilled",
                                    "code": "600176.SH", "side": "sell",
                                    "msg": "600176卖未成", "alert": True},
        "unfilled:002074.SZ:今天": {"ts": now.isoformat(), "kind": "unfilled",
                                    "code": "002074.SZ", "side": "sell",
                                    "msg": "002074卖未成", "alert": True},
    }, ensure_ascii=False), encoding="utf-8")
    ledger = tmp_path / "live_ledger.json"
    ledger.write_text(json.dumps({"version": 1, "agents": {
        "a": {"positions": {"002074.SZ": {"volume": 300}}}}}, ensure_ascii=False),
        encoding="utf-8")

    assert A.main(["order_events", str(events)]) == 0
    assert "600176卖未成" in capsys.readouterr().out          # 无账本 → 不猜

    assert A.main(["order_events", str(events), str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "002074卖未成" in out and "600176卖未成" not in out


# ---------- 延期单滞留（减仓意图静默消失的兜底告警）----------
# 背景：replay_deferred.py 有两条静默路径——24h 到期丢弃、被在途单无限期挡住。
# 两者都意味着「一笔减仓永远没执行而无人知晓」，这里把它变成可见告警。

def _defer(ts="2026-09-11T08:00:00", code="600309.SH"):
    return {"agent": "glm-5.3-flash", "side": "sell", "code": code,
            "volume": 100, "reason": "断开", "ts": ts}


def test_deferred_stuck_quiet_when_nothing_pending(tmp_path):
    """无延期单、无作废 → 不输出（alert.sh 按空串判定）。"""
    assert A.deferred_stuck({"deferred": []}, _bj(2026, 9, 11, 10, 20),
                            expired_path=tmp_path / "nope.jsonl") is None


def test_deferred_stuck_counts_stuck_in_trading_window(tmp_path):
    """盘中滞留 >2h → 报滞留数 + 最久小时。"""
    now = _bj(2026, 9, 11, 11, 0)          # 距 08:00 整 3h
    out = A.deferred_stuck({"deferred": [_defer()]}, now,
                           expired_path=tmp_path / "nope.jsonl")

    assert out == "1|3.0|0"


def test_deferred_stuck_ignores_fresh_orders_and_outside_window(tmp_path):
    """刚延期的（<2h）不算滞留；盘外不算滞留（隔夜天然存在）。"""
    now = _bj(2026, 9, 11, 10, 0)
    assert A.deferred_stuck({"deferred": [_defer(ts="2026-09-11T09:30:00")]}, now,
                            expired_path=tmp_path / "nope.jsonl") is None
    after = _bj(2026, 9, 11, 20, 0)        # 盘后
    assert A.deferred_stuck({"deferred": [_defer()]}, after,
                            expired_path=tmp_path / "nope.jsonl") is None
    assert A.deferred_stuck({"deferred": [_defer()]}, _bj(2026, 9, 12, 10, 30),
                            expired_path=tmp_path / "nope.jsonl") is None   # 周六


def test_deferred_stuck_reports_today_expiries_only(tmp_path):
    """作废单只在当日计入——这是已确定没执行的减仓，必须让人看见。"""
    exp = tmp_path / "deferred_expired.jsonl"
    exp.write_text(
        json.dumps({"ts": "2026-09-11T09:05:00", "code": "600309.SH"}) + "\n"
        + json.dumps({"ts": "2026-09-11T14:05:00", "code": "001312.SZ"}) + "\n"
        + json.dumps({"ts": "2026-09-10T14:05:00", "code": "旧单"}) + "\n"
        + "不是 json\n",
        encoding="utf-8")

    out = A.deferred_stuck({"deferred": []}, _bj(2026, 9, 11, 16, 0), expired_path=exp)

    assert out == "0|0|2"


def test_deferred_stuck_cli_prints_combined_line(tmp_path, capsys, monkeypatch):
    led = tmp_path / "live_ledger.json"
    led.write_text(json.dumps({"deferred": [_defer(ts="2026-09-11T08:00:00")]}),
                   encoding="utf-8")
    monkeypatch.setattr(A, "DEFER_EXPIRED_JSONL", tmp_path / "nope.jsonl")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _bj(2026, 9, 11, 10, 30)

    monkeypatch.setattr(A, "datetime", _FrozenDatetime)

    assert A.main(["deferred_stuck", str(led)]) == 0
    assert capsys.readouterr().out.strip() == "1|2.5|0"


def test_deferred_stuck_tolerates_garbage(tmp_path):
    assert A.deferred_stuck(None, _bj(2026, 9, 11, 10, 0),
                            expired_path=tmp_path / "nope.jsonl") is None
    assert A.deferred_stuck({"deferred": ["不是字典", {"ts": "坏时间"}]},
                            _bj(2026, 9, 11, 10, 0),
                            expired_path=tmp_path / "nope.jsonl") is None


# ---------- 循环熔断（当日禁止新开仓的无声状态 → 必须上告警面） ----------
# 2026-09-18 前：live_breaker 触发只落 logs/breaker/{day}.json，alert.sh 无接线
# ——某 agent 全天禁止买入，而人不知道。这里把它变成可见告警（按 agent 去重）。

def test_breaker_lines_reports_trips():
    doc = {"deepseek-v4-pro": {"ts": "2026-09-18T10:30:00+08:00",
                               "reason": "当日已实现亏损 ¥5,200 ≥ 5%×日初权益"}}

    assert A.breaker_lines(doc) == [
        "deepseek-v4-pro|当日已实现亏损 ¥5,200 ≥ 5%×日初权益|2026-09-18T10:30:00+08:00"]


def test_breaker_lines_tolerates_garbage_and_empty():
    assert A.breaker_lines(None) == []
    assert A.breaker_lines({}) == []
    assert A.breaker_lines({"x": "不是字典"}) == []
    # 缺 reason/ts 也要报（熔断事实不因字段缺失而消失），空字段留白
    assert A.breaker_lines({"glm-5.3-flash": {}}) == ["glm-5.3-flash||"]


def test_cli_shim_breaker_prints_lines(tmp_path, capsys):
    p = tmp_path / "breaker.json"
    p.write_text(json.dumps({"a": {"ts": "T", "reason": "R"}}), encoding="utf-8")

    assert A.main(["breaker", str(p)]) == 0
    assert capsys.readouterr().out.strip() == "a|R|T"


def test_cli_shim_breaker_quiet_when_no_trip(tmp_path, capsys):
    assert A.main(["breaker", str(tmp_path / "absent.json")]) == 0
    assert capsys.readouterr().out == ""


# ---------- 磁盘水位（2026-09-21 盘满事故：症状一堆、源头零告警） ----------
# ENOSPC 15:43-17:44 北京：rsyslog 266 万行错误刷爆 journal、心跳写失败被误判
# 「主机离线 177 分钟」、alerts.log 丢块、14:45 尾盘决策轮中断——全是下游症状，
# 没有一条告警指向磁盘本身。阈值 88%：距 100% 留出数小时清理窗口。


class _U:                       # 伪 shutil.disk_usage 返回值（字节）
    """reserved_gb = ext4 根保留块等**谁都写不进去**的空间：它计入 total，
    但既不算 used 也不算 free。默认 0 = 无保留块（此时两种口径重合）。

    free 夹在 0：真实 f_bavail 不会为负，替身也不该造出「剩余 -1GB」这种不可能态
    当成断言的基准（会掩盖文案缺陷）。
    """

    def __init__(self, total_gb: float, used_pct: float, reserved_gb: float = 0.0):
        self.total = int(total_gb * (1 << 30))
        self.used = int(self.total * used_pct)
        self.free = max(0, self.total - self.used - int(reserved_gb * (1 << 30)))


class _Uv:                      # 直接注入 used/free 原始字节（造 inf/nan 这类怪值用）
    def __init__(self, used, free):
        self.total = 0
        self.used = used
        self.free = free


def test_disk_water_quiet_below_threshold():
    assert A.disk_water(usage=_U(937, 0.50)) is None
    assert A.disk_water(usage=_U(937, 0.879)) is None


def test_disk_water_warns_with_pct_and_free_gb():
    msg = A.disk_water(usage=_U(937, 0.90))
    assert msg is not None and "90%" in msg and "磁盘" in msg and "GB" in msg


def test_disk_water_critical_wording_and_degenerate_usage_safe():
    assert "将满" in A.disk_water(usage=_U(937, 0.97))
    assert A.disk_water(usage=_U(0, 0.0)) is None      # 全零：不炸、不报


def test_disk_water_counts_writable_space_not_raw_total():
    """口径 = used/(used+free)，根保留块不算可写空间。

    本机 2026-09-22 实测同一块盘：used/total=87.25%（沉默）而 used/(used+free)=91.92%
    （越线）——差的 47.7G 是 ext4 给 root 的 5% 保留块，我们（非 root 的写入方）写不进去。
    旧口径不是全盲（non-root 的 ENOSPC 在 used/total≈95% 才发生，那时旧口径也会报），
    但同样的 88% 阈值下真实可写余量只剩 ~65G，预警带被压缩；换分母后 ~107G。
    """
    # 937G / 用 86% / 保留 48G → 可写口径 90.6%（报），而 used/total=86%（旧口径沉默）
    msg = A.disk_water(usage=_U(937, 0.86, reserved_gb=48))
    assert msg is not None and "91%" in msg
    # 反向：水位真的低时，换分母也不能把正常盘报成告警
    assert A.disk_water(usage=_U(937, 0.50, reserved_gb=48)) is None
    # 跃迁点（这一对才是「分母确实是 used+free」的硬证据）：保留 48G 时可写口径的 88%
    # 落在 raw 83.5%——raw 还差 4.5 个点没到线，新口径必须已经红；83.0% 则必须还绿。
    assert A.disk_water(usage=_U(937, 0.836, reserved_gb=48)) is not None
    assert A.disk_water(usage=_U(937, 0.830, reserved_gb=48)) is None


def test_disk_water_nonfinite_values_get_no_verdict():
    """inf/nan **能过 float()**（`float("inf")` 不抛，Decimal("Infinity") 同理）——
    不显式堵住就会算出 nan% 并推「磁盘水位高 nan%（剩余 infGB）」上 QQ。

    生产不可达（statvfs 给不出 inf/nan），但注释声称覆盖了 Infinity，行为和注释必须一致：
    判不出=n 无告警（而不是拿荒谬结论当告警）。
    """
    for used, free in ((float("inf"), float("inf")), (float("inf"), 0.0),
                       (float("nan"), 1.0), (float("-inf"), 1.0),
                       ("inf", "0"), (1e308, 1e308)):
        assert A.disk_water(usage=_Uv(used, free)) is None, (used, free)


def test_disk_water_says_the_metric_out_loud():
    """文案必须自报口径，否则「92%」会被读成「盘用了 92%」（实际可写空间用了 92%）。"""
    assert "可写空间" in A.disk_water(usage=_U(937, 0.90))


def test_disk_water_degenerate_writable_space_is_silent():
    """used+free=0（取不到数/怪值）：分母为 0 → 不炸、不报，而不是 ZeroDivisionError。"""
    assert A.disk_water(usage=_U(0, 0.0)) is None
    # 边界另一侧：可写空间被吃光（free=0）是真·满盘，必须报「将满」而不是沉默
    assert "将满" in A.disk_water(usage=_U(937, 1.0))


def test_cli_shim_disk_water_needs_no_path_arg(capsys):
    """alert.sh 的调用不带 path（argv 只有一项）——共用 main 不能 IndexError。"""
    assert A.main(["disk_water"]) == 0


# ---------- CLI 参数契约：缺输入文件 ≠ 无故障 ----------

def test_cli_refuses_to_report_healthy_without_its_input_path(capsys):
    """缺参数必须 rc≠0。

    旧实现 `path = argv[1] if len(argv) > 1 else ""` + `_load("")` → 空文档 → 空输出
    + rc=0，与「真没故障」完全同形：alert.sh 里一个路径被改坏/漏传，那条检查就永久
    静默失效，而告警面全绿（2026-09-22 加的 acheck 只看得见 rc≠0，看不见这一种）。
    """
    for check in sorted(A.PATH_REQUIRED):
        assert A.main([check]) == 2, f"{check} 缺参数竟然报「无故障」"
    assert "缺少输入文件参数" in capsys.readouterr().err


def test_cli_path_required_list_covers_the_whole_dispatcher():
    """新增检查时必须在 PATH_REQUIRED 里表态——漏一个就是漏一个静默洞。"""
    import re
    from pathlib import Path as _P

    src = (_P(A.__file__)).read_text(encoding="utf-8")
    names = set(re.findall(r'elif check == "([a-z_]+)"', src))
    assert names, "没解析到任何检查名（分发器结构变了？）"
    assert names - set(A.PATH_REQUIRED) == {"disk_water"}, \
        f"这些检查既不在 PATH_REQUIRED 也不是 disk_water: {names - set(A.PATH_REQUIRED)}"


def test_cli_provided_but_missing_file_still_reads_as_no_alarm(tmp_path):
    """参数**给了**、文件不存在 → 仍是「无故障」（各检查自己的语义，本批不动）。"""
    assert A.main(["l2_minute", str(tmp_path / "absent.json")]) == 0


# ---------- 看板自身的新鲜度（2026-09-22 二轮审查 HIGH-1） ----------
#
# `rt_down` 里原来写着「缺该路（老看板/探针停更）不报——停更由别的检查兜」。
# 全仓核验后那句话是**假的**：logs/rt_status.json 的消费者只有 rt_down /
# account_down（都不看 mtime）与 daily_report_agent（只读 account 段内容），
# 没有任何一处检查它的新鲜度。于是「探针写不出板子」= 六路告警面整体静默冻结，
# 从表面完全看不出来——正是本批次要消灭的最后一条假绿路径。

class TestRtStale:
    def test_fresh_board_is_silent(self):
        now = _bj(2026, 9, 22, 10, 30)
        assert A.rt_stale_line(now, now.timestamp() - 60) is None

    def test_missing_board_is_reported(self):
        line = A.rt_stale_line(_bj(2026, 9, 22, 10, 30), None)
        assert line and "不存在" in line or "缺失" in line, line

    def test_stale_board_names_the_age_and_the_consequence(self):
        now = _bj(2026, 9, 22, 10, 30)
        line = A.rt_stale_line(now, now.timestamp() - 20 * 60)
        assert line and "20 分钟" in line, line
        assert "六路" in line or "全部" in line, "要说清后果：冻在上一份板上"

    def test_future_mtime_is_not_fresh_forever(self):
        """时钟回拨/未来 mtime 不许被当成新鲜（同 last_ok_ts 的未来时刻规则）。"""
        now = _bj(2026, 9, 22, 10, 30)
        assert A.rt_stale_line(now, now.timestamp() + 3600) is not None

    def test_cli_shim_rt_stale_prints_one_line(self, tmp_path, capsys):
        p = tmp_path / "rt_status.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (time.time() - 3600, time.time() - 3600))

        assert A.main(["rt_stale", str(p)]) == 0

        assert "分钟" in capsys.readouterr().out
