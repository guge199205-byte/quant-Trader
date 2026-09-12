"""风险预算新鲜度（2026-09-12 P2）：档位过期必须看得见。

`configs/risk_budget.json` 由 cron 每交易日 09:10 重定档，是**所有实盘入口共用**
的硬约束（live_hourly_analysis / live_llm_trade 都走 load_limits）。定档链路一
旦静默故障（cron 没跑 / 数据源取不到 / 写入失败），当天所有入口会**无声地**沿用
旧档位——档位偏松时等于风控半失效，且没有任何暴露面。

口径（有意为止损面最小的设计）：
  - 只提醒，**不改行为**：过期照样返回档位（fail-open 维持，不阻断交易路径）；
  - 应定档日 = 今天（交易日）否则最近一个交易日（周末/假期沿用上一交易日档位）；
  - 文件缺失/损坏保持既有语义（返回 {}，由调用方各自默认），不在本次告警面内。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_risk_budget_freshness.py -q
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills as F  # noqa: E402
import risk_budget_agent as R  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")
WED = date(2026, 9, 9)     # 交易日（周三）
FRI = date(2026, 9, 11)    # 交易日（周五）
SAT = date(2026, 9, 12)    # 非交易日（周六）→ 应定档日 = 周五

BUDGET = {"leverage_max": 1.2, "per_stock_pct": 0.15, "max_new_buys": 2,
          "leverage_trim_to": 1.15}


def _doc(d: date) -> dict:
    return {"date": d.isoformat(), "level": "caution", "budget": dict(BUDGET)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """事件文件与「今天」都固定，档位文件由用例自备。"""
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    return tmp_path


def _freeze(monkeypatch, d: date) -> None:
    monkeypatch.setattr(R, "_bj_now",
                        lambda: datetime(d.year, d.month, d.day, 10, 0, tzinfo=CN))


def _events(path: Path) -> list:
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def _stale_events(path: Path) -> list:
    return [e for e in _events(path) if e["kind"] == "risk_budget_stale"]


def _write(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- 新鲜：照常返回、不打扰 ----------

def test_fresh_budget_on_trading_day_is_silent(env, monkeypatch):
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", _doc(WED))

    lim = R.load_limits(p)

    assert lim == BUDGET                                   # 档位原样
    assert _stale_events(env / "events.json") == []


def test_friday_budget_is_fresh_on_saturday(env, monkeypatch):
    """周末/假期沿用上一交易日档位：周五的档位对周六不算过期。"""
    _freeze(monkeypatch, SAT)
    p = _write(env / "budget.json", _doc(FRI))

    assert R.load_limits(p) == BUDGET
    assert _stale_events(env / "events.json") == []


# ---------- 过期：提醒但行为不变 ----------

def test_stale_budget_alerts_but_still_returns_limits(env, monkeypatch, capsys):
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", _doc(WED - timedelta(days=3)))

    lim = R.load_limits(p)

    assert lim == BUDGET                                   # fail-open：不阻断
    out = capsys.readouterr().out
    assert "风险预算过期" in out
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1
    assert hits[0]["alert"] is True                        # 必须上告警面
    assert "早于应定档日" in hits[0]["msg"]


def test_stale_budget_alert_is_once_per_day(env, monkeypatch):
    """档位被当天多个入口反复读取：同日只留一条事件（record_event 覆盖语义）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", _doc(WED - timedelta(days=1)))

    R.load_limits(p)
    R.load_limits(p)

    assert len(_stale_events(env / "events.json")) == 1


def test_missing_date_field_alerts(env, monkeypatch):
    """老格式/写坏：没有日期字段同样算「不知道用的是哪天的档位」。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"level": "caution", "budget": dict(BUDGET)})

    assert R.load_limits(p) == BUDGET
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True


def test_missing_file_keeps_old_semantics(env, monkeypatch):
    """文件缺失维持 {} 语义（调用方各自默认），不在本次告警面内。"""
    _freeze(monkeypatch, WED)

    assert R.load_limits(env / "absent.json") == {}
    assert _stale_events(env / "events.json") == []
