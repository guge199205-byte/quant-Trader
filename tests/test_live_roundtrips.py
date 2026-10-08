"""回合台账（live_ledger.record_sell → ledger['roundtrips'] → save_ledger 落盘）。

为什么需要：本仓库此前**不记录已平仓回合**（台账只有未平仓持仓），
导致「从自己的成交流水里抽取盈利形态」（HKUDS/Vibe-Trading 的 Shadow Account）
无从做起，定量复盘也只能靠 LLM 读日志写定性结论。

设计约束：live_ledger 的不变量是「纯函数不碰文件」（见 scripts/test_live_ledger.py），
所以 record_sell 只把回合记进**返回的账本**，落盘统一由 save_ledger 这个唯一写点完成。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_roundtrips.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_ledger as L  # noqa: E402


def _ledger_with(agent="glm-5.3-flash", code="600309.SH", vol=1000,
                 cost=10.0, buy_ts="2026-09-08T09:35:00+08:00"):
    return L.record_buy({"version": 1, "agents": {}}, agent, code, vol, cost, buy_ts)


# ---------- record_sell 产出回合 ----------

def test_full_close_emits_roundtrip_with_pnl():
    led = _ledger_with()
    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 1000, 11.0,
                        "2026-09-11T14:55:00+08:00", exit_reason="止盈")

    rts = led["roundtrips"]
    assert len(rts) == 1
    rt = rts[0]
    assert rt["closed"] is True
    assert rt["volume"] == 1000
    assert rt["realized_pnl"] == pytest.approx(1000.0)      # (11-10)×1000
    assert rt["pnl_pct"] == pytest.approx(10.0)
    assert rt["exit_reason"] == "止盈"
    # 持仓天数按时间戳算：9/8 09:35 → 9/11 14:55
    assert rt["holding_days"] == pytest.approx(3.223, abs=0.01)


def test_partial_close_marks_not_closed_and_can_be_followed():
    """部分平仓也要出回合（Vibe-Trading 按 FIFO 配对，部分离场同样是样本）。"""
    led = _ledger_with()
    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 400, 9.0,
                        "2026-09-09T10:00:00+08:00", exit_reason="减仓")

    rt = led["roundtrips"][0]
    assert rt["closed"] is False
    assert rt["volume"] == 400
    assert rt["realized_pnl"] == pytest.approx(-400.0)      # (9-10)×400
    # 仓位仍在，剩余 600
    assert led["agents"]["glm-5.3-flash"]["positions"]["600309.SH"]["volume"] == 600

    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 600, 12.0,
                        "2026-09-10T10:00:00+08:00", exit_reason="止盈")
    assert [r["closed"] for r in led["roundtrips"]] == [False, True]
    assert led["agents"]["glm-5.3-flash"]["positions"] == {}


def test_sell_without_position_emits_nothing():
    led = _ledger_with()
    out = L.record_sell(led, "glm-5.3-flash", "000001.SZ", 100, 10.0, "t")

    assert out is led                       # 原样返回
    assert "roundtrips" not in out


def test_holding_days_tolerates_bad_timestamps():
    led = _ledger_with(buy_ts="不是时间")
    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 1000, 11.0, "2026-09-11T14:55:00+08:00")

    assert led["roundtrips"][0]["holding_days"] is None     # 不臆造


# ---------- save_ledger 落盘 ----------

def test_save_ledger_flushes_roundtrips_and_clears_them(tmp_path, monkeypatch):
    log = tmp_path / "live_roundtrips.jsonl"
    monkeypatch.setattr(L, "ROUNDTRIP_LOG", log)
    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "live_ledger.json")

    led = _ledger_with()
    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 1000, 11.0,
                        "2026-09-11T14:55:00+08:00", exit_reason="止盈")
    L.save_ledger(led)

    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1 and rows[0]["exit_reason"] == "止盈"
    # 落盘后从账本里清掉（否则每次 save 都会重复追加）
    saved = json.loads((tmp_path / "live_ledger.json").read_text(encoding="utf-8"))
    assert "roundtrips" not in saved


def test_save_ledger_keeps_roundtrips_when_append_fails(tmp_path, monkeypatch):
    """追加失败不能丢回合：留在账本里等下次 save 重试。"""
    monkeypatch.setattr(L, "ROUNDTRIP_LOG", tmp_path / "no_such_dir" / "x.jsonl")
    # 落盘符号已上移到协议层（见 docs/ACCOUNT_PROTOCOL_PLAN.md 事项一）
    monkeypatch.setattr(L._p, "append_roundtrips", lambda rows, path=None: False)
    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "live_ledger.json")

    led = _ledger_with()
    led = L.record_sell(led, "glm-5.3-flash", "600309.SH", 1000, 11.0, "t")
    L.save_ledger(led)

    saved = json.loads((tmp_path / "live_ledger.json").read_text(encoding="utf-8"))
    assert len(saved.get("roundtrips") or []) == 1
