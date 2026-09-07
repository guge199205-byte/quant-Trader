"""断线窗口补跑：build_gap_note 提示注入 + 恢复判据辅助函数单测。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_gap_note.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_prompt_context as P  # noqa: E402
import live_hourly_analysis as L  # noqa: E402

HOLD = {"code": "600309.SH", "name": "万华化学"}


def _rows_file(snap_dir: Path):
    lines = [
        {"ts": "2026-09-07T11:05:00+08:00", "code": "600309.SH", "px": 100.0},
        {"ts": "2026-09-07T11:10:00+08:00", "code": "600309.SH", "px": 99.5},
        {"ts": "2026-09-07T11:15:00+08:00", "code": "600309.SH", "px": 101.2},
        {"ts": "2026-09-07T11:12:00+08:00", "code": "688183.SH", "px": 200.0},  # 别家持仓
        {"not-json": 1,},
    ]
    f = snap_dir / "2026-09-07.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")
    return f


# ---------- build_gap_note ----------

def test_gap_note_reports_window_rows(tmp_path):
    _rows_file(tmp_path)
    note = P.build_gap_note("2026-09-07T11:05:00+08:00",
                            "2026-09-07T11:20:00+08:00", [HOLD], snap_dir=tmp_path)
    assert "断线窗口" in note
    assert "万华化学" in note
    # 3 条记录：首 100.0 → 末 101.2（+1.20%），窗口内高/低
    assert "3 条" in note
    assert "+1.20%" in note
    assert "101.20" in note and "99.50" in note


def test_gap_note_no_rows_is_honest(tmp_path):
    _rows_file(tmp_path)
    note = P.build_gap_note("2026-09-07T09:30:00+08:00",
                            "2026-09-07T10:00:00+08:00", [HOLD], snap_dir=tmp_path)
    assert "无" in note or "缺失" in note
    assert "600309.SH" in note
    # 不臆造数字：窗口内确实没有记录
    assert "条" not in note.replace("无窗口内记录", "")


def test_gap_note_window_boundary_inclusive(tmp_path):
    _rows_file(tmp_path)
    # 终点取 11:15:00（含 11:15 那条）
    note = P.build_gap_note("2026-09-07T11:10:00+08:00",
                            "2026-09-07T11:15:00+08:00", [HOLD], snap_dir=tmp_path)
    assert "2 条" in note


def test_gap_note_skips_malformed_lines_and_other_codes(tmp_path):
    _rows_file(tmp_path)
    note = P.build_gap_note("2026-09-07T11:00:00+08:00",
                            "2026-09-07T11:20:00+08:00", [HOLD], snap_dir=tmp_path)
    assert "3 条" in note  # 坏行与别家代码都被跳过


def test_gap_note_naive_row_ts_assumed_beijing(tmp_path):
    snap_dir = tmp_path
    (snap_dir / "2026-09-07.jsonl").write_text(
        json.dumps({"ts": "2026-09-07T11:12:00", "code": "600309.SH", "px": 88.0}),
        encoding="utf-8")
    note = P.build_gap_note("2026-09-07T11:00:00+08:00",
                            "2026-09-07T11:20:00+08:00", [HOLD], snap_dir=snap_dir)
    assert "88.00" in note


def test_gap_note_empty_holdings(tmp_path):
    _rows_file(tmp_path)
    assert P.build_gap_note("2026-09-07T11:00:00+08:00",
                            "2026-09-07T11:20:00+08:00", [], snap_dir=tmp_path) == ""


def test_gap_note_instructions_present(tmp_path):
    _rows_file(tmp_path)
    note = P.build_gap_note("2026-09-07T11:00:00+08:00",
                            "2026-09-07T11:20:00+08:00", [HOLD], snap_dir=tmp_path)
    assert "watch" in note  # 跳变 → 挂 watch 等确认，而非盲目追/杀


# ---------- live_hourly_analysis 恢复判据辅助 ----------

def test_last_good_prefers_new_key():
    st = {"last_good_sample_ts": "2026-09-07T11:00:00+08:00",
          "last_sample_ts": "2026-09-07T09:00:00+08:00", "last_sample_asset": 0}
    assert L.last_good_sample_ts(st) == "2026-09-07T11:00:00+08:00"


def test_last_good_legacy_fallback_only_when_positive():
    # 旧格式：asset≤0（桥假活/断线）不算"数据好"，不能当恢复起点
    assert L.last_good_sample_ts({"last_sample_ts": "2026-09-07T09:00:00+08:00",
                                  "last_sample_asset": 0}) is None
    assert L.last_good_sample_ts({"last_sample_ts": "2026-09-07T09:00:00+08:00",
                                  "last_sample_asset": "123456.78"}) == "2026-09-07T09:00:00+08:00"


def test_last_good_missing():
    assert L.last_good_sample_ts({}) is None


def _cn(day, hm):
    return datetime.fromisoformat(f"2026-09-{day:02d}T{hm}:00+08:00")


def test_missed_sample_minutes_in_window():
    # 09:35 后断到 09:45：盘中每分钟都是应采样分钟 → 错失 10 个（09:36..09:45）
    m = L.missed_sample_minutes("2026-09-07T09:35:00+08:00", _cn(7, "09:45"))
    assert m == 10 and m >= L.RECOVERY_DOWN_MIN


def test_missed_sample_minutes_lunch_not_counted():
    # 午休 11:31-12:59 不是断线：11:30 采样后跨午休到 13:00，只"错失"13:00 本分钟
    m = L.missed_sample_minutes("2026-09-07T11:30:00+08:00", _cn(7, "13:00"))
    assert m < L.RECOVERY_DOWN_MIN  # 不会在下午开盘首分钟误触发补跑


def test_missed_sample_minutes_overnight_not_counted():
    # 隔夜正常：昨 15:10 收市采样后到今晨 09:30，只计今晨 09:25 之后未采样分钟
    m = L.missed_sample_minutes("2026-09-04T15:10:00+08:00", _cn(7, "09:30"))
    assert 5 <= m < L.RECOVERY_DOWN_MIN  # 09:25..09:29 已采样→0；全没采到→5


def test_missed_sample_minutes_bad_input():
    now = _cn(7, "11:30")
    assert L.missed_sample_minutes(None, now) == 0
    assert L.missed_sample_minutes("not-a-date", now) == 0
    assert L.missed_sample_minutes("2026-09-07T12:00:00+08:00", now) == 0  # last_good 在未来
