#!/usr/bin/env python3
"""港股日线数据拉取（腾讯行情接口，免费，后复权）。

生成 data/HK_stock/merged.jsonl（与美股/A股同格式）：
{"Meta Data": {"2. Symbol": "00700.HK", ...}, "Time Series (Daily)": {"2025-10-02": {"1. buy price": 开盘, "4. sell price": 收盘, ...}}}

用法: python data/HK_stock/get_daily_price_hk.py [--symbols 00700.HK,09988.HK] [--start 2025-09-01]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# 恒生指数权重股（默认股票池，可 --symbols 覆盖）
DEFAULT_SYMBOLS = [
    "00700.HK", "09988.HK", "03690.HK", "01810.HK", "00941.HK",  # 腾讯/阿里/美团/小米/中移动
    "00005.HK", "01299.HK", "00939.HK", "03988.HK", "00011.HK",  # 汇丰/友邦/建行/中行/恒生
    "02318.HK", "02628.HK", "01398.HK", "00998.HK", "00388.HK",  # 平安/人寿/工行/中信/港交所
    "01093.HK", "09618.HK", "09999.HK", "02020.HK", "01024.HK",  # 石药/京东/网易/安踏/快手
    "02331.HK", "02688.HK", "00288.HK", "00016.HK", "00027.HK",  # 李宁/新奥/万洲/新鸿基/银河
    "01928.HK", "00267.HK", "00175.HK", "02382.HK", "06862.HK",  # 金沙/中信/吉利/舜宇/海底捞
]

API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def fetch_kline(symbol: str, start: str, end: str) -> list:
    """拉单只港股日K（后复权）。返回 [{date, open, close, high, low, volume}]。"""
    code = symbol.replace(".HK", "").replace(".", "")
    params = {"param": f"hk{code},day,{start},{end},320,qfq"}
    resp = requests.get(API, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        return []
    node = data.get("data", {}).get(f"hk{code}", {})
    # 复权后数据在 qfqday，普通在 day
    rows = node.get("qfqday") or node.get("day") or []
    bars = []
    for row in rows:
        if len(row) < 6:
            continue
        bars.append({
            "date": row[0],
            "open": row[1],
            "close": row[2],
            "high": row[3],
            "low": row[4],
            "volume": row[5],
        })
    return bars


def to_merged_line(symbol: str, bars: list) -> str:
    """转 merged.jsonl 行（buy price=开盘, sell price=收盘）。"""
    time_series = {}
    for b in bars:
        time_series[b["date"]] = {
            "1. buy price": b["open"],
            "2. high": b["high"],
            "3. low": b["low"],
            "4. sell price": b["close"],
            "5. volume": b["volume"],
        }
    doc = {
        "Meta Data": {
            "1. Information": "Daily Prices (buy price, high, low, sell price) and Volumes",
            "2. Symbol": symbol,
            "3. Last Refreshed": bars[-1]["date"] if bars else "",
            "4. Interval": "daily",
            "6. Time Zone": "Asia/Hong_Kong",
        },
        "Time Series (Daily)": time_series,
    }
    return json.dumps(doc, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="港股日线拉取（腾讯接口）")
    parser.add_argument("--symbols", help="逗号分隔代码列表，默认恒指权重股")
    parser.add_argument("--start", default=(datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d"), help="起始日期")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_SYMBOLS
    end = datetime.now().strftime("%Y-%m-%d")

    out_dir = Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_file = out_dir / "merged.jsonl"

    ok, fail = 0, 0
    with merged_file.open("w", encoding="utf-8") as f:
        for symbol in symbols:
            try:
                bars = fetch_kline(symbol, args.start, end)
                if not bars:
                    print(f"⚠️  {symbol}: 无数据")
                    fail += 1
                    continue
                f.write(to_merged_line(symbol, bars) + "\n")
                print(f"✅ {symbol}: {len(bars)} 根 ({bars[0]['date']} ~ {bars[-1]['date']})")
                ok += 1
            except Exception as e:
                print(f"❌ {symbol}: {e}")
                fail += 1
            time.sleep(0.3)  # 限速

    print(f"\n完成: {ok} 成功 / {fail} 失败 -> {merged_file}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
