#!/usr/bin/env python3
"""从 merged.jsonl 生成等权 NASDAQ-100 基准（AlphaVantage 风格），供 arena 前端对比。

用法: cd data && python ../scripts/gen_benchmark.py
输出: data/benchmark_nasdaq100.json（2026 数据与 agent 同源同步）
说明: data/ 不入库，每次数据更新后重跑即可；前端 /api/data/benchmark_nasdaq100.json 直接读。
"""
import json
from collections import defaultdict
from pathlib import Path

MERGED = Path(__file__).resolve().parent.parent / "data" / "merged.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "benchmark_nasdaq100.json"

prices: dict[str, dict[str, float]] = defaultdict(dict)  # date -> {symbol: close}
with MERGED.open() as f:
    for line in f:
        doc = json.loads(line)
        sym = doc.get("Meta Data", {}).get("2. Symbol")
        if not sym:
            continue
        for key, series in doc.items():
            if not key.startswith("Time Series") or not isinstance(series, dict):
                continue
            for ts, bar in series.items():
                close = bar.get("4. close") or bar.get("2. high")
                if close:
                    prices[ts[:10]][sym] = float(close)

dates = sorted(prices)
out = {
    "Meta Data": {
        "1. Information": "quantmind equal-weight NASDAQ-100 index (generated from merged.jsonl)",
        "2. Symbol": "NDX100",
        "3. Last Refreshed": dates[-1] if dates else "",
        "4. Interval": "daily",
        "6. Time Zone": "UTC",
    },
    "Time Series (Daily)": {},
}
for d in dates:
    vals = list(prices[d].values())
    if vals:
        out["Time Series (Daily)"][d] = {"4. close": str(round(sum(vals) / len(vals), 4))}

with OUT.open("w") as f:
    json.dump(out, f)

print(f"generated: {len(dates)} days, {dates[0]} ~ {dates[-1]}, {OUT}")
