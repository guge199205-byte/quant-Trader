#!/usr/bin/env python3
"""更新 Quant-Trader 全部价格数据与 benchmark（免费接口，无 key 依赖）。

- 美股（NASDAQ100 + QQQ）: Yahoo Finance chart API（需 UA 头，无通达信美股源）
- A股（上证50 + 上证50指数 sh000016）: 通达信 TdxAiData 实时源（前复权，批量；
  失败回退 8550 桥，均为通达信，不再用腾讯等第三方）
- 港股（30 只 + 恒指 hkHSI）: 腾讯 fqkline（后复权；通达信暂无港股数据支持）

输出：
  data/daily_prices_{SYM}.json     美股日线（AlphaVantage 格式）
  data/Adaily_prices_QQQ.json      QQQ benchmark
  data/A_stock/daily_prices_sse_50.csv   A股 csv（agent 直接读）
  data/A_stock/merged.jsonl         A股 merged（agent 直接读）
  data/A_stock/index_daily_sse_50.json  上证50 benchmark
  data/HK_stock/merged.jsonl        港股 merged
  data/hsi_daily.json              恒指 benchmark

覆盖前自动备份到 /tmp/baymax_data_backup_<ts>/。用法: python scripts/update_prices.py
"""
import csv
import json
import os
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
TENCENT_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 拉取范围（agent 交易集中在 08-24~28，富余几天保证 benchmark 对齐）
START = "2026-08-01"
END = "2026-08-29"


