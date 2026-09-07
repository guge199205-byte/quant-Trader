"""波动触发防护单测：坏 tick 丢弃 / 好 tick 保留 / 同向冷却 / 反向不受限。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_volatility_guard.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_hourly_analysis as L  # noqa: E402

ROWS = [{"code": "688183.SH", "name": "生益电子", "price": 118.3,
         "pnl_pct": -11.5, "day_chg": -5.13}]


class FakeBroker:
    def get_klines(self, *a, **k):
        return []


def setup_module(module):
    module._orig_state = L.STATE_PATH
    module._orig_rows, module._orig_qf = L.build_rows, L.quote_fallback
    L.STATE_PATH = Path("/tmp/test_state_guard.json")
    L.build_rows = lambda broker, positions, names: [dict(r) for r in ROWS]
    L.MIN_ANALYSIS_INTERVAL_MIN = 0


def teardown_module(module):
    L.STATE_PATH = module._orig_state
    L.build_rows, L.quote_fallback = module._orig_rows, module._orig_qf
    Path("/tmp/test_state_guard.json").unlink(missing_ok=True)


def _set_state(d):
    Path("/tmp/test_state_guard.json").write_text(json.dumps(d), encoding="utf-8")


def _base():
    _set_state({"last_pnl": {"688183.SH": -5.0}, "last_day": {}, "last_ts": ""})


def test_bad_tick_dropped(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 131.5)  # 偏差 11%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_good_tick_kept(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)  # 偏差 0.17%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])


def test_same_direction_cooldown(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    # 60 分钟内同向再触发 → 冷却丢弃
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_reverse_direction_not_cooled(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    _set_state({"last_pnl": {"688183.SH": -20.0}, "last_day": {}, "last_ts": "",
                "last_trigger": {"688183.SH": {"dir": "down", "ts": now.isoformat()}}})
    r = L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    assert r and "盈亏" in r  # 反向（盈亏转好）不受冷却限制，pnl 触发保留


# ---------- 方案 C v2：板块联动 / L2 资金流 / 新闻持仓信号 ----------

def test_sector_trigger_delta_and_abs():
    ctx = {"position_sectors": {"600309.SH": "881034.SH"},
           "sectors": {"881034.SH": {"name": "化学制品", "chg_pct": 2.0}}}
    # 变化 5pp ≥3pp → 触发
    hits = L.sector_triggers(ctx, {"881034.SH": -3.0}, ["600309.SH"])
    assert hits and hits[0][2].startswith("板块联动")
    # 变化 1pp 但绝对 ≥5% → 触发
    ctx["sectors"]["881034.SH"]["chg_pct"] = 6.0
    hits2 = L.sector_triggers(ctx, {"881034.SH": 5.0}, ["600309.SH"])
    assert hits2 and "板块绝对涨跌" in hits2[0][2]
    # 变化 1pp、绝对 2% → 不触发
    ctx["sectors"]["881034.SH"]["chg_pct"] = 2.0
    assert L.sector_triggers(ctx, {"881034.SH": 1.0}, ["600309.SH"]) == []
    # 持仓无板块映射 → 不触发
    assert L.sector_triggers(ctx, {}, ["600309.SH"]) == []


def test_l2_trigger_inout_delta():
    factors = {"600309.SH": {"factors": {"inout": 0.5}}}
    assert L.l2_triggers(factors, {"600309.SH": 0.1}, ["600309.SH"])  # 0.4 ≥0.3
    assert not L.l2_triggers(factors, {"600309.SH": 0.4}, ["600309.SH"])  # 0.1 <0.3
    assert not L.l2_triggers(factors, {}, ["600309.SH"])  # 无基线不触发
    assert not L.l2_triggers({}, {"600309.SH": 0.1}, ["600309.SH"])  # 无数据


def test_news_trigger_impact_and_dedup():
    brief = {"ts": "2026-09-07T14:42:00+08:00",
             "holdings": [{"code": "600309.SH", "name": "万华化学", "verdict": "利空",
                           "impact": -1.0, "event_type": "监管"}]}
    hits, key = L.news_triggers(brief, "")
    assert hits and "impact-1" in hits[0][2] and key == brief["ts"]
    # 同一分子不重复唤醒
    hits2, _ = L.news_triggers(brief, key)
    assert hits2 == []
    # impact 0 不触发
    brief2 = {"ts": "x2", "holdings": [{"code": "c", "impact": 0}]}
    assert L.news_triggers(brief2, "")[0] == []


def test_check_volatility_v2_sources_wired():
    # 三源函数已接入 check_volatility（源码级确认接线存在）
    import inspect
    src = inspect.getsource(L.check_volatility)
    assert "sector_triggers(" in src and "l2_triggers(" in src and "news_triggers(" in src
