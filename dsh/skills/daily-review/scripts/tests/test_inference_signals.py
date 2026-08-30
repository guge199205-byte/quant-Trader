"""inference_signals 模块单元测试：run 选择 + 信号查询（mock PG，不连真库）。

验证：
- _latest_completed_run 按 created_at 取最新、精确匹配 data/prediction 日期
- _signals_for_run 按 fusion_score 排序、限条数
- load_prev_vs_today / load_next_top_n 主流程 + fallback 逻辑
- symbol 归一化（PG 纯数字 → suffix）
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inference_signals as ins  # noqa: E402


class FakeCur:
    """按 execute 顺序返回 preset 结果的假游标。

    plan 每一项对应一次 execute 的 fetchone/fetchall 返回值：
        tuple((...),)  → fetchone 返回内层元组
        tuple([...],)  → fetchall 返回内层列表
        (None,)        → fetchone 返回 None
    """

    def __init__(self, plan: list):
        self._plan = list(plan)
        self._result = None
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self.executed.append((sql, args))
        self._result = self._plan.pop(0) if self._plan else ((None,),)

    def fetchone(self):
        inner = self._result[0]
        return inner if inner is not None else None

    def fetchall(self):
        return self._result[0]


@pytest.fixture
def run_rows():
    def _mk(run_id, data, pred, ts):
        return ((run_id, "mdl_x", date(*data), date(*pred), ts),)

    return _mk


class TestLatestCompletedRun:
    def test_picks_latest_by_created_at(self, run_rows):
        cur = FakeCur([run_rows("run_2", (2026, 8, 20), (2026, 8, 21), "2026-08-21 10:12"),
                       run_rows("run_1", (2026, 8, 20), (2026, 8, 21), "2026-08-20 09:00")])
        run = ins._latest_completed_run(cur, "mdl_x", date(2026, 8, 20), date(2026, 8, 21))
        # 第一段查询按 created_at DESC LIMIT 1 → 应返回 run_2
        assert run["run_id"] == "run_2"
        assert run["data_trade_date"] == date(2026, 8, 20)

    def test_exact_prediction_match(self, run_rows):
        cur = FakeCur([run_rows("run_p", (2026, 8, 19), (2026, 8, 20), "2026-08-20 11:00")])
        run = ins._latest_completed_run(cur, "mdl_x", None, date(2026, 8, 20))
        assert run["run_id"] == "run_p"

    def test_no_match_returns_none(self):
        cur = FakeCur([(None,)])
        assert ins._latest_completed_run(cur, "mdl_x", date(2026, 8, 1), None) is None

    def test_constrains_columns_present(self, run_rows):
        cur = FakeCur([run_rows("run_c", (2026, 8, 20), (2026, 8, 21), "2026-08-21 10:12")])
        ins._latest_completed_run(cur, "mdl_x", date(2026, 8, 20), date(2026, 8, 21))
        sql = cur.executed[0][0]
        assert "model_id=%s" in sql
        assert "status='completed'" in sql
        assert "data_trade_date=%s" in sql
        assert "prediction_trade_date=%s" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT 1" in sql


class TestSignalsForRun:
    def test_sorted_and_limited(self):
        cur = FakeCur([
            ([(f"60{s:04d}", i, "HOLD") for s, i in ((0, 10), (1, 8), (2, 5), (3, 2))],),
        ])
        rows = ins._signals_for_run(cur, "run_x", 3)
        assert len(rows) == 3
        assert [r["fusion_score"] for r in rows] == [10.0, 8.0, 5.0]


class TestLoadPrevVsToday:
    def test_happy_path(self, run_rows):
        plan = [
            run_rows("run_v", (2026, 8, 19), (2026, 8, 20), "2026-08-20 08:00"),
            ([(symbol := "300502", 0.074, "HOLD"), ("688806", 0.044, "HOLD")],),
        ]
        cur = FakeCur(plan)
        conn = type("C", (), {"cursor": lambda self: cur, "close": lambda self: None})()
        out = ins.load_prev_vs_today("mdl_x", date(2026, 8, 20), conn=conn, top_n=20)
        assert out["run_id"] == "run_v"
        assert out["signals"][0]["symbol"] == "300502.SZ"
        assert out["signals"][1]["symbol"] == "688806.SH"

    def test_no_run_returns_none(self):
        cur = FakeCur([(None,)])
        conn = type("C", (), {"cursor": lambda self: cur, "close": lambda self: None})()
        assert ins.load_prev_vs_today("mdl_x", date(2026, 8, 20), conn=conn) is None


class TestLoadNextTopN:
    def test_fallback_to_latest_when_today_missing(self, run_rows):
        plan = [
            (None,),                       # data=trade_date/pred=next 无匹配
            run_rows("run_latest", (2026, 8, 20), (2026, 8, 21), "2026-08-21 10:12"),
            ([(symbol := "300502", 0.07, "HOLD")],),
        ]
        cur = FakeCur(plan)
        conn = type("C", (), {"cursor": lambda self: cur, "close": lambda self: None})()
        out = ins.load_next_top_n("mdl_x", date(2026, 8, 20), conn=conn)
        assert out["fallback"] is True
        assert out["signals"][0]["symbol"] == "300502.SZ"

    def test_happy_path_no_fallback(self, run_rows):
        plan = [
            run_rows("run_n", (2026, 8, 20), (2026, 8, 21), "2026-08-20 15:10"),
            ([(symbol := "000733", 0.041, "HOLD")],),
        ]
        cur = FakeCur(plan)
        conn = type("C", (), {"cursor": lambda self: cur, "close": lambda self: None})()
        out = ins.load_next_top_n("mdl_x", date(2026, 8, 20), conn=conn)
        assert out["fallback"] is False
        assert out["signals"][0]["symbol"] == "000733.SZ"