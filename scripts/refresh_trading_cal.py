#!/usr/bin/env python3
"""刷新 configs/trading_days.json 交易日清单（跨年前/新年度假日公布后运行）。

源：新浪沪深交易日清单 https://finance.sina.com.cn/realstock/company/klc_td_sh.txt
（含交易所公布的当年未来法定假日/调休口径）。解码复用 akshare 的
hk_js_decode（Apache-2.0，见 vendor/sina_td_decode.js 头部出处），需要 node 可执行。

校验：与 quantdb trading_days.parquet 重叠区间逐日核对，不一致即中止，
防止上游口径漂移。跨年后若未刷新，is_trading_day 对新年度退化为星期判断。

用法：
  python scripts/refresh_trading_cal.py [--write]
不带 --write 只抓取解码并打印校验结果（dry-run）。
"""
import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "configs" / "trading_days.json"
DECODER = ROOT / "scripts" / "vendor" / "sina_td_decode.js"
URL = "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt"


def _fetch_payload() -> str:
    import urllib.request

    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", errors="ignore")
    # var datelist="....";var KLC_TD_SH=datelist;
    return text.split("=", 1)[1].split(";", 1)[0].replace('"', "").strip()


def _decode(payload: str) -> list[str]:
    """node 执行 akshare 同源 JS 解码 → [YYYYMMDD,...]（UTC 日期无时区漂移）。"""
    js = DECODER.read_text(encoding="utf-8")
    prog = f"{js}\nconst p={json.dumps(payload)};"
    prog += "console.log(d(p).map(x=>x.toISOString().slice(0,10).replace(/-/g,'')).join('\\n'));"
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"node 解码失败: {out.stderr[:300]}")
    days = [ln.strip() for ln in out.stdout.splitlines() if len(ln.strip()) == 8]
    if len(days) < 1000:
        raise RuntimeError(f"解码结果异常（{len(days)} 天），中止")
    return days


def _quantdb_days() -> tuple[set, str | None]:
    """quantdb 交易日（截至最近同步日），无则返回空。"""
    import glob

    import duckdb

    for base in (Path.home() / "projects/quantmind/data/quantdb/2_base_sector/trading_calendar",
                 Path("/data/quantdb/2_base_sector/trading_calendar")):
        fs = sorted(glob.glob(f"{base}/*.parquet"))
        if not fs:
            continue
        rows = duckdb.connect().execute(
            "SELECT TradingDate FROM read_parquet(?)", [fs[-1]]).fetchall()
        return {str(r[0]) for r in rows}, max(str(r[0]) for r in rows)
    return set(), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="校验通过后落盘")
    a = ap.parse_args()

    print(f"抓取: {URL}")
    days = _decode(_fetch_payload())
    print(f"解码 {len(days)} 个交易日 {days[0]}..{days[-1]}")
    qd, qmax = _quantdb_days()
    if qd:
        qmin = min(qd)  # quantdb 只覆盖 2016+，sina 含 1990 起全历史 → 只比 quantdb 窗口内
        sina_win = {d for d in days if qmin <= d <= qmax}
        bad = (qd ^ sina_win) if sina_win else qd
        if bad:
            print(f"❌ 与 quantdb（截至 {qmax}）不一致 {len(bad)} 天，示例: "
                  f"{sorted(bad)[:5]} —— 中止，勿覆盖权威清单")
            return 1
        print(f"✔ 与 quantdb（截至 {qmax}）重叠区间逐日一致")

    if not a.write:
        print("dry-run 完成（加 --write 落盘）")
        return 0
    cfg = {"source": URL,
           "cross_checked": f"与 quantdb trading_days.parquet 重叠区间逐日一致（截至 {qmax or '无 quantdb'}）",
           "generated": date.today().isoformat(),
           "coverage_note": "跨年后需重新运行本脚本刷新未来假日",
           "days": days}
    OUT.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    print(f"✔ 已写入 {OUT}（{len(days)} 天）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
