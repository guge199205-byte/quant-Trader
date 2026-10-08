"""回合台账价格类特征附件（scripts/roundtrip_features.py）。

最要紧的一组是**点位时间纪律**的回归测试：买入在盘中，那一刻看不到当日收盘，
所以特征只能用到「买入日前一交易日」为止。一旦这条被破坏，所有抽取结论都会失真
而且极难事后发现——必须用测试钉死。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_roundtrip_features.py -q
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import roundtrip_features as F  # noqa: E402

SYM = "600000.SH"


def _write_kline(tmp_path, values: dict[str, float]) -> None:
    """按 Hive 分区写 dt=YYYYMMDD/data.parquet（与 quantdb 布局一致）。"""
    for dt, close in values.items():
        d = tmp_path / f"dt={dt}"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"symbol": [SYM], "time": [dt], "close": [close]}).to_parquet(
            d / "data.parquet")


def _roundtrip(buy_ts="2026-09-11T09:37:26+08:00", code=SYM):
    return {"agent": "glm-5.3-flash", "code": code, "volume": 1000,
            "cost_price": 10.0, "sell_price": 11.0,
            "buy_ts": buy_ts, "sell_ts": "2026-09-11T14:55:00+08:00"}


#: 有涨有跌的震荡序列 —— 单调上行会让 Wilder RSI 恒为 100，测试就失去区分度。
_CLOSES = [10.00, 10.10, 10.05, 10.20, 10.15, 10.30, 10.25, 10.40, 10.35, 10.50,
           10.45, 10.60, 10.55, 10.70, 10.65, 10.80, 10.75, 10.90, 10.85, 11.00]


def _trending(n: int, **_kw):
    """n 个交易日的震荡收盘价，键为 YYYYMMDD（2026-08-20 起）。"""
    base = pd.Timestamp("2026-08-20")
    return {(base + pd.Timedelta(days=i)).strftime("%Y%m%d"): c
            for i, c in enumerate(_CLOSES[:n])}


# ---------- 点位时间纪律 ----------

def test_buy_day_spike_never_enters_features(tmp_path, monkeypatch):
    """买入日暴涨 10 倍 —— 特征必须完全不受影响。"""
    values = _trending(16)                       # 覆盖到 2026-09-04
    buy_day = pd.Timestamp("2026-09-11").strftime("%Y%m%d")
    values[buy_day] = 999.0                      # 买入日暴涨
    values["20260910"] = 10.15                   # 买入日前一交易日
    _write_kline(tmp_path, values)
    monkeypatch.setattr(F, "KLINE_GLOB", str(tmp_path / "dt=*" / "*.parquet"))

    rec = F.compute_features(_roundtrip())

    assert rec["status"] == "ok"
    # 只用到 09-10 为止的 15 根 → 与不含暴涨日的序列算出来的一致
    pre = sorted((d, c) for d, c in values.items() if d < buy_day)
    closes = [c for _d, c in pre][-15:]
    assert rec["entry_rsi14"] == F._rsi_wilder(closes)
    assert 0 < rec["entry_rsi14"] < 100          # 震荡序列 → RSI 落在中间区间
    assert rec["prior_5d_return"] == pytest.approx(
        (closes[-1] / closes[-6] - 1) * 100, abs=0.01)


def test_window_excludes_bars_on_and_after_buy_date(tmp_path, monkeypatch):
    """落在买入当日及之后的 K 线一律不进窗口（含当日停牌但事后补录的情况）。"""
    values = _trending(20)
    values["20260911"] = 50.0
    values["20260912"] = 60.0
    _write_kline(tmp_path, values)
    monkeypatch.setattr(F, "KLINE_GLOB", str(tmp_path / "dt=*" / "*.parquet"))

    rec = F.compute_features(_roundtrip())

    assert rec["status"] == "ok"
    assert rec["window_days"] == 15              # need = max(15, 6)
    # 买入日之后的 50.0 / 60.0 若混进窗口，RSI 会被拉高 —— 用"只到 09-10"的序列对照
    buy_day = "20260911"
    pre = [c for d, c in sorted(_trending(20).items()) if d < buy_day][-15:]
    assert rec["entry_rsi14"] == F._rsi_wilder(pre)


# ---------- 缺数据：占位而非消失 ----------

def test_missing_symbol_writes_placeholder(tmp_path, monkeypatch):
    _write_kline(tmp_path, _trending(20))        # 只有 600000.SH
    monkeypatch.setattr(F, "KLINE_GLOB", str(tmp_path / "dt=*" / "*.parquet"))

    rec = F.compute_features(_roundtrip(code="000001.SZ"))

    assert rec["status"] == "insufficient_data"
    assert "无后复权行情" in rec["reason"]
    assert rec["feature_id"]                        # 仍能被关联到回合


def test_unparsable_buy_ts_yields_placeholder():
    rec = F.compute_features({**_roundtrip(), "buy_ts": "不是时间"})

    assert rec["status"] == "insufficient_data"
    assert "buy_ts" in rec["reason"]


# ---------- 幂等 ----------

def test_write_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FEATURE_LOG", tmp_path / "feat.jsonl")
    recs = [{"feature_id": "bbb", "status": "ok"}, {"feature_id": "aaa", "status": "ok"}]

    F.write_features(recs)
    first = (tmp_path / "feat.jsonl").read_bytes()
    F.write_features(list(reversed(recs)))          # 输入顺序不同

    assert (tmp_path / "feat.jsonl").read_bytes() == first   # 逐字节一致


# ---------- 自然键 ----------

def test_feature_id_is_stable_and_discriminating():
    a = F.feature_id(_roundtrip())
    assert a == F.feature_id(_roundtrip())                       # 稳定
    assert a != F.feature_id({**_roundtrip(), "volume": 200})    # 可区分
