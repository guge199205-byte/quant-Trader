#!/usr/bin/env python3
"""三市场日线数据统一拉取。

生成各市场 merged.jsonl（与交易系统格式一致）：
  US: data/merged.jsonl          （us 前缀，如 usAAPL，腾讯接口）
  CN: data/A_stock/merged.jsonl  （sh/sz 前缀，如 sh600519，通达信 TdxAiData 实时源，
                                  失败回退 8550 桥——2026-08-31 起不再用腾讯等第三方）
  HK: data/HK_stock/merged.jsonl （hk 前缀，如 hk00700，腾讯接口，通达信暂无港股）

用法: python data/get_daily_price_all.py [--start 2026-07-01]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
           "tz": "Asia/Shanghai", "source": "tdx"},
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


def _to_native_bars(bars: list) -> list:
    """统一 bars 数值为原生 float（桥返回字符串、TdxAiData 返回 np.float64）。"""
    out = []
    for b in bars:
        try:
            out.append({"date": b["date"], "open": float(b["open"]), "close": float(b["close"]),
                        "high": float(b["high"]), "low": float(b["low"]),
                        "volume": float(b["volume"])})
        except (TypeError, ValueError, KeyError):
            continue
    return out


def tdx_fetch(symbol: str, start: str, end: str) -> list:
    """通达信 A股日线（sh600519 → 600519.SH）：TdxAiData 实时源 → 8550 桥回退。"""
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker
    from agent_tools.datasources import tdx_aidata

    code = symbol.strip().lower()
    if code.startswith(("sh", "sz", "bj")) and len(code) >= 8:
        code = f"{code[2:]}.{code[:2].upper()}"
    try:
        if tdx_aidata.available():
            bars = tdx_aidata.get_klines(code, interval="daily", start=start, end=end)
            if bars:
                return _to_native_bars(bars)
        return _to_native_bars(
            [b for b in TdxBridgeBroker().get_klines(code, interval="daily")
             if start <= (b.get("date") or "") <= end])
    except Exception:
        return []


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

    root = Path(__file__).resolve().parent
    total_ok = total_fail = 0

    for market in args.markets.split(","):
        cfg = MARKETS[market]
        # "今天"用各市场本地时区（本机可能是 JST，不能依赖）
        end = datetime.now(ZoneInfo(cfg["tz"])).strftime("%Y-%m-%d")
        out_file = root / cfg["out"]
        out_file.parent.mkdir(parents=True, exist_ok=True)
        ok = fail = 0
        # 先全部拉取到内存，成功条数>0 才落盘——避免拉取失败时把已有 merged.jsonl 清空
        lines: list[str] = []
        for symbol in cfg["symbols"]:
            code = cfg["prefix"](symbol)
            suffixes = cfg.get("suffixes")
            try:
                if cfg.get("source") == "tdx":
                    bars = tdx_fetch(symbol, args.start, end)
                else:
                    bars = fetch_kline(code, args.start, end, suffixes)
                if not bars:
                    print(f"⚠️  [{market}] {symbol}: 无数据")
                    fail += 1
                    continue
                lines.append(to_merged_line(symbol, bars, cfg["tz"]) + "\n")
                ok += 1
            except Exception as e:
                print(f"❌ [{market}] {symbol}: {e}")
                fail += 1
            time.sleep(0.25)
        if ok > 0:
            out_file.write_text("".join(lines), encoding="utf-8")
            print(f"\n[{market}] {ok} 成功 / {fail} 失败 -> {out_file}")
        else:
            print(f"\n[{market}] {ok} 成功 / {fail} 失败 -> 全部失败，保留原文件不覆盖: {out_file}")
        total_ok += ok
        total_fail += fail

    print(f"\n总计: {total_ok} 成功 / {total_fail} 失败")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
