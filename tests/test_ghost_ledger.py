"""影子代价账（scripts/ghost_ledger.py + configs/gate_registry.json）。

P3 的唯一问题：**每条闸门拦下的东西，事后来看值多少钱？** 没有这个数，
"加闸"是纯信仰——只看得见拦掉了什么，看不见拦对了没有。

本文件钉住四类不变量（缺一条，影子账就会变成又一份自我合理化的叙事）：
  1. **记录层永不反噬主链路**：record_event 在任何异常下都返回 False，不抛；
  2. **同一事件只记一次**（幂等键含 rule——同一天同标的被两条规则拦是两件事）；
  3. **登记表与词表双向覆盖**：代码能发出的规则必须有依据/失效条件/复核日，
     登记表里也不许有已经不对应任何规则的死条目；
  4. **与决策池共用同一套远期收益引擎**（不另起一套口径）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_ghost_ledger.py -q
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules as GR  # noqa: E402
import ghost_ledger as GL  # noqa: E402


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- 幂等键 ----------

def test_id_stable_and_rule_sensitive():
    """同一天同标的被两条不同规则拦下是两件事，不能互相顶掉。"""
    a = GL.make_id("agent-a", "2026-09-19", "600000.SH", GR.CASH_INSUFFICIENT)

    assert a == GL.make_id("agent-a", "2026-09-19", "600000.SH", GR.CASH_INSUFFICIENT)
    assert a != GL.make_id("agent-a", "2026-09-19", "600000.SH", GR.LEV_OVER)
    assert a != GL.make_id("agent-b", "2026-09-19", "600000.SH", GR.CASH_INSUFFICIENT)
    assert a != GL.make_id("agent-a", "2026-09-20", "600000.SH", GR.CASH_INSUFFICIENT)


# ---------- 行构造（纯函数） ----------

def test_row_shape_carries_forward_engine_fields():
    r = GL.row("agent-a", "600000.SH", GR.CAP_POSITION, day="2026-09-18",
               ts="2026-09-18T10:00:00+08:00", pct=0.2, reason="集中度超限",
               ref_px=12.34, source="live_llm_trade")

    assert r["rule"] == GR.CAP_POSITION and r["kind"] == GR.KIND_VETO
    assert r["agent"] == "agent-a" and r["code"] == "600000.SH"
    assert r["date"] == "2026-09-18" and r["ts"].startswith("2026-09-18")
    # 与 decision_pool 同 schema：这三件是远期引擎（update_forward）要读的
    assert r["entry_dt"] == 20260921 and r["entry_px"] is None and r["fwd"] == {}
    assert r["pct"] == 0.2 and r["ref_px"] == 12.34


def test_row_derives_kind_from_rule():
    """unseen/unknown 与 veto 读法相反，类别不能靠调用方自觉。"""
    assert GL.row("a", "600000.SH", GR.BUDGET_UNAFFORDABLE, day="2026-09-18")["kind"] == "unseen"
    assert GL.row("a", "600000.SH", GR.UNKNOWN_NO_EXEC, day="2026-09-18")["kind"] == "unknown"


def test_row_rejects_missing_code_or_rule():
    assert GL.row("agent-a", "", GR.CAP_POSITION, day="2026-09-18") is None
    assert GL.row("agent-a", "600000.SH", "", day="2026-09-18") is None
    assert GL.row("", "600000.SH", GR.CAP_POSITION, day="2026-09-18") is None


def test_row_normalizes_pct_and_ref_px_dirty_values():
    """模型/桥都可能吐字符串和脏值：脏值进不了统计口径（不进 fwd 也不进均值）。"""
    r = GL.row("a", "600000.SH", GR.PCT_ZERO, day="2026-09-18",
               pct="0.3股", ref_px=float("nan"))

    assert r["pct"] is None and r["ref_px"] is None


def test_row_truncates_reason_and_strips_noise():
    r = GL.row("a", "600000.SH", GR.CASH_INSUFFICIENT, day="2026-09-18", reason="x" * 500)

    assert len(r["reason"]) == 200


def test_row_extra_cannot_clobber_core_fields():
    """extra 是给业务附注用的（min_cost/n_shown…），不得改写 id/fwd 这些骨架字段。"""
    r = GL.row("a", "600000.SH", GR.BUDGET_UNAFFORDABLE, day="2026-09-18",
               extra={"min_cost": 42000, "fwd": {"t1": {"excess": 9.9}}, "id": "hack"})

    assert r["min_cost"] == 42000
    assert r["fwd"] == {} and r["id"] != "hack"


# ---------- 记录层：幂等 + 永不抛 ----------

def test_record_writes_jsonl_and_is_idempotent(tmp_path):
    p = tmp_path / "g.jsonl"
    rows = [GL.row("a", "600000.SH", GR.CAP_POSITION, day="2026-09-18")]

    assert GL.record(rows, p) == 1
    assert GL.record(rows, p) == 0                     # 同批重放：幂等
    assert len(_read(p)) == 1


def test_record_keeps_distinct_events_for_same_code(tmp_path):
    """同一天同标的被两条规则拦下 → 两行都在（否则报告会漏掉一条规则的成本）。"""
    p = tmp_path / "g.jsonl"
    GL.record([GL.row("a", "600000.SH", GR.CAP_POSITION, day="2026-09-18")], p)
    GL.record([GL.row("a", "600000.SH", GR.CASH_INSUFFICIENT, day="2026-09-18")], p)

    assert len(_read(p)) == 2


def test_record_skips_bad_rows_without_losing_good_ones(tmp_path):
    p = tmp_path / "g.jsonl"
    n = GL.record([None, {"no": "id"}, GL.row("a", "600000.SH", GR.LEV_OVER,
                                              day="2026-09-18")], p)

    assert n == 1 and len(_read(p)) == 1


def test_record_never_raises_and_returns_zero_on_unwritable(tmp_path):
    """观测层绝不能反噬交易主链路：路径是目录 / 权限拒绝 → 返回 0，不抛。"""
    d = tmp_path / "adir"
    d.mkdir()

    assert GL.record([GL.row("a", "600000.SH", GR.CAP_POSITION, day="2026-09-18")], d) == 0


def test_record_event_persists_to_default_path(tmp_path, monkeypatch):
    """便捷入口走模块常量 GHOST（调用点不自己拼路径）。"""
    monkeypatch.setattr(GL, "GHOST", tmp_path / "ghost.jsonl")

    assert GL.record_event("a", "600000.SH", GR.LIMIT_UP, day="2026-09-18",
                           reason="涨停不追") is True
    assert _read(tmp_path / "ghost.jsonl")[0]["rule"] == GR.LIMIT_UP


def test_record_event_swallows_exceptions(tmp_path, monkeypatch):
    """record_event 是热路径调用点，任何内部异常都必须被吞掉并留痕 False。"""
    monkeypatch.setattr(GL, "GHOST", tmp_path / "ghost.jsonl")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(GL, "record", _boom)

    assert GL.record_event("a", "600000.SH", GR.LIMIT_UP, day="2026-09-18") is False


def test_record_event_accepts_ts_without_day(tmp_path, monkeypatch):
    monkeypatch.setattr(GL, "GHOST", tmp_path / "ghost.jsonl")
    GL.record_event("a", "600000.SH", GR.LIMIT_UP, ts="2026-09-18T10:00:00+08:00")

    assert _read(tmp_path / "ghost.jsonl")[0]["date"] == "2026-09-18"


def test_veto_uses_the_round_clock(tmp_path, monkeypatch):
    """热路径入口用**传入的**轮时钟（不自己再读一次钟）——同一轮两个时刻会让
    事件时间戳彼此错位，事后无法按轮聚合。"""
    from datetime import datetime

    monkeypatch.setattr(GL, "GHOST", tmp_path / "ghost.jsonl")
    now = datetime.fromisoformat("2026-09-18T10:00:00+08:00")

    assert GL.veto("a", "600000.SH", GR.CASH_VCASH, now, reason="虚拟现金不足",
                   pct=0.2, ref_px=10.5, source="live_llm_trade")
    r = _read(tmp_path / "ghost.jsonl")[0]

    assert r["ts"] == "2026-09-18T10:00:00+08:00" and r["date"] == "2026-09-18"
    assert r["pct"] == 0.2 and r["ref_px"] == 10.5 and r["source"] == "live_llm_trade"


def test_veto_without_clock_records_nothing_but_does_not_raise(tmp_path, monkeypatch):
    """没有时钟 → 不落记录（而不是落一条时间不明的）。

    时间不明的行进不了收益统计（没有 entry_dt），只会让"命中次数"虚高——宁可少记
    这一条，也不要制造一个没法判读的样本；但**绝不能抛**（观测层不反噬主链路）。
    """
    monkeypatch.setattr(GL, "GHOST", tmp_path / "ghost.jsonl")

    assert GL.veto("a", "600000.SH", GR.CASH_VCASH, None) is False
    assert not (tmp_path / "ghost.jsonl").exists()


# ---------- 登记表 ----------

def test_registry_covers_every_rule_in_vocabulary():
    """代码能发出的每个规则都必须有依据/失效条件——没有的规则不许上生产。"""
    reg = GL.load_registry()
    missing = [r for r in GR.ALL if r not in reg.get("rules", {})]

    assert not missing, f"词表里的规则缺登记表条目：{missing}"


def test_registry_has_no_dead_entries():
    """登记表里的条目必须对应词表里的规则：改过名字的规则要删旧条目，不留幽灵。"""
    reg = GL.load_registry()
    dead = [r for r in reg.get("rules", {}) if r not in GR.ALL]

    assert not dead, f"登记表里的死条目（词表已无此规则）：{dead}"


def test_registry_entries_have_required_fields_and_consistent_kind():
    for rule, ent in GL.load_registry()["rules"].items():
        for field in ("title", "where", "kind", "evidence", "falsify", "review_by"):
            assert str(ent.get(field) or "").strip(), f"{rule} 缺字段 {field}"
        assert ent["kind"] == GR.kind_of(rule), f"{rule} 类别与词表不一致"


def test_registry_review_dates_are_parseable_and_not_ancient():
    for rule, ent in GL.load_registry()["rules"].items():
        d = date.fromisoformat(ent["review_by"])
        assert d.year >= 2026, f"{rule} 复核日不合法：{ent['review_by']}"


def test_expired_rules_lists_rules_past_review_date():
    """复核日过期必须在读数面上显式出现——规则默认会过期，不会默认正确。"""
    reg = {"rules": {"a.b": {"review_by": "2026-01-01"},
                     "c.d": {"review_by": "2099-01-01"}}}
    got = GL.expired_rules("2026-09-19", registry=reg)

    assert got == [("a.b", "2026-01-01")]


def test_load_registry_fails_open_on_corrupt_file(tmp_path):
    """登记表写坏 = 读不出标题/失效条件，但**绝不抛进交易链路**。"""
    p = tmp_path / "reg.json"
    p.write_text("{ 这不是 json", encoding="utf-8")

    assert GL.load_registry(p) == {"rules": {}}


# ---------- 与决策池同一个远期引擎（不另起口径） ----------

def test_rows_flow_through_decision_track_engine(tmp_path, monkeypatch):
    """影子账自己不实现远期收益：decision_track.update_forward(path=GHOST) 直接可用。

    这是「唯一出处」的证据——一旦有人复制一套收益算法，两边口径必然漂移。
    """
    import pandas as pd
    import decision_track as DT

    ghost = tmp_path / "ghost.jsonl"
    GL.record([GL.row("a", "600000.SH", GR.CAP_POSITION, day="2026-09-18")], ghost)

    # 09-21(一) 入场 10.00 → 当日收 10.50（+5%）；另一只票同日 0% 平盘
    # → 基准 = 全市场等权 = 2.5%，超额 = 5% − 2.5%（两个数都验，防止把
    #   "基准=它自己"的退化情形当成口径正确）
    panel = pd.DataFrame({
        "symbol": ["600000.SH"] * 2 + ["000001.SZ"] * 2,
        "dt": [20260921, 20260922] * 2,
        "open": [10.0, 10.5, 20.0, 20.0], "close": [10.5, 10.6, 20.0, 20.2],
        "high": [10.6, 10.7, 20.1, 20.3], "low": [9.9, 10.4, 19.9, 19.9]})
    monkeypatch.setattr(DT, "_read_panel", lambda days=90: panel)

    assert DT.update_forward(path=ghost, today="2026-09-25")["updated"] == 1
    rec = GL.load_ghost(ghost)[0]

    assert rec["entry_px"] == 10.0
    assert rec["fwd"]["t1"]["ret"] == 0.05
    assert rec["fwd"]["t1"]["bench"] == 0.025          # (5% + 0%) / 2
    assert rec["fwd"]["t1"]["excess"] == 0.025
    assert rec["rule"] == GR.CAP_POSITION              # 回填不得丢业务字段
