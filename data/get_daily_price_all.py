#!/usr/bin/env python3
"""三市场日线数据统一拉取（腾讯行情接口，免费，后复权）。

生成各市场 merged.jsonl（与交易系统格式一致）：
  US: data/merged.jsonl          （us 前缀，如 usAAPL）
  CN: data/A_stock/merged.jsonl  （sh/sz 前缀，如 sh600519）
  HK: data/HK_stock/merged.jsonl （hk 前缀，如 hk00700）

用法: python data/get_daily_price_all.py [--start 2026-07-01]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 各市场股票池（与 prompts/price_tools 一致）
from tools.price_tools import all_nasdaq_100_symbols, all_sse_50_symbols  # noqa: E402

HK_SYMBOLS = [
    "00700.HK", "09988.HK", "03690.HK", "01810.HK", "00941.HK",
    "00005.HK", "01299.HK", "00939.HK", "03988.HK", "00011.HK",
    "02318.HK", "02628.HK", "01398.HK", "00998.HK", "00388.HK",
    "01093.HK", "09618.HK", "09999.HK", "02020.HK", "01024.HK",
    "02331.HK", "02688.HK", "00288.HK", "00016.HK", "00027.HK",
    "01928.HK", "00267.HK", "00175.HK", "02382.HK", "06862.HK",
]

MARKETS = {
    "us": {"symbols": all_nasdaq_100_symbols, "out": "merged.jsonl",
           "prefix": lambda s: f"us{s}", "tz": "US/Eastern",
           "suffixes": [".OQ", ".N"]},  # 纳斯达克/纽交所后缀
    "cn": {"symbols": all_sse_50_symbols, "out": "A_stock/merged.jsonl",
           "prefix": lambda s: s.replace(".SH", "").replace(".SZ", "").lower(),
           "tz": "Asia/Shanghai"},
    "hk": {"symbols": HK_SYMBOLS, "out": "HK_stock/merged.jsonl",
           "prefix": lambda s: f"hk{s.replace('.HK', '')}", "tz": "Asia/Hong_Kong"},
}


def fetch_kline(code: str, start: str, end: str, suffixes=None) -> list:
    for suffix in (suffixes or [""]):
        full = f"{code}{suffix}"
        params = {"param": f"{full},day,{start},{end},500,qfq"}
        try:
            resp = requests.get(API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            node = data.get("data", {}).get(full, {})
            rows = node.get("qfqday") or node.get("day") or []
            if rows:
                return _parse_rows(rows)
        except Exception:
            continue
    return []
    resp = requests.get(API, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        return []
    node = data.get("data", {}).get(code, {})
    rows = node.get("qfqday") or node.get("day") or []
    bars = []
    for row in rows:
        if len(row) < 6:
            continue
        bars.append({
            "date": row[0], "open": row[1], "close": row[2],
            "high": row[3], "low": row[4], "volume": row[5],
        })
    return bars


def _parse_rows(rows: list) -> list:
    bars = []
    for row in rows:
        if len(row) < 6:
            continue
        bars.append({"date": row[0], "open": row[1], "close": row[2],
                     "high": row[3], "low": row[4], "volume": row[5]})
    return bars


def to_merged_line(symbol: str, bars: list, tz: str) -> str:
    time_series = {}
    for b in bars:
        time_series[b["date"]] = {
            "1. buy price": b["open"], "2. high": b["high"],
            "3. low": b["low"], "4. sell price": b["close"],
            "5. volume": b["volume"],
        }
    doc = {
        "Meta Data": {
            "1. Information": "Daily Prices (buy price, high, low, sell price) and Volumes",
            "2. Symbol": symbol,
            "3. Last Refreshed": bars[-1]["date"] if bars else "",
            "4. Interval": "daily",
            "6. Time Zone": tz,
        },
        "Time Series (Daily)": time_series,
    }
    return json.dumps(doc, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="三市场日线统一拉取（腾讯接口）")
    parser.add_argument("--start", default=(datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"))
    parser.add_argument("--markets", default="us,cn,hk", help="逗号分隔: us,cn,hk")
    args = parser.parse_args()

    end = datetime.now().strftime("%Y-%m-%d")
    root = Path(__file__).resolve().parent
    total_ok = total_fail = 0

    for market in args.markets.split(","):
        cfg = MARKETS[market]
        out_file = root / cfg["out"]
        out_file.parent.mkdir(parents=True, exist_ok=True)
        ok = fail = 0
        with out_file.open("w", encoding="utf-8") as f:
            for symbol in cfg["symbols"]:
                code = cfg["prefix"](symbol)
                suffixes = cfg.get("suffixes")
                try:
                    bars = fetch_kline(code, args.start, end, suffixes)
                    if not bars:
                        print(f"⚠️  [{market}] {symbol}: 无数据")
                        fail += 1
                        continue
                    f.write(to_merged_line(symbol, bars, cfg["tz"]) + "\n")
                    ok += 1
                except Exception as e:
                    print(f"❌ [{market}] {symbol}: {e}")
                    fail += 1
                time.sleep(0.25)
        print(f"\n[{market}] {ok} 成功 / {fail} 失败 -> {out_file}")
        total_ok += ok
        total_fail += fail

    print(f"\n总计: {total_ok} 成功 / {total_fail} 失败")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
