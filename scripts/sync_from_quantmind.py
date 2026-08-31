#!/usr/bin/env python3
"""从本机 quantmind 数据仓库同步 Quant-Trader 价格数据（数据源切换：本地为准）。

- CN（quantdb daily_backward 后复权）:
    data/A_stock/daily_prices_sse_50.csv      (agent 读)
    data/A_stock/merged.jsonl                 (前端读)
    data/A_stock/index_daily_sse_50.json      (SSE50 benchmark, 000016.SH)
- US（quantus daily_forward 前复权 + index_daily NDX）:
    data/daily_prices_{SYM}.json              (agent/前端读, 覆盖到的 89 只)
    data/Adaily_prices_QQQ.json               (benchmark 用 NDX.US 纳指100，文件名保持 QQQ)
- HK：quantHK 覆盖不足（29 只仅 3 只），保留腾讯已拉数据不动

覆盖前自动备份。用法: python scripts/sync_from_quantmind.py
"""
import csv
import json
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
Q = Path("/home/zbox/projects/quantmind/data")
START, END = date(2026, 8, 1), date(2026, 8, 29)


def read_partitions(base: Path, symbols: set, cols=None):
    """读 base/dt=YYYYMMDD/data.parquet 分区 -> {sym: {date: {col: val}}}"""
    result = {s: {} for s in symbols}
    day = START
    while day <= END:
        part = base / f"dt={day.strftime('%Y%m%d')}" / "data.parquet"
        if part.exists():
            t = pq.read_table(str(part), columns=cols)
            d = t.to_pydict()
            for i, sym in enumerate(d["symbol"]):
                if sym in result:
                    k = day.isoformat()
                    result[sym][k] = {c: str(d[c][i]) for c in d if c != "symbol"}
        day += timedelta(days=1)
    return {s: v for s, v in result.items() if v}


def alpha_file(symbol, series, info):
    return {"Meta Data": {"1. Information": info, "2. Symbol": symbol,
            "3. Last Refreshed": sorted(series)[-1] if series else "",
            "4. Interval": "daily", "6. Time Zone": "UTC"},
            "Time Series (Daily)": series}


def backup(targets):
    bak = Path(f"/tmp/baymax_quantmind_backup_{int(time.time())}")
    bak.mkdir(parents=True, exist_ok=True)
    for t in targets:
        if t.exists():
            shutil.copy2(t, bak / t.name)
    print(f"备份 -> {bak}")


def main() -> int:
    cols = ["symbol", "open", "high", "low", "close", "volume", "amount"]

    # ---------- CN: quantdb daily_backward ----------
    print("== CN: quantdb ==")
    cn_csv = ROOT / "data/A_stock/daily_prices_sse_50.csv"
    with open(cn_csv) as f:
        cn_symbols = sorted({r["ts_code"] for r in csv.DictReader(f)})
    t0 = time.time()
    cn_series = read_partitions(Q / "quantdb/1_kline_data/daily_backward", set(cn_symbols))
    print(f"  quantdb 覆盖 {len(cn_series)}/{len(cn_symbols)} 只（{time.time()-t0:.1f}s）")

    with open(cn_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount"])
        for sym in sorted(cn_series):
            dates = sorted(cn_series[sym])
            prev_close = None
            for day in dates:
                s = cn_series[sym][day]
                close = float(s["close"])
                pre = prev_close if prev_close is not None else close
                change = round(close - pre, 4)
                pct = round(change / pre * 100, 4) if pre else 0.0
                w.writerow([sym, day.replace("-", ""), s["open"], s["high"], s["low"],
                            s["close"], f"{pre:.2f}", change, pct, s["volume"], s["amount"]])
                prev_close = close
    print(f"  csv 更新完成: {len(cn_series)} 只")

    with open(ROOT / "data/A_stock/merged.jsonl", "w", encoding="utf-8") as f:
        for sym in sorted(cn_series):
            ts = {day: {"1. open": s["open"], "2. high": s["high"], "3. low": s["low"],
                        "4. close": s["close"], "5. volume": s["volume"]}
                  for day, s in cn_series[sym].items()}
            f.write(json.dumps({"Meta Data": {"2. Symbol": sym,
                    "3. Last Refreshed": sorted(ts)[-1], "4. Interval": "daily",
                    "6. Time Zone": "Asia/Shanghai"}, "Time Series (Daily)": ts},
                    ensure_ascii=False) + "\n")
    print("  merged.jsonl 更新完成")

    # SSE50 指数 benchmark（000016.SH）
    idx = read_partitions(Q / "quantdb/1_kline_data/index_daily", {"000016.SH"})
    if idx.get("000016.SH"):
        series = {d: {"1. open": v["open"], "2. high": v["high"], "3. low": v["low"],
                      "4. close": v["close"], "5. volume": v["volume"]}
                  for d, v in idx["000016.SH"].items()}
        (ROOT / "data/A_stock/index_daily_sse_50.json").write_text(
            json.dumps(alpha_file("SSE50", series, "quantmind index"), ensure_ascii=False), encoding="utf-8")
        print(f"  SSE50 index: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")

    # ---------- US: quantus daily_forward ----------
    print("== US: quantus ==")
    us_symbols = sorted(p.stem.replace("daily_prices_", "")
                        for p in (ROOT / "data").glob("daily_prices_*.json"))
    t0 = time.time()
    us_series = read_partitions(Q / "quantus/1_kline_data/daily_forward", set(us_symbols))
    print(f"  quantus 覆盖 {len(us_series)}/{len(us_symbols)} 只（{time.time()-t0:.1f}s）")
    for sym, series in sorted(us_series.items()):
        (ROOT / "data" / f"daily_prices_{sym}.json").write_text(
            json.dumps(alpha_file(sym, series, "quantmind warehouse"), ensure_ascii=False), encoding="utf-8")
    print(f"  {len(us_series)} 只已写入；缺口 {len(us_symbols)-len(us_series)} 只（Yahoo 后台补）")

    # US benchmark：NDX.US（纳指100，本地 index_daily）写入 Adaily_prices_QQQ.json
    ndx = read_partitions(Q / "quantus/1_kline_data/index_daily", {"NDX.US"})
    if ndx.get("NDX.US"):
        series = {d: {"1. open": v["open"], "2. high": v["high"], "3. low": v["low"],
                      "4. close": v["close"], "5. volume": v["volume"]}
                  for d, v in ndx["NDX.US"].items()}
        (ROOT / "data/Adaily_prices_QQQ.json").write_text(
            json.dumps(alpha_file("QQQ", series, "quantmind NDX.US index (as QQQ benchmark)"),
                       ensure_ascii=False), encoding="utf-8")
        print(f"  NDX benchmark: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")

    print("\n== 完成（HK 保留腾讯数据不动）==")
    return 0


if __name__ == "__main__":
    backup([ROOT / "data/A_stock/daily_prices_sse_50.csv",
            ROOT / "data/A_stock/merged.jsonl",
            ROOT / "data/A_stock/index_daily_sse_50.json",
            ROOT / "data/Adaily_prices_QQQ.json"])
    sys.exit(main())
