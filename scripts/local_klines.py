#!/usr/bin/env python3
"""本地历史K线（duckdb 直读 quantus/quanthk parquet，quantmind 数据资产日更）。

- quantus = 美股日线（symbol 纯代码 ABNB）；quanthk = 港股日线（0622.HK 四位带零）
- 路径: /home/zbox/projects/quantmind/data/{quantus,quanthk}/1_kline_data/daily_forward/dt=YYYY-MM-DD/data.parquet
- 用法: get_daily(symbol, market, days=60) → [{date, open, high, low, close, volume}]（旧→新）
- symbol 归一化: HK.00700/00700 → 0700.HK；US.AAPL/AAPL → AAPL
"""

import glob
import os
from datetime import date, timedelta
from pathlib import Path

QUANTMIND_DATA = Path(os.getenv("QUANTMIND_DATA_DIR",
                                "/home/zbox/projects/quantmind/data"))


def _data_dir() -> Path:
    """quantmind 数据根：宿主 /home/zbox/projects/quantmind/data 或容器 /data。"""
    for p in (os.getenv("QUANTMIND_DATA_DIR", ""), "/data",
              "/home/zbox/projects/quantmind/data"):
        if p and (Path(p) / "quantus").is_dir():
            return Path(p)
    return Path("/home/zbox/projects/quantmind/data")


def _normalize(symbol: str, market: str) -> str:
    if market == "hk":
        # HK.00700 / 00700 → 0700.HK（parquet 是四位带零：0622.HK）
        digits = str(symbol).replace("HK.", "").lstrip("0") or "0"
        return digits.zfill(4) + ".HK"
    return str(symbol).split(".")[-1].upper()


def get_daily(symbol: str, market: str, days: int = 60) -> list:
    """读最近 days 个交易日的本地日线（duckdb 直查，零依赖）；失败返回 []。"""
    import duckdb

    base = _data_dir() / ("quanthk" if market == "hk" else "quantus")
    sym = _normalize(symbol, market)
    # 只扫近 150 天的分区（60 日线 + 节假日余量），dt=YYYY-MM-DD 命名可裁剪
    cutoff = (date.today() - timedelta(days=150)).isoformat()
    parts = sorted(
        p for p in glob.glob(str(base / "1_kline_data/daily_forward/dt=*/data.parquet"))
        if p.split("dt=")[1][:10] >= cutoff)
    if not parts:
        return []
    try:
        union = " UNION ALL ".join(
            f"SELECT symbol, time, open, high, low, close, volume FROM read_parquet('{p}')"
            for p in parts)
        df = duckdb.sql(
            f"SELECT * FROM ({union}) WHERE symbol = '{sym}' "
            f"ORDER BY time DESC LIMIT {int(days)}").df()
    except Exception:  # noqa: BLE001
        return []
    if df.empty:
        return []
    df = df.sort_values("time")
    return [{"date": str(r.time)[:10], "open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
            for r in df.itertuples()]


def trend_features(symbol: str, market: str, days: int = 60) -> dict:
    """历史趋势特征：mom20（近20日动量）/ mom60（近60日动量）/ vol20（20日波动）。"""
    bars = get_daily(symbol, market, days=days)
    if len(bars) < 20:
        return {}
    closes = [b["close"] for b in bars]
    out: dict = {}
    if len(closes) >= 21 and closes[-21] > 0:
        out["mom20"] = round((closes[-1] / closes[-21] - 1) * 100, 2)
    if len(closes) >= 61 and closes[-61] > 0:
        out["mom60"] = round((closes[-1] / closes[-61] - 1) * 100, 2)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) >= 10:
        avg = sum(rets) / len(rets)
        out["vol20"] = round((sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 2)
    return out


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "00700"
    mkt = sys.argv[2] if len(sys.argv) > 2 else "hk"
    bars = get_daily(sym, mkt, days=5)
    print(f"{sym} ({mkt}): {len(bars)} 根", bars[-1] if bars else "无数据")
    print("趋势:", trend_features(sym, mkt))
