"""执行链路加固批（2026-09-18）单元测试。

覆盖四项：
  1. 轮心跳 + 轮预算（live_hourly_analysis.round_heartbeat / round_trace /
     ROUND_BUDGET_MIN 超时不再开新 agent）；
  2. alert_checks.round_stuck（心跳停滞判定）；
  3. 决策时效（decision_age_min / drop_stale_decisions）；
  4. 新闻游标（state_after_run advance=False：桶失败不推进 last_end）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import alert_checks as AC  # noqa: E402
import live_ledger as L  # noqa: E402

BJ = timezone(timedelta(hours=8))
AGENT = "deepseek-v4-pro"
CODE = "001312.SZ"


# ---------- 1. 轮心跳 / 轮预算 ----------

def test_round_heartbeat_writes_and_trace_marks_failed(monkeypatch, tmp_path):
    import live_hourly_analysis as H

    rf = tmp_path / "round.json"
    monkeypatch.setattr(H, "ROUND_FILE", rf)

    with pytest.raises(RuntimeError):
        with H.round_trace():
            raise RuntimeError("boom")

    doc = json.loads(rf.read_text(encoding="utf-8"))
    assert doc["running"] is False and doc["phase"] == "failed" and doc["pid"]


def test_run_analysis_over_budget_skips_agents(monkeypatch, tmp_path, capsys):
    import live_hourly_analysis as H
    import live_trade_picks as TP
    import prompts.analysis_modes as AM

    rf = tmp_path / "round.json"
    monkeypatch.setattr(H, "ROUND_FILE", rf)
    monkeypatch.setattr(H, "ROUND_BUDGET_MIN", -1)      # 预算恒超 → 首个 agent 前 break
    monkeypatch.setattr(AM, "rotated_modes", lambda agent: [])
    monkeypatch.setattr(H, "record_equity", lambda *a, **k: None)
    monkeypatch.setattr(H, "market_data_stale", lambda broker: False)
    monkeypatch.setattr(H, "build_trade_recap", lambda agent: "")
    monkeypatch.setattr(H, "load_review_recap", lambda agent: "")
    monkeypatch.setattr(H, "load_orderbook", lambda broker, codes: {})
    monkeypatch.setattr(L, "load_ledger", lambda: {"agents": {AGENT: {
        "virtual_cash": 50000.0,
        "positions": {CODE: {"volume": 100, "cost_price": 9.0,
                             "buy_ts": "2026-09-01T09:35:00+08:00"}}}}})

    class Broker:
        def _account_query(self):
            return {"asset": {"asset": 60000.0, "cash": 20000.0},
                    "positions": [{"stock_code": CODE, "total_volume": 100,
                                   "available_volume": 100, "cost_price": 9.0,
                                   "last_price": 10.0}]}

        def get_quote(self, code, _=""):
            return {"close": 10.0}

        def get_klines(self, code, interval="daily"):
            return [{"close": 10.0}, {"close": 10.0}]

    H.run_analysis(Broker(), "每小时定时", dry_run=True, agents=[AGENT])

    out = capsys.readouterr().out
    assert "本轮已超预算" in out                    # 预算闸触发（在 LLM 之前拦下）
    doc = json.loads(rf.read_text(encoding="utf-8"))
    assert doc["running"] is False                  # 轮已收尾（finally 写 done/failed）
    # 首个 agent 就被拦下：当日轮计数器不该被 bump（预算闸在 bump 与 LLM 之前）
    today = H.now_cn().strftime("%Y-%m-%d")
    assert H.rounds_today_of(H.load_state(), AGENT, today) == 0


# ---------- 2. round_stuck ----------

def _hb(updated_min_ago: float, running=True) -> dict:
    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    return {"running": running, "pid": 123, "phase": "agent:x",
            "start_ts": (now - timedelta(minutes=50)).isoformat(),
            "updated_ts": (now - timedelta(minutes=updated_min_ago)).isoformat()}


def test_round_stuck_quiet_when_fresh_or_not_running():
    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    assert AC.round_stuck(_hb(5), now) is None
    assert AC.round_stuck(_hb(25, running=False), now) is None
    assert AC.round_stuck({}, now) is None
    assert AC.round_stuck(None, now) is None


def test_round_stuck_reports_when_stale():
    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    line = AC.round_stuck(_hb(25), now)
    assert line and "心跳停滞 25 分钟" in line and "123" in line


# ---------- 3. 决策时效 ----------

def test_decision_age_and_missing_stamp(monkeypatch):
    import live_hourly_analysis as H

    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    monkeypatch.setattr(H, "now_cn", lambda: now)
    assert H.decision_age_min({"decided_at": (now - timedelta(minutes=7)).isoformat()}) \
        == pytest.approx(7, abs=0.1)
    assert H.decision_age_min({}) is None                 # 无戳 → 不设限
    assert H.decision_age_min({"decided_at": "垃圾"}) is None
    naive = (now - timedelta(minutes=3)).replace(tzinfo=None).isoformat()
    assert H.decision_age_min({"decided_at": naive}) == pytest.approx(3, abs=0.1)


def test_drop_stale_decisions_filters_and_logs(monkeypatch):
    import live_hourly_analysis as H
    import live_trade_picks as TP

    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    monkeypatch.setattr(H, "now_cn", lambda: now)
    logged = []
    monkeypatch.setattr(TP, "log_line", lambda doc: logged.append(doc))

    old = {"action": "buy", "code": "600000.SH",
           "decided_at": (now - timedelta(minutes=31)).isoformat(), "reason": "陈旧主题"}
    fresh = {"action": "sell", "code": CODE,
             "decided_at": (now - timedelta(minutes=5)).isoformat()}
    nostamp = {"action": "sell", "code": "600001.SH"}

    out = H.drop_stale_decisions([old, fresh, nostamp], AGENT)

    assert {d["code"] for d in out} == {CODE, "600001.SH"}
    assert len(logged) == 1 and logged[0]["mode"] == "decision_expired" \
        and logged[0]["code"] == "600000.SH" and logged[0]["age_min"] > 30


def test_drop_stale_decisions_dry_run_no_persist(monkeypatch):
    import live_hourly_analysis as H
    import live_trade_picks as TP

    now = datetime(2026, 9, 18, 10, 30, tzinfo=BJ)
    monkeypatch.setattr(H, "now_cn", lambda: now)
    logged = []
    monkeypatch.setattr(TP, "log_line", lambda doc: logged.append(doc))

    old = {"action": "sell", "code": CODE,
           "decided_at": (now - timedelta(minutes=45)).isoformat()}
    assert H.drop_stale_decisions([old], AGENT, persist=False) == []
    assert logged == []


# ---------- 4. 新闻游标 ----------

def test_news_cursor_holds_on_bucket_failure():
    import news_brief as nb

    st = {"last_end": "2026-09-17T19:05:00+08:00"}
    held = nb.state_after_run(st, nb.ALL_STAGES, "2026-09-18T10:21:00+08:00",
                              advance=False, last_empty="2026-09-18T10:21:00+08:00")
    assert held["last_end"] == st["last_end"]           # 桶失败 → 游标不推进
    assert held["last_empty"].startswith("2026-09-18")  # 但空窗事实照记

    moved = nb.state_after_run(st, nb.ALL_STAGES, "2026-09-18T10:21:00+08:00")
    assert moved["last_end"] == "2026-09-18T10:21:00+08:00"
