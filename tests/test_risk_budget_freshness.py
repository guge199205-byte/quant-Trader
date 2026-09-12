"""风险预算新鲜度（2026-09-12 P2）：档位过期必须看得见。

`configs/risk_budget.json` 由 cron 每交易日 09:10 重定档，是**所有实盘入口共用**
的硬约束（live_hourly_analysis / live_llm_trade 都走 load_limits）。定档链路一
旦静默故障（cron 没跑 / 数据源取不到 / 写入失败），当天所有入口会**无声地**沿用
旧档位——档位偏松时等于风控半失效，且没有任何暴露面。

口径（有意为止损面最小的设计）：
  - 只提醒，**不改行为**：过期照样返回档位（fail-open 维持，不阻断交易路径）；
  - 应定档日 = 今天（交易日）否则最近一个交易日（周末/假期沿用上一交易日档位）；
  - 文件缺失/损坏/结构异常同样返回 {}（由调用方各自默认），**但同样要告警**
    （2026-09-12 审查 LOW）：读不到档位 = 当天按调用方硬编码默认跑（1.5/20%，
    比预算档松），这正是「静默半失效」；旧实现只在 return {} 之前漏掉告警调用。

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
    assert "风险预算异常" in out
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


def test_non_utf8_file_alerts_instead_of_raising(env, monkeypatch):
    """非 UTF-8 字节（写入侧是非原子写 + `ensure_ascii=False` 含中文，进程被杀/
    磁盘满可能截断在多字节字符中间）→ read_text 抛 UnicodeDecodeError。

    它是 ValueError 子类、**不是** OSError，只捕 OSError 会让它穿出 load_limits：
    无 print、无事件，退回「静默按默认档跑」——本函数要消的正是这个形态
    （2026-09-12 复审 LOW）。"""
    _freeze(monkeypatch, WED)
    p = env / "budget.json"
    p.write_bytes(b'{"date": "2026-09-09", "note": "\xe4\xb8')   # 多字节字符截断

    assert R.load_limits(p) == {}
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "UTF-8" in hits[0]["msg"]


def test_missing_file_keeps_empty_semantics_but_alerts(env, monkeypatch):
    """文件缺失维持 {} 语义（调用方各自默认），但必须留痕——读不到档位 =
    当天按调用方默认档跑（可能比预算档松），不能再无声（2026-09-12 审查 LOW）。"""
    _freeze(monkeypatch, WED)

    assert R.load_limits(env / "absent.json") == {}
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "不存在" in hits[0]["msg"] or "不可读" in hits[0]["msg"]


def test_corrupt_file_alerts_instead_of_silent_default(env, monkeypatch):
    """写坏/写空（磁盘满、进程被杀）→ 解析失败同样要上告警面。

    旧实现在 `except (OSError, ValueError): return {}` 里直接返回，`_warn_if_stale`
    永不执行——P2 想消除的「定档链路静默故障」恰恰漏了这一形态。"""
    _freeze(monkeypatch, WED)
    p = env / "budget.json"

    for bad in ("", "{不是 JSON", '{"date":'):     # 空文件 / 垃圾 / 截断
        p.write_text(bad, encoding="utf-8")
        assert R.load_limits(p) == {}

    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True      # 同日一条（覆盖语义）
    assert "JSON" in hits[0]["msg"]


def test_non_dict_doc_does_not_raise_and_alerts(env, monkeypatch):
    """顶层非 dict（JSON 数组/数字/字符串）→ 不许抛 AttributeError。

    旧实现 `doc.get("budget")` 在 isinstance 兜底（现 298 行）**之前**执行，非 dict
    直接抛异常，被两个调用点的 `except Exception` 静默吞掉 → 无告警、按松默认跑，
    兜底那行成了死代码。"""
    _freeze(monkeypatch, WED)

    for bad in ([1, 2], 5, "oops"):                # 都是合法 JSON、都不是 dict
        p = env / "budget.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        assert R.load_limits(p) == {}

    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "结构异常" in hits[0]["msg"]


def test_non_dict_budget_field_alerts(env, monkeypatch):
    """doc 是 dict 但 budget 字段是 truthy 非 dict（`[1]`/`"x"`）→ 同样读不出档位，
    同样要看见（否则 `lv.get` 又是一次被吞掉的 AttributeError）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "budget": [1]})

    assert R.load_limits(p) == {}
    assert len(_stale_events(env / "events.json")) == 1


def test_missing_budget_field_alerts_even_with_fresh_date(env, monkeypatch):
    """日期新鲜但没有 budget 字段：结构性故障优先于日期判据被报出来
    （定档写入侧永远产出四个键，缺失即故障）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "level": "calm"})

    assert R.load_limits(p) == {}
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True and "budget" in hits[0]["msg"]


def test_empty_budget_object_alerts(env, monkeypatch):
    """budget 是空对象（写入侧 bug 形态）→ 一个档位都取不到，与缺失同罪：
    返回 {} 却无声 = 当天按调用方默认档跑。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "budget": {}})

    assert R.load_limits(p) == {}
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and "可用" in hits[0]["msg"]