def http_get(url: str, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def yahoo_daily(symbol: str) -> dict:
    """Yahoo 日线 -> AlphaVantage 格式 {date: {"1. open".."5. volume"}}

    免费接口对连续请求限流（429/403），降频 + 重试（最多 4 次）。
    """
    d1 = int(time.mktime(time.strptime(START, "%Y-%m-%d")))
    d2 = int(time.mktime(time.strptime(END, "%Y-%m-%d"))) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={d1}&period2={d2}&interval=1d")
    for attempt in range(4):
        try:
            data = json.loads(http_get(url, UA))
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


def tencent_daily(symbol: str) -> dict:
    """腾讯 fqkline 日线 -> {date: {"1. open".."5. volume"}}（后复权）"""
    code = symbol.replace(".HK", "").replace(".", "")
    if symbol.endswith(".HK"):
        code = f"hk{code}"
    elif symbol.endswith(".SH"):
        code = f"sh{symbol[:6]}"
    elif symbol.endswith(".SZ"):
        code = f"sz{symbol[:6]}"
    url = f"{TENCENT_API}?param={code},day,{START},{END},320,qfq"
    data = json.loads(http_get(url, UA))
    node = data.get("data", {}).get(code, {})
    rows = node.get("qfqday") or node.get("day") or []
    series = {}
    for row in rows:
        if len(row) < 6:
            continue
        series[row[0]] = {
            "1. open": row[1],
            "2. high": row[3],
            "3. low": row[4],
            "4. close": row[2],
            "5. volume": row[5],
        }
    return series


def tencent_index(code: str) -> dict:
    """腾讯指数日线（sh000016 上证50 / hkHSI 恒指）-> 同 AlphaVantage 格式"""
    url = f"{TENCENT_API}?param={code},day,{START},{END},320,qfq"
    data = json.loads(http_get(url, UA))
    node = data.get("data", {}).get(code, {})
    rows = node.get("day") or node.get("qfqday") or []
    series = {}
    for row in rows:
        if len(row) < 6:
            continue
        series[row[0]] = {
            "1. open": row[1],
            "2. high": row[3],
            "3. low": row[4],
            "4. close": row[2],
            "5. volume": row[5],
        }
    return series


def _to_alpha(bars: list) -> dict:
    """bars（本仓库统一格式 [{"date","open","high","low","close","volume"}]）
    -> {date: {"1. open".."5. volume"}}，值转原生 float（np.float64 不能 JSON 序列化）。"""
    series = {}
    for b in bars:
        day, close = b.get("date"), b.get("close")
        if not day or close is None:
            continue
        series[day] = {
            "1. open": float(b.get("open") or 0),
            "2. high": float(b.get("high") or 0),
            "3. low": float(b.get("low") or 0),
            "4. close": float(close),
            "5. volume": float(b.get("volume") or 0),
        }
    return series


def tdx_daily_batch(symbols: list, start: str, end: str) -> dict:
    """通达信 TdxAiData 批量日线（前复权）-> {symbol: AlphaVantage 格式 series}。
    分块调用（10 只/块）降低单次请求大小，块失败自动跳过（限流退避在 tdx_aidata 内部）。"""
    from agent_tools.datasources import tdx_aidata

    out = {}
    if not tdx_aidata.available():
        return out
    for i in range(0, len(symbols), 10):
        part = symbols[i:i + 10]
        try:
            bars_map = tdx_aidata.get_klines_batch(part, interval="daily", start=start, end=end)
        except Exception as e:
            print(f"  ⚠️ TdxAiData 块失败({part[0]}~{part[-1]}): {e}")
            continue
        for sym, bars in bars_map.items():
            series = _to_alpha(bars)
            if series:
                out[sym] = series
    return out


def bridge_daily(symbols: list, start: str, end: str) -> dict:
    """8550 桥（通达信）日线回退 -> {symbol: AlphaVantage 格式 series}。"""
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    broker = TdxBridgeBroker()
    out = {}
    for sym in symbols:
        try:
            bars = [b for b in broker.get_klines(sym, interval="daily")
                    if start <= (b.get("date") or "") <= end]
            series = _to_alpha(bars)
            if series:
                out[sym] = series
        except Exception as e:
            print(f"  ❌ {sym}: {e}")
    return out


def alpha_vantage_file(symbol: str, series: dict, info: str) -> dict:
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


def backup(targets):
    bak = Path(f"/tmp/baymax_data_backup_{int(time.time())}")
    bak.mkdir(parents=True, exist_ok=True)
    for t in targets:
        if t.exists():
            shutil.copy2(t, bak / t.name)
    print(f"备份 -> {bak}")


def main():
    targets = [
        ROOT / "data/Adaily_prices_QQQ.json",
        ROOT / "data/A_stock/daily_prices_sse_50.csv",
        ROOT / "data/A_stock/merged.jsonl",
        ROOT / "data/A_stock/index_daily_sse_50.json",
        ROOT / "data/HK_stock/merged.jsonl",
        ROOT / "data/hsi_daily.json",
    ]
    backup(targets)

    # ---------- 美股（Yahoo）----------
    us_symbols = sorted(
        p.stem.replace("daily_prices_", "")
        for p in (ROOT / "data").glob("daily_prices_*.json")
    )
    print(f"\n== 美股 {len(us_symbols)} 只 + QQQ ==")
    us_ok, us_fail = 0, 0
    for sym in us_symbols + ["QQQ"]:
        try:
            series = yahoo_daily(sym)
            if not series:
                print(f"  ⚠️ {sym}: 无数据"); us_fail += 1; continue
            out = ROOT / "data" / (f"Adaily_prices_{sym}.json" if sym == "QQQ"
                                   else f"daily_prices_{sym}.json")
            out.write_text(json.dumps(alpha_vantage_file(sym, series, "Yahoo Finance daily"), ensure_ascii=False), encoding="utf-8")
            print(f"  ✅ {sym}: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")
            us_ok += 1
        except Exception as e:
            print(f"  ❌ {sym}: {e}"); us_fail += 1
        time.sleep(1.2)  # 降频防 Yahoo 限流

    # ---------- A股（通达信 TdxAiData 实时源，失败回退 8550 桥）----------
    print("\n== A股 上证50 + 指数 ==")
    cn_csv = ROOT / "data/A_stock/daily_prices_sse_50.csv"
    with open(cn_csv) as f:
        rows = list(csv.DictReader(f))
    cn_symbols = sorted({r["ts_code"] for r in rows})
    # A股/港股用北京时间（本机时区可能是 JST）
    end_day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    from agent_tools.datasources import tdx_aidata

    tdx_up = tdx_aidata.available()
    if not tdx_up:
        print("  ⚠️ TdxAiData 不可用 → 全部走桥回退")
    cn_series = tdx_daily_batch(cn_symbols, START, end_day)
    print(f"  TdxAiData: {len(cn_series)}/{len(cn_symbols)} 只")
    missing = [s for s in cn_symbols if s not in cn_series]
    if missing:
        print(f"  桥回退 {len(missing)} 只: {', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}")
        cn_series.update(bridge_daily(missing, START, end_day))
    cn_ok = len(cn_series)
    cn_fail = len(cn_symbols) - cn_ok
    for sym in sorted(cn_series):
        series = cn_series[sym]
        print(f"  ✅ {sym}: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")

    if cn_series:
        # 写 csv（保持原列顺序）
        with open(cn_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"])
            for sym, series in cn_series.items():
                dates = sorted(series)
                prev_close = None
                for day in dates:
                    s = series[day]
                    close = float(s["4. close"])
                    pre = prev_close if prev_close is not None else close
                    change = round(close - pre, 4)
                    pct = round(change / pre * 100, 4) if pre else 0.0
                    w.writerow([sym, day.replace("-", ""), s["1. open"], s["2. high"], s["3. low"], s["4. close"],
                                f"{pre:.2f}", change, pct, s["5. volume"], ""])
                    prev_close = close
        print(f"  csv 更新完成: {len(cn_series)} 只")

        # 写 A_stock/merged.jsonl（前端 data-loader 读这个文件算资产曲线）
        with open(ROOT / "data/A_stock/merged.jsonl", "w", encoding="utf-8") as f:
            for sym, series in cn_series.items():
                ts = {day: {"1. open": s["1. open"], "2. high": s["2. high"],
                            "3. low": s["3. low"], "4. close": s["4. close"],
                            "5. volume": s["5. volume"]} for day, s in series.items()}
                f.write(json.dumps({
                    "Meta Data": {"2. Symbol": sym, "3. Last Refreshed": sorted(ts)[-1],
                                  "4. Interval": "daily", "6. Time Zone": "Asia/Shanghai"},
                    "Time Series (Daily)": ts,
                }, ensure_ascii=False) + "\n")
        print(f"  A_stock/merged.jsonl 更新完成: {len(cn_series)} 只")

        # 上证50 指数 benchmark（000016.SH，TdxAiData → 桥回退）
        try:
            idx = {}
            if tdx_up:
                try:
                    idx = _to_alpha(tdx_aidata.get_klines("000016.SH", interval="daily",
                                                          start=START, end=end_day))
                except Exception:
                    idx = {}
            if not idx:
                from agent_tools.brokers.tdx_bridge import TdxBridgeBroker
                idx = _to_alpha([b for b in TdxBridgeBroker().get_klines("000016.SH", interval="daily")
                                 if START <= (b.get("date") or "") <= end_day])
            if idx:
                (ROOT / "data/A_stock/index_daily_sse_50.json").write_text(
                    json.dumps(alpha_vantage_file("SSE50", idx, "通达信 daily"), ensure_ascii=False),
                    encoding="utf-8")
                print(f"  ✅ sh000016: {len(idx)} 点")
        except Exception as e:
            print(f"  ❌ sh000016: {e}")
    else:
        print("  ❌ 全部行情失败，保留原文件（备份在 /tmp/baymax_data_backup_*）")

    # ---------- 港股（腾讯）----------
    print("\n== 港股 30 只 + 恒指 ==")
    hk_series = {}
    hk_ok, hk_fail = 0, 0
    hk_file = ROOT / "data/HK_stock/merged.jsonl"
    hk_symbols = []
    for line in hk_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            hk_symbols.append(json.loads(line)["Meta Data"]["2. Symbol"])
    for sym in hk_symbols:
        try:
            series = tencent_daily(sym)
            if series:
                hk_series[sym] = series
                print(f"  ✅ {sym}: {len(series)} 点 ({sorted(series)[0]}~{sorted(series)[-1]})")
                hk_ok += 1
            else:
                print(f"  ⚠️ {sym}: 无数据"); hk_fail += 1
        except Exception as e:
            print(f"  ❌ {sym}: {e}"); hk_fail += 1
        time.sleep(0.3)

    with open(hk_file, "w", encoding="utf-8") as f:
        for sym, series in hk_series.items():
            # 与 get_daily_price_hk.py 相同格式：1. buy price=open / 4. sell price=close
            ts = {day: {"1. buy price": s["1. open"], "2. high": s["2. high"],
                        "3. low": s["3. low"], "4. sell price": s["4. close"],
                        "5. volume": s["5. volume"]} for day, s in series.items()}
            f.write(json.dumps({
                "Meta Data": {"2. Symbol": sym, "3. Last Refreshed": sorted(ts)[-1],
                              "4. Interval": "daily", "6. Time Zone": "Asia/Hong_Kong"},
                "Time Series (Daily)": ts,
            }, ensure_ascii=False) + "\n")
    print(f"  merged.jsonl 更新完成: {len(hk_series)} 只")

    # 恒指 benchmark
    try:
        hsi = tencent_index("hkHSI")
        (ROOT / "data/hsi_daily.json").write_text(
            json.dumps(alpha_vantage_file("HSI", hsi, "Tencent index"), ensure_ascii=False), encoding="utf-8")
        print(f"  ✅ hkHSI: {len(hsi)} 点")
    except Exception as e:
        print(f"  ❌ hkHSI: {e}")

    print(f"\n== 汇总: US {us_ok}/{us_ok + us_fail} | CN {cn_ok}/{cn_ok + cn_fail} | HK {hk_ok}/{hk_ok + hk_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
