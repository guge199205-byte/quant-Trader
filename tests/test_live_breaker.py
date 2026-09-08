"""实盘循环熔断（live_breaker）：当日亏损/日内回撤 → 该 agent 当日禁买。

背景：实盘买入闸门此前只有"单笔/单轮"口径的六道硬约束，没有一条盯"今天亏了多少"，
属 fail-open（MCP 路径有日亏熔断，实盘循环不走那条路）。2026-09-08 补第二道防线。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_breaker.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_breaker as B  # noqa: E402

DAY = "2026-09-08"


def _equity(tmp_path: Path, rows: list) -> Path:
    f = tmp_path / "live_equity.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return f


def _eq(agent, value, ts="09:35", date=DAY):
    return {"key": f"{date} {ts[:2]}", "agent": agent, "date": date,
            "ts": f"{date}T{ts}:00+08:00", "value": value}


def _trades(tmp_path: Path, rows: list) -> Path:
    f = tmp_path / "live_trade_20260908.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return f


def _sell(vol, price, cost, agent="a1", mode=None):
    r = {"ts": f"{DAY}T10:00:00+08:00", "agent": agent, "code": "600309.SH",
         "side": "sell", "volume": vol, "price": price, "cost_price": cost}
    if mode:
        r["mode"] = mode
    else:
        r["fill"] = {"filled_volume": vol, "filled_price": price}
        r.pop("volume"), r.pop("price")
    return r


# ---------------------------------------------------------------- 权益口径

def test_day_equity_first_and_last(tmp_path):
    f = _equity(tmp_path, [_eq("a1", 100_000, "09:35"), _eq("a1", 97_000, "11:00"),
                           _eq("a1", 96_500, "14:00"), _eq("a2", 50_000, "14:00"),
                           _eq("a1", 99_000, "09:35", date="2026-09-05")])
    assert B.day_equity("a1", DAY, f) == (100_000.0, 96_500.0)


def test_day_equity_missing_or_garbage(tmp_path):
    assert B.day_equity("a1", DAY, tmp_path / "nope.jsonl") == (None, None)
    f = tmp_path / "live_equity.jsonl"
    f.write_text('not json\n{"agent":"a1","date":"2026-09-08","value":"x"}\n', encoding="utf-8")
    assert B.day_equity("a1", DAY, f) == (None, None)


# ---------------------------------------------------------------- 已实现亏损

def test_realized_loss_counts_sells_only(tmp_path):
    f = _trades(tmp_path, [
        _sell(500, 9.0, 10.0),                                   # 亏 500
        _sell(200, 20.0, 19.0),                                  # 赚 200（不计入亏损）
        _sell(100, 9.0, 10.0, mode="fill_confirm"),              # 亏 100
        {"agent": "a1", "side": "buy", "code": "600000.SH",
         "fill": {"filled_volume": 100, "filled_price": 9}},     # 买入不计
        {"agent": "a2", "side": "sell", "code": "600309.SH",
         "fill": {"filled_volume": 100, "filled_price": 9}, "cost_price": 10},  # 别家不计
        {"agent": "a1", "side": "sell", "code": "600309.SH", "pending": True,
         "volume": 100, "price": 9, "cost_price": 10},           # 在途未成不计
        {"agent": "a1", "side": "sell", "code": "600309.SH", "error": "rejected",
         "cost_price": 10},                                      # 失败单不计
    ])
    assert B.realized_loss_today("a1", DAY, f) == 600.0


def test_realized_loss_missing_file_is_zero(tmp_path):
    assert B.realized_loss_today("a1", DAY, tmp_path / "nope.jsonl") == 0.0


# ---------------------------------------------------------------- 评估

def test_trips_on_day_loss(tmp_path):
    eq = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 98_000, "11:00")])
    tr = _trades(tmp_path, [_sell(600, 9.0, 10.0)])  # 亏 600 = 0.6%
    assert B.evaluate("a1", DAY, eq, tr)[0] is False
    tr2 = _trades(tmp_path, [_sell(6000, 9.0, 10.0)])  # 亏 6000 = 6% ≥5%
    tripped, reason = B.evaluate("a1", DAY, eq, tr2)
    assert tripped and "已实现亏损" in reason


def test_trips_on_intraday_drawdown(tmp_path):
    eq = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 93_000, "14:00")])  # -7%
    tripped, reason = B.evaluate("a1", DAY, eq, tmp_path / "no_trades.jsonl")
    assert tripped and "回撤" in reason


def test_no_trip_when_flat(tmp_path):
    eq = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 101_000, "14:00")])
    assert B.evaluate("a1", DAY, eq, tmp_path / "no_trades.jsonl") == (False, "")


def test_day_loss_uses_fallback_equity_when_no_snapshot(tmp_path):
    """无当日净值快照时仍能按已实现亏损判（分母用子账户名义额度）。"""
    tr = _trades(tmp_path, [_sell(6000, 9.0, 10.0)])  # 亏 6000 ≥ 100000×5%
    tripped, reason = B.evaluate("a1", DAY, tmp_path / "nope.jsonl", tr)
    assert tripped and "100,000" in reason


def test_no_inputs_no_trip(tmp_path):
    """两项输入都取不到 → 不阻断（第一道闸门的硬约束仍在）。"""
    assert B.evaluate("a1", DAY, tmp_path / "nope.jsonl",
                      tmp_path / "nope2.jsonl") == (False, "")


# ---------------------------------------------------------------- 落盘/幂等

def test_trip_persists_and_survives_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TRIP_DIR", tmp_path / "breaker")
    eq = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 93_000, "14:00")])
    tr = tmp_path / "no_trades.jsonl"
    assert B.check_and_trip("a1", DAY, True, eq, tr)[0] is True
    # 权益恢复后仍熔断（当日不可解除）
    eq2 = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 100_500, "14:30")])
    tripped, reason = B.check_and_trip("a1", DAY, True, eq2, tr)
    assert tripped and "回撤" in reason


def test_dry_run_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TRIP_DIR", tmp_path / "breaker")
    eq = _equity(tmp_path, [_eq("a1", 100_000), _eq("a1", 93_000, "14:00")])
    assert B.check_and_trip("a1", DAY, False, eq, tmp_path / "no_trades.jsonl")[0] is True
    assert B.load_trips(DAY) == {}


def test_save_trip_keeps_first_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TRIP_DIR", tmp_path / "breaker")
    B.save_trip("a1", "首因", DAY)
    B.save_trip("a1", "后因", DAY)
    assert B.load_trips(DAY)["a1"]["reason"] == "首因"


def test_load_trips_garbage_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TRIP_DIR", tmp_path / "breaker")
    (tmp_path / "breaker").mkdir()
    (tmp_path / "breaker" / f"{DAY}.json").write_text("{bad", encoding="utf-8")
    assert B.load_trips(DAY) == {}
