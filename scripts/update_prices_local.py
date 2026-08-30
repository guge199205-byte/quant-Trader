#!/usr/bin/env python3
"""US 价格更新：本机 quantus 数据仓库为主 + Yahoo 补缺口。

- quantus（/home/zbox/projects/quantmind/data/quantus/1_kline_data/daily_forward）
  覆盖 BayMax 102 只中的 88 只（Hive 分区 dt=YYYYMMDD，裸 ticker）
- 其余 13 只（含 QQQ benchmark）用 Yahoo chart API 补（降频防限流）

输出：data/daily_prices_{SYM}.json、data/Adaily_prices_QQQ.json（AlphaVantage 格式）
覆盖前自动备份到 /tmp/baymax_us_backup_<ts>/。用法: python scripts/update_prices_local.py
"""
import json
import shutil
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
QUANTUS = Path("/home/zbox/projects/quantmind/data/quantus/1_kline_data/daily_forward")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
START, END = date(2026, 8, 1), date(2026, 8, 29)


def alpha_file(symbol: str, series: dict, info: str) -> dict:
    return {
        "Meta Data": {
            "1. Information": info,
            "2. Symbol": symbol,
            "3. Last Refreshed": sorted(series)[-1] if series else "",
            "4. Interval": "daily",
            "6. Time Zone": "UTC",
        },
        "Time Series (Daily)": series,
    }


def yahoo_daily(symbol: str) -> dict:
    """Yahoo 日线 -> {date: {1-5 字段}}（限流重试 4 次，等待 20s*(attempt+1)）"""
    d1 = int(time.mktime(time.strptime(str(START), "%Y-%m-%d")))
    d2 = int(time.mktime(time.strptime(str(END), "%Y-%m-%d"))) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={d1}&period2={d2}&interval=1d")
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            break
        except Exception as e:
            if attempt == 3:
                raise
            wait = 20 * (attempt + 1)
            print(f"    (限流 {e}，{wait}s 后重试 {attempt + 1}/3)")
            time.sleep(wait)
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    series = {}
    for i, t in enumerate(ts):
        close = quote["close"][i]
        if close is None:
            continue
        day = date.fromtimestamp(t).isoformat()
        series[day] = {
            "1. open": str(quote["open"][i] or ""),
            "2. high": str(quote["high"][i] or ""),
            "3. low": str(quote["low"][i] or ""),
            "4. close": str(close),
            "5. volume": str(quote["volume"][i] or ""),
        }
    return series


def local_series(symbols: set) -> dict:
    """从 quantus 日分区读多日数据 -> {sym: {date: {...}}}"""
    result = {s: {} for s in symbols}
    day = START
    while day <= END:
        part = QUANTUS / f"dt={day.strftime('%Y%m%d')}" / "data.parquet"
        if part.exists():
            t = pq.read_table(str(part))
            df = t.to_pydict()
            for i, sym in enumerate(df["symbol"]):
                if sym in result:
                    d = day.isoformat()
                    result[sym][d] = {
                        "1. open": str(df["open"][i]),
                        "2. high": str(df["high"][i]),
                        "3. low": str(df["low"][i]),
                        "4. close": str(df["close"][i]),
                        "5. volume": str(df["volume"][i]),
                    }
        day += timedelta(days=1)
    return {s: v for s, v in result.items() if v}


def main() -> int:
    us_symbols = sorted(
        p.stem.replace("daily_prices_", "")
        for p in (ROOT / "data").glob("daily_prices_*.json")
    )
    # 备份
    bak = Path(f"/tmp/baymax_us_backup_{int(time.time())}")
    bak.mkdir(parents=True, exist_ok=True)
    for p in (ROOT / "data").glob("daily_prices_*.json"):
        shutil.copy2(p, bak / p.name)
    if (ROOT / "data/Adaily_prices_QQQ.json").exists():
        shutil.copy2(ROOT / "data/Adaily_prices_QQQ.json", bak / "Adaily_prices_QQQ.json")
    print(f"备份 -> {bak}")

    # 1) 本地 quantus
    print(f"\n== quantus 本地 {len(us_symbols)} 只 ==")
    local = local_series(set(us_symbols))
    ok = 0
    for sym, series in sorted(local.items()):
        out = ROOT / "data" / f"daily_prices_{sym}.json"
        out.write_text(json.dumps(alpha_file(sym, series, "quantus local warehouse"),
                                  ensure_ascii=False), encoding="utf-8")
        print(f"  ✅ {sym}: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")
        ok += 1
    missing = [s for s in us_symbols if s not in local]
    print(f"  本地完成 {ok}/{len(us_symbols)}，缺口 {len(missing)} 只 -> Yahoo")

    # 2) Yahoo 补缺口 + QQQ
    yahoo_syms = sorted(missing) + ["QQQ"]
    print(f"\n== Yahoo {len(yahoo_syms)} 只 ==")
    for sym in yahoo_syms:
        try:
            series = yahoo_daily(sym)
            if not series:
                print(f"  ⚠️ {sym}: 无数据")
                continue
            if sym == "QQQ":
                out = ROOT / "data" / "Adaily_prices_QQQ.json"
            else:
                out = ROOT / "data" / f"daily_prices_{sym}.json"
            out.write_text(json.dumps(alpha_file(sym, series, "Yahoo Finance daily"),
                                      ensure_ascii=False), encoding="utf-8")
            print(f"  ✅ {sym}: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")
        time.sleep(1.5)

    print(f"\n== 汇总: 本地 {ok}/{len(us_symbols)} + Yahoo {len(yahoo_syms)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
