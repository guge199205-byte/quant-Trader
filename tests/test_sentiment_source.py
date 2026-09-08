"""情绪温度取数修复（2026-09-08）：分页口径 + 盘前回退盘中记录。

两个实测 bug：
1. Fuyao 涨停池 item 被默认分页截断在 50 条，`len(items)` 把 72 家读成 50 家，
   「过热 >90」阈值永远够不着；
2. 涨停池是盘中时点数据，盘前（09:10 定档）查询结构性为空 → 每天记 0 家 →
   风控永久判定"情绪冰点" → 永久谨慎档（杠杆/单票上限/新开仓数全被压）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_sentiment_source.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import risk_budget_agent as R  # noqa: E402


def _payload(items: int, total=None) -> dict:
    """Fuyao 响应骨架：item 长度 = 本页条数，total = 全市场真实家数。"""
    d = {"item": [{"code": f"60000{i}.SH"} for i in range(items)]}
    if total is not None:
        d["pagination"] = {"total": total, "pages": 2, "size": 50, "page": 1}
    return {"code": 0, "data": d}


# ---------------------------------------------------------------- 分页口径

def test_pagination_total_wins_over_truncated_items():
    """72 家 vs 本页 50 条——必须读 total（本次修复的核心）。"""
    assert R.pool_count(_payload(50, total=72)) == 72


def test_falls_back_to_len_when_pagination_absent():
    """天梯等端点无 pagination 字段 → 退回本页条数。"""
    assert R.pool_count(_payload(30)) == 30


def test_true_zero_is_zero_not_none():
    """跌停池 0 家是合法观测（≠ 取数失败），不能混淆。"""
    assert R.pool_count(_payload(0, total=0)) == 0


def test_garbage_payload_is_none():
    for bad in ({}, {"data": None}, {"data": {}}, {"data": {"item": "x"}},
                {"data": {"item": []}}):
        assert R.pool_count(bad) is None


def test_negative_total_ignored():
    assert R.pool_count(_payload(3, total=-1)) == 3


# ---------------------------------------------------------------- 观测落盘

def test_record_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "sentiment_zt.jsonl")
    R.record_sentiment(72, 5, ts="2026-09-07T14:30:00")
    R.record_sentiment(31, 3, ts="2026-09-08T10:05:00")
    rec = R._last_recorded()
    assert rec["zt"] == 31 and rec["max_ladder"] == 3 and rec["date"] == "2026-09-08"


def test_bad_lines_skipped_on_read(tmp_path, monkeypatch):
    f = tmp_path / "sentiment_zt.jsonl"
    f.write_text('not json\n{"zt": "x"}\n{"ts":"2026-09-08T09:40:00","date":"2026-09-08",'
                 '"zt":41,"max_ladder":4}\n', encoding="utf-8")
    monkeypatch.setattr(R, "SENTIMENT_LOG", f)
    assert R._last_recorded()["zt"] == 41


def test_no_record_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "nope.jsonl")
    assert R._last_recorded() is None


def test_record_ignores_none_value(tmp_path, monkeypatch):
    """取数失败（None）绝不能写成 0 家——那正是本次修复要消灭的假信号。"""
    f = tmp_path / "sentiment_zt.jsonl"
    monkeypatch.setattr(R, "SENTIMENT_LOG", f)
    R.record_sentiment(None, None)
    assert not f.exists()


# ---------------------------------------------------------------- 取数口径

def test_preopen_uses_last_intraday_record(tmp_path, monkeypatch):
    """09:10 定档：涨停池必然为空 → 引用最近一次盘中记录并标注日期。"""
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "sentiment_zt.jsonl")
    R.record_sentiment(72, 6, ts="2026-09-07T14:55:00")
    zt, ladder, src = R._sentiment(now=datetime(2026, 9, 8, 9, 10))
    assert (zt, ladder) == (72, 6)
    assert "2026-09-07" in src and "非今日实时" in src


def test_preopen_without_record_is_none_not_zero(tmp_path, monkeypatch):
    """无记录 → (None, None, "")，交给 decide_level 的 fail-safe，而不是谎报 0 家。"""
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "nope.jsonl")
    assert R._sentiment(now=datetime(2026, 9, 8, 9, 10)) == (None, None, "")


def test_intraday_live_and_recorded(tmp_path, monkeypatch):
    f = tmp_path / "sentiment_zt.jsonl"
    monkeypatch.setattr(R, "SENTIMENT_LOG", f)
    monkeypatch.setattr(R, "_fuyao_live", lambda: (88, 7))
    zt, ladder, src = R._sentiment(now=datetime(2026, 9, 8, 10, 30))
    assert (zt, ladder) == (88, 7) and "实时" in src
    assert json.loads(f.read_text(encoding="utf-8").strip())["zt"] == 88


def test_intraday_live_failure_falls_back_to_record(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "sentiment_zt.jsonl")
    R.record_sentiment(60, 4, ts="2026-09-08T09:35:00")
    monkeypatch.setattr(R, "_fuyao_live", lambda: None)
    zt, _, src = R._sentiment(now=datetime(2026, 9, 8, 10, 30))
    assert zt == 60 and "记录" in src


def test_weekend_never_calls_live(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "SENTIMENT_LOG", tmp_path / "nope.jsonl")

    def _boom():
        raise AssertionError("非交易日不应请求实时接口")

    monkeypatch.setattr(R, "_fuyao_live", _boom)
    assert R._sentiment(now=datetime(2026, 9, 6, 10, 30)) == (None, None, "")  # 周日


# ---------------------------------------------------------------- 连板天梯口径

def _ladder(rows: list) -> dict:
    return {"code": 0, "data": {"item": rows}}


def test_ladder_takes_latest_date_not_historical_max():
    """item 是 30 日分组行：必须取最新一天，否则历史高板串味（09-07 有 6 板 / 09-08 只有 4 板）。"""
    payload = _ladder([
        {"date": "2026-09-08", "boards": {"two_board": [{"board_num": 2}] * 12,
                                          "four_board": [{"board_num": 4}] * 3}},
        {"date": "2026-09-07", "boards": {"six_board": [{"board_num": 6}]}},
    ])
    assert R.ladder_max(payload) == 4


def test_ladder_explicit_date():
    payload = _ladder([
        {"date": "2026-09-08", "boards": {"four_board": [{"board_num": 4}]}},
        {"date": "2026-09-07", "boards": {"six_board": [{"board_num": 6}]}},
    ])
    assert R.ladder_max(payload, date="2026-09-07") == 6


def test_ladder_flat_shape_fallback():
    """端点若改回扁平列表（continue_day）仍要能读。"""
    assert R.ladder_max(_ladder([{"continue_day": 3}, {"continue_day": 5}])) == 5


def test_ladder_empty_or_garbage_is_none():
    for bad in ({}, {"data": None}, {"data": {"item": []}}, {"data": {"item": "x"}}):
        assert R.ladder_max(bad) is None


def test_ladder_no_boards_today_is_zero():
    """今天天梯为空（无连板）= 0，不是取数失败。"""
    assert R.ladder_max(_ladder([{"date": "2026-09-08", "boards": {"two_board": []}}])) == 0


# ---------------------------------------------------------------- fail-safe 联动

def test_missing_sentiment_degrades_to_caution_not_calm():
    """情绪取不到时不允许落到最松档（数据故障 ≠ 风平浪静）。"""
    lvl, reasons = R.decide_level(0.8, 1.0, None, None)
    assert lvl == "caution"
    assert any("情绪温度" in r for r in reasons)
