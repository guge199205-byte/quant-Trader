#!/usr/bin/env python3
"""一键初始化 Quant-Trader 价格数据（新用户部署 / 重建数据用）。

三个市场 + 两个基准，全部走免费接口、不需要任何数据源 Key，
输出与生产环境（scripts/sync_from_quantmind.py）完全一致的格式：

  A股  SSE50（50 只，前复权）+ 上证50指数 000016.SH   腾讯行情接口   -> data/A_stock/
  美股  NASDAQ100（102 只，不复权）+ QQQ 基准          Yahoo Finance -> data/daily_prices_*.json
  港股  恒指权重（30 只，后复权）                      腾讯行情接口   -> data/HK_stock/merged.jsonl

用法:
  python scripts/bootstrap_data.py             # 全市场（约 3~6 分钟）
  python scripts/bootstrap_data.py --skip us   # 跳过美股（--skip cn/hk/us 可多写）
  python scripts/bootstrap_data.py --days 200  # 只拉最近 200 个自然日（默认 400）
  python scripts/bootstrap_data.py --limit 3   # 每市场只拉前 N 只（连通性自测用）

接口约束（实测 2026-08）:
  - 腾讯 fqkline:日K前复权/后复权都可用，volume 单位=手（bootstrap 已 ×100 转股）
  - Yahoo chart:免 key，带 UA 即可；快速连续请求会 429（脚本内置 1.2s 间隔 + 退避重试）
  - 东财 push2his 接口对本机 IP 连续请求会限流数十分钟，故不使用
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TENCENT_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
YAHOO_API = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval=1d"

# ---------------------------------------------------------------------------
# 符号表（与生产脚本一致，内嵌以避免依赖本机量化仓库）
# ---------------------------------------------------------------------------

SSE50 = [
    "600519.SH", "601318.SH", "600036.SH", "601899.SH", "600900.SH",
    "601166.SH", "600276.SH", "600030.SH", "603259.SH", "688981.SH",
    "688256.SH", "601398.SH", "688041.SH", "601211.SH", "601288.SH",
    "601328.SH", "688008.SH", "600887.SH", "600150.SH", "601816.SH",
    "601127.SH", "600031.SH", "688012.SH", "603501.SH", "601088.SH",
    "600309.SH", "601601.SH", "601668.SH", "603993.SH", "601012.SH",
    "601728.SH", "600690.SH", "600809.SH", "600941.SH", "600406.SH",
    "601857.SH", "601766.SH", "601919.SH", "600050.SH", "600760.SH",
    "601225.SH", "600028.SH", "601988.SH", "688111.SH", "601985.SH",
    "601888.SH", "601628.SH", "601600.SH", "601658.SH", "600048.SH",
]

NASDAQ100 = [
    "NVDA", "MSFT", "AAPL", "GOOG", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "NFLX",
    "PLTR", "COST", "ASML", "AMD", "CSCO", "AZN", "TMUS", "MU", "LIN", "PEP",
    "SHOP", "APP", "INTU", "AMAT", "LRCX", "PDD", "QCOM", "ARM", "INTC", "BKNG",
    "AMGN", "TXN", "ISRG", "GILD", "KLAC", "PANW", "ADBE", "HON", "CRWD", "CEG",
    "ADI", "ADP", "DASH", "CMCSA", "VRTX", "MELI", "SBUX", "CDNS", "ORLY", "SNPS",
    "MSTR", "MDLZ", "ABNB", "MRVL", "CTAS", "TRI", "MAR", "MNST", "CSX", "ADSK",
    "PYPL", "FTNT", "AEP", "WDAY", "REGN", "ROP", "NXPI", "DDOG", "AXON", "ROST",
    "IDXX", "EA", "PCAR", "FAST", "EXC", "TTWO", "XEL", "ZS", "PAYX", "WBD",
    "BKR", "CPRT", "CCEP", "FANG", "TEAM", "CHTR", "KDP", "MCHP", "GEHC", "VRSK",
    "CTSH", "CSGP", "KHC", "ODFL", "DXCM", "TTD", "ON", "BIIB", "LULU", "CDW",
    "GFS",
]

# ---------------------------------------------------------------------------
# 腾讯日 K（A股/港股通用;qfqday=前复权, day=不复权;volume 单位=手）
# ---------------------------------------------------------------------------

TENCENT_RETRIES = 3
TENCENT_BACKOFF = (3, 8, 20)


def tencent_kline(param: str, start: str, end: str) -> list:
    """拉腾讯日 K。param 形如 'sh600519' / 'hk00700'。返回 [{date, open, close, high, low, volume}]。

    volume 手 → 股（×100）。A股指数/个股用 qfqday（前复权），港股脚本按原样带 qfq。
    """
    for attempt in range(TENCENT_RETRIES):
        try:
            resp = requests.get(TENCENT_API,
                                params={"param": f"{param},day,{start},{end},320,qfq"},
                                headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                break
            node = data.get("data", {}).get(param, {})
            rows = node.get("qfqday") or node.get("day") or []
            bars = []
            for row in rows:
                if len(row) < 6:
                    continue
                bars.append({"date": row[0], "open": row[1], "close": row[2],
                             "high": row[3], "low": row[4],
                             "volume": str(round(float(row[5]) * 100))})
            return bars
        except Exception:
            pass
        if attempt < TENCENT_RETRIES - 1:
            time.sleep(TENCENT_BACKOFF[attempt])
    return []


def fetch_cn(symbol: str, start: str, end: str) -> list:
    """A股:600519.SH -> 腾讯 sh600519;688981.SH 科创板同样 sh 前缀。"""
    code = symbol.split(".")[0]
    return tencent_kline(("sh" if symbol.endswith(".SH") else "sz") + code, start, end)


# ---------------------------------------------------------------------------
# Yahoo 日 K（美股;免 key,带 UA;volume 单位=股）
# ---------------------------------------------------------------------------

YAHOO_INTERVAL = 1.2
YAHOO_RETRIES = 4
YAHOO_BACKOFF = (3, 8, 20, 45)
_last_yahoo = 0.0


def _at(q: dict, key: str, i: int):
    """取 Yahoo quote 数组第 i 个值（越界/缺失返回 None）。"""
    v = q.get(key)
    return v[i] if v and i < len(v) else None


def yahoo_range(days: int) -> str:
    """Yahoo range 只支持固定枚举（1d/5d/1mo/3mo/6mo/1y/2y/...），按天数取最小覆盖档。"""
    for limit, label in ((30, "1mo"), (90, "3mo"), (180, "6mo"), (365, "1y"), (730, "2y")):
        if days <= limit:
            return label
    return "2y"


def yahoo_kline(symbol: str, days: int) -> list:
    """拉 Yahoo chart 日 K。返回 [{date, open, close, high, low, volume}]，date 为 UTC 日。

    用 curl 子进程（requests 的 TLS 指纹会被 Yahoo 风控 403，curl 实测可用）。
    """
    global _last_yahoo
    range_s = yahoo_range(days)
    url = YAHOO_API.format(symbol, range_s)
    for attempt in range(YAHOO_RETRIES):
        wait = YAHOO_INTERVAL - (time.time() - _last_yahoo)
        if wait > 0:
            time.sleep(wait)
        try:
            proc = subprocess.run(
                ["curl", "-s", "--max-time", "20", "-A", UA, url],
                capture_output=True, text=True, timeout=30)
            _last_yahoo = time.time()
            if proc.returncode != 0 or not proc.stdout.strip():
                raise RuntimeError("curl empty/failed")
            result = (json.loads(proc.stdout).get("chart") or {}).get("result")
            if not result:
                break
            r = result[0]
            ts = r.get("timestamp") or []
            q = (r.get("indicators") or {}).get("quote") or [{}]
            q = q[0]
            bars = []
            for i, t in enumerate(ts):
                o, h, l, c, v = _at(q, "open", i), _at(q, "high", i), _at(q, "low", i), \
                                _at(q, "close", i), _at(q, "volume", i)
                if o is None or h is None or l is None or c is None:
                    continue
                bars.append({"date": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"),
                             "open": f"{o:.2f}", "high": f"{h:.2f}", "low": f"{l:.2f}",
                             "close": f"{c:.2f}", "volume": str(int(v) if v else 0)})
            return bars
        except Exception:
            pass
        if attempt < YAHOO_RETRIES - 1:
            time.sleep(YAHOO_BACKOFF[attempt])
    return []


# ---------------------------------------------------------------------------
# 输出格式（与 sync_from_quantmind.py / 现有数据文件一致）
# ---------------------------------------------------------------------------


def alpha_doc(symbol: str, series: dict, tz: str, info: str) -> dict:
    return {"Meta Data": {"1. Information": info, "2. Symbol": symbol,
            "3. Last Refreshed": sorted(series)[-1] if series else "",
            "4. Interval": "daily", "6. Time Zone": tz},
            "Time Series (Daily)": series}


def write_cn(bars_by_symbol: dict, days: int, out_root: Path) -> None:
    """A股 -> merged.jsonl(open 风格) + daily_prices_sse_50.csv + 上证50指数。"""
    out_dir = out_root / "A_stock"
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "merged.jsonl").open("w", encoding="utf-8") as f:
        for sym in sorted(bars_by_symbol):
            if not bars_by_symbol[sym]:
                continue
            ts = {b["date"]: {"1. open": b["open"], "2. high": b["high"],
                              "3. low": b["low"], "4. close": b["close"],
                              "5. volume": b["volume"]} for b in bars_by_symbol[sym]}
            f.write(json.dumps(alpha_doc(sym, ts, "Asia/Shanghai", "bootstrap tencent qfq"),
                               ensure_ascii=False) + "\n")

    with (out_dir / "daily_prices_sse_50.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount"])
        for sym in sorted(bars_by_symbol):
            prev_close = None
            for b in bars_by_symbol[sym]:
                close = float(b["close"])
                pre = prev_close if prev_close is not None else close
                change = round(close - pre, 4)
                pct = round(change / pre * 100, 4) if pre else 0.0
                w.writerow([sym, b["date"].replace("-", ""), b["open"], b["high"],
                            b["low"], b["close"], f"{pre:.2f}", change, pct,
                            b["volume"], ""])
                prev_close = close

    idx = bars_by_symbol.get("INDEX_000016")
    if idx:
        ts = {b["date"]: {"1. open": b["open"], "2. high": b["high"],
                          "3. low": b["low"], "4. close": b["close"],
                          "5. volume": b["volume"]} for b in idx}
        (out_dir / "index_daily_sse_50.json").write_text(
            json.dumps(alpha_doc("SSE50", ts, "Asia/Shanghai", "bootstrap tencent index"),
                       ensure_ascii=False), encoding="utf-8")
    n = len([s for s in bars_by_symbol if bars_by_symbol[s]])
    print(f"CN: merged.jsonl / daily_prices_sse_50.csv / index_daily_sse_50.json 已写"
          f"（{n} 只有数据，最近 {days} 日）")


def write_us(bars_by_symbol: dict, days: int, out_root: Path) -> None:
    """美股 -> data/daily_prices_{SYM}.json(buy price 风格) + Adaily_prices_QQQ.json 基准。"""
    for sym, bars in bars_by_symbol.items():
        if not bars:
            continue
        ts = {b["date"]: {"1. buy price": b["open"], "2. high": b["high"],
                          "3. low": b["low"], "4. sell price": b["close"],
                          "5. volume": b["volume"]} for b in bars}
        doc = alpha_doc(sym, ts, "US/Eastern", "bootstrap yahoo")
        (out_root / f"daily_prices_{sym}.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    n = len([s for s in bars_by_symbol if bars_by_symbol[s]])
    print(f"US: daily_prices_*.json 已写（{n} 只有数据，最近 {days} 日）")


def write_hk(merged_lines: list, days: int, out_root: Path) -> None:
    """港股 -> data/HK_stock/merged.jsonl（复用腾讯行情脚本的解析逻辑）。"""
    out_dir = out_root / "HK_stock"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "merged.jsonl").write_text("".join(merged_lines), encoding="utf-8")
    print(f"HK: merged.jsonl 已写（{len(merged_lines)} 只有数据，最近 {days} 日）")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="一键初始化三市场价格数据（免费接口）")
    ap.add_argument("--days", type=int, default=400, help="拉取最近 N 个自然日（默认 400）")
    ap.add_argument("--skip", nargs="*", default=[], choices=["cn", "us", "hk"],
                    help="跳过的市场（可多写）")
    ap.add_argument("--limit", type=int, default=0, help="每市场只拉前 N 只（连通性自测）")
    ap.add_argument("--out", type=Path, default=ROOT / "data",
                    help="输出目录（默认 data/;测试用 --out /tmp/bootstrap_test）")
    args = ap.parse_args()
    out_root = args.out

    end = datetime.now()
    start = end - timedelta(days=args.days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    fail = []

    # ---------- A股（腾讯,前复权） ----------
    if "cn" not in args.skip:
        print(f"== CN: 腾讯 SSE50（{start_s} ~ {end_s}）==")
        cn_symbols = SSE50[: args.limit] if args.limit else SSE50
        bars = {}
        for sym in cn_symbols:
            b = fetch_cn(sym, start_s, end_s)
            if b:
                bars[sym] = b
            else:
                fail.append(("CN", sym))
            print(f"  {sym}: {'✓ ' + str(len(b)) + ' 根' if b else '✗ 失败'}")
            time.sleep(0.3)
        idx = tencent_kline("sh000016", start_s, end_s)
        bars["INDEX_000016"] = idx
        print(f"  000016.SH(上证50): {'✓ ' + str(len(idx)) + ' 根' if idx else '✗ 失败'}")
        if not idx:
            fail.append(("CN", "000016.SH 指数"))
        write_cn(bars, args.days, out_root)

    # ---------- 美股（Yahoo,免 key） ----------
    if "us" not in args.skip:
        print(f"== US: Yahoo NASDAQ100（最近 {args.days} 日）==")
        us_symbols = NASDAQ100[: args.limit] if args.limit else NASDAQ100
        bars = {}
        for sym in us_symbols:
            b = yahoo_kline(sym, args.days)
            if b:
                bars[sym] = b
            else:
                fail.append(("US", sym))
            print(f"  {sym}: {'✓ ' + str(len(b)) + ' 根' if b else '✗ 失败'}")
        qqq = yahoo_kline("QQQ", args.days)
        if qqq:
            ts = {b["date"]: {"1. open": b["open"], "2. high": b["high"],
                              "3. low": b["low"], "4. close": b["close"],
                              "5. volume": b["volume"]} for b in qqq}
            (out_root / "Adaily_prices_QQQ.json").write_text(
                json.dumps(alpha_doc("QQQ", ts, "US/Eastern", "bootstrap yahoo QQQ"),
                           ensure_ascii=False), encoding="utf-8")
            print(f"  QQQ 基准: ✓ {len(qqq)} 根")
        else:
            fail.append(("US", "QQQ 基准"))
        write_us(bars, args.days, out_root)

    # ---------- 港股（腾讯,后复权;复用仓库内脚本避免重复实现） ----------
    if "hk" not in args.skip:
        print(f"== HK: 腾讯恒指权重（{start_s} ~ {end_s}）==")
        sys.path.insert(0, str(ROOT / "data" / "HK_stock"))
        try:
            from get_daily_price_hk import DEFAULT_SYMBOLS, fetch_kline, to_merged_line
        except ImportError:
            print("✗ 找不到 data/HK_stock/get_daily_price_hk.py，跳过港股")
            fail.append(("HK", "脚本缺失"))
            return 1 if fail else 0
        hk_symbols = DEFAULT_SYMBOLS[: args.limit] if args.limit else DEFAULT_SYMBOLS
        lines = []
        for sym in hk_symbols:
            try:
                bars = fetch_kline(sym, start_s, end_s)
                if bars:
                    lines.append(to_merged_line(sym, bars) + "\n")
                    print(f"  {sym}: ✓ {len(bars)} 根")
                else:
                    fail.append(("HK", sym)); print(f"  {sym}: ✗ 失败")
                time.sleep(0.3)
            except Exception as e:
                fail.append(("HK", sym)); print(f"  {sym}: ✗ {e}")
        write_hk(lines, args.days, out_root)

    # ---------- 摘要 ----------
    print(f"\n== 完成 ==")
    if fail:
        print(f"失败 {len(fail)} 项:")
        for market, sym in fail:
            print(f"  [{market}] {sym}")
        print("重跑会跳过已成功的市场/标的（--skip 已成功市场可省时间）")
        return 1
    print("全部成功。下一步: docker compose up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
