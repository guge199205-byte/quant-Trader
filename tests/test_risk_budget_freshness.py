"""风险预算新鲜度（2026-09-12 P2）：档位过期必须看得见。

`configs/risk_budget.json` 由 cron 每交易日 09:10 重定档，是**所有实盘入口共用**
的硬约束（live_hourly_analysis / live_llm_trade 都走 load_limits）。定档链路一
旦静默故障（cron 没跑 / 数据源取不到 / 写入失败），当天所有入口会沿用旧档位
——档位偏松时等于风控半失效，且没有任何暴露面。

口径（2026-09-18 机构级审计后**契约变更**）：
  - 只提醒不改行为的 fail-open **已废弃**：档位不可信（缺失/损坏/过期/结构异常）
    → 返回 `FALLBACK_LIMITS`（买入侧防守：单票 10% / 新开仓 1 只 / 单票上限 15%），
    与 decide_level 的 fail-safe 同方向——风控读不到就收紧，不是放宽；
  - **有意不含 leverage_max / leverage_trim_to**：强减是风险动作，数据故障不应
    触发强平；fail-safe 的方向是"限制新增风险"，不是"制造新动作"；
  - 应定档日 = 今天（交易日）否则最近一个交易日（周末/假期沿用上一交易日档位）。

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
          "leverage_trim_to": 1.15, "per_stock_pos_pct": 0.25}
#: 档位不可信时的买入侧回退（与 risk_budget_agent.FALLBACK_LIMITS 同源；2026-09-18）。
#: 过期分支 = 买入侧三键强制回退 + **杠杆/强减键取文件原值**（评审 H-1：初版让
#: 杠杆上限回落 1.5，是"故障放宽"，方向错误）。
FALLBACK = {"per_stock_pct": 0.10, "max_new_buys": 1, "per_stock_pos_pct": 0.15}
TIGHTENED = {**BUDGET, **FALLBACK}


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


# ---------- 过期/不可信：回退买入侧防守参数（fail-safe）+ 提醒 ----------

def test_stale_budget_tightens_buy_side_but_keeps_leverage(env, monkeypatch, capsys):
    """2026-09-18 契约变更：过期不再沿用旧档位买入侧，而是回退收紧；
    杠杆/强减键取文件原值（不因数据故障放宽、也不制造强平——评审 H-1）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", _doc(WED - timedelta(days=3)))

    lim = R.load_limits(p)

    assert lim == TIGHTENED
    assert lim["leverage_max"] == BUDGET["leverage_max"]       # 杠杆键取原值，未被放宽
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
    """老格式/写坏：没有日期字段同样算「不知道用的是哪天的档位」→ 买入侧回退。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"level": "caution", "budget": dict(BUDGET)})

    assert R.load_limits(p) == TIGHTENED
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True


def test_missing_new_key_fills_buy_side_but_still_alerts(env, monkeypatch):
    """写读键集漂移（2026-09-18 实况形态：新键 per_stock_pos_pct 已上线而
    实盘档位文件还是旧版四键）→ 缺的买入侧键按防守补齐**且必须留痕**。

    评审 MEDIUM：初版成功路径对缺键 `continue`，返回部分字典 → 新键静默不生效。
    """
    _freeze(monkeypatch, WED)
    doc = {"date": WED.isoformat(), "level": "caution",
           "budget": {k: v for k, v in BUDGET.items() if k != "per_stock_pos_pct"}}
    p = _write(env / "budget.json", doc)

    lim = R.load_limits(p)

    # 已有键照旧（当天档位真实值），唯缺键按防守补齐
    assert lim == {**{k: v for k, v in BUDGET.items() if k != "per_stock_pos_pct"},
                   "per_stock_pos_pct": FALLBACK["per_stock_pos_pct"]}
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "缺少键" in hits[0]["msg"] and "per_stock_pos_pct" in hits[0]["msg"]


def test_fallback_never_contains_leverage_keys():
    """钉死设计取舍：回退**只收紧买入侧**，不含杠杆/强减参数——数据故障不应
    触发强平（fail-safe 的方向是限制新增风险，不是制造新动作）。"""
    assert "leverage_max" not in R.FALLBACK_LIMITS
    assert "leverage_trim_to" not in R.FALLBACK_LIMITS
    assert R.FALLBACK_LIMITS["per_stock_pct"] <= 0.10
    assert R.FALLBACK_LIMITS["max_new_buys"] <= 1


def test_non_utf8_file_alerts_instead_of_raising(env, monkeypatch):
    """非 UTF-8 字节（写入侧是非原子写 + `ensure_ascii=False` 含中文，进程被杀/
    磁盘满可能截断在多字节字符中间）→ read_text 抛 UnicodeDecodeError。

    它是 ValueError 子类、**不是** OSError，只捕 OSError 会让它穿出 load_limits：
    无 print、无事件，退回「静默按默认档跑」——本函数要消的正是这个形态
    （2026-09-12 复审 LOW）。2026-09-18 起返回值改为 FALLBACK（回退收紧）。"""
    _freeze(monkeypatch, WED)
    p = env / "budget.json"
    p.write_bytes(b'{"date": "2026-09-09", "note": "\xe4\xb8')   # 多字节字符截断

    assert R.load_limits(p) == FALLBACK
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "UTF-8" in hits[0]["msg"]


def test_missing_file_falls_back_to_buy_side_tightening(env, monkeypatch):
    """文件缺失 → 回退 FALLBACK（买入侧收紧）并留痕——读不到档位不能再无声、
    也不能按比预算档松的调用方默认跑（2026-09-18 契约变更）。"""
    _freeze(monkeypatch, WED)

    assert R.load_limits(env / "absent.json") == FALLBACK
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
        assert R.load_limits(p) == FALLBACK

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
        assert R.load_limits(p) == FALLBACK

    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "结构异常" in hits[0]["msg"]


def test_non_dict_budget_field_alerts(env, monkeypatch):
    """doc 是 dict 但 budget 字段是 truthy 非 dict（`[1]`/`"x"`）→ 同样读不出档位，
    同样要看见（否则 `lv.get` 又是一次被吞掉的 AttributeError）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "budget": [1]})

    assert R.load_limits(p) == FALLBACK
    assert len(_stale_events(env / "events.json")) == 1


def test_missing_budget_field_alerts_even_with_fresh_date(env, monkeypatch):
    """日期新鲜但没有 budget 字段：结构性故障优先于日期判据被报出来
    （定档写入侧永远产出四个键，缺失即故障）。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "level": "calm"})

    assert R.load_limits(p) == FALLBACK
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True and "budget" in hits[0]["msg"]


def test_empty_budget_object_alerts(env, monkeypatch):
    """budget 是空对象（写入侧 bug 形态）→ 一个档位都取不到，与缺失同罪：
    旧行为返回 {} 却无声 = 当天按调用方默认档跑；现回退 FALLBACK。"""
    _freeze(monkeypatch, WED)
    p = _write(env / "budget.json", {"date": WED.isoformat(), "budget": {}})

    assert R.load_limits(p) == FALLBACK
    hits = _stale_events(env / "events.json")
    assert len(hits) == 1 and "可用" in hits[0]["msg"]
