#!/usr/bin/env python3
"""A股日线拉取（东财接口，免费，前复权）。"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.price_tools import all_sse_50_symbols  # noqa: E402

API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def fetch_kline(symbol: str, start: str, end: str) -> list:
    code = symbol.split(".")[0]
    market = "1" if symbol.endswith(".SH") else "0"
    params = {
        "secid": f"{market}.{code}", "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56", "klt": "101", "fqt": "1",
        "beg": start.replace("-", ""), "end": end.replace("-", ""),
    }
    resp = requests.get(API, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    klines = (data.get("data") or {}).get("klines") or []
    bars = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        bars.append({"date": parts[0], "open": parts[1], "close": parts[2],
                     "high": parts[3], "low": parts[4], "volume": parts[5]})
    return bars


def to_merged_line(symbol: str, bars: list) -> str:
    ts = {b["date"]: {"1. buy price": b["open"], "2. high": b["high"],
                      "3. low": b["low"], "4. sell price": b["close"],
                      "5. volume": b["volume"]} for b in bars}
    doc = {"Meta Data": {"2. Symbol": symbol,
                         "3. Last Refreshed": bars[-1]["date"] if bars else "",
                         "4. Interval": "daily", "6. Time Zone": "Asia/Shanghai"},
           "Time Series (Daily)": ts}
    return json.dumps(doc, ensure_ascii=False)


def main() -> int:
    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    out = Path(__file__).resolve().parent / "merged.jsonl"
    ok = fail = 0
    with out.open("w", encoding="utf-8") as f:
        for symbol in all_sse_50_symbols:
            try:
                bars = fetch_kline(symbol, start, end)
                if not bars:
                    print(f"⚠️  {symbol}: 无数据"); fail += 1; continue
                f.write(to_merged_line(symbol, bars) + "\n")
                ok += 1
            except Exception as e:
                print(f"❌ {symbol}: {e}"); fail += 1
            time.sleep(0.15)
    print(f"A股: {ok} 成功 / {fail} 失败 -> {out}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
