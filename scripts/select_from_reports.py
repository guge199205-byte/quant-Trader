#!/usr/bin/env python3
"""从历史市场分析报告选股（供实盘下单使用）。

数据源（优先级）：
  1. quantmind data/reports/stock_picks/{YYYYMMDD}_picks.json — 结构化候选（裸 6 位代码）
  2. quantmind data/reports/market_analysis/{YYYY-MM-DD}_report.md — 资金流表（SH600xxx 前缀）
  3. quantmind data/reports/daily_review/{YYYY-MM-DD}_facts.md — 信号表（点号式代码）

用法：
  python scripts/select_from_reports.py                 # 最新一期 picks
  python scripts/select_from_reports.py --date 20260821 # 指定日期
  python scripts/select_from_reports.py --source md     # 从 market_analysis md 提取
  python scripts/select_from_reports.py --top 10        # 取前 N 只
  python scripts/select_from_reports.py --min-side BUY  # 只输出 side>=BUY 的候选
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPORTS = Path("/home/zbox/projects/quantmind/data/reports")


def normalize_stock_code(code: str) -> str:
    """600519 / 600519.SH / SH600519 / 600519_SZ → 600519.SH 格式。
    非股票文本（+3.96%、板块名等）一律返回空串。"""
    s = str(code or "").strip().upper()
    if not s:
        return ""
    # 前缀式 SH600183 / SZ300750
    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    # 文件名式 300750_SZ
    m = re.fullmatch(r"(\d{6})_(SH|SZ|BJ)", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    # 点号式 600519.SH（严格格式才接受）
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    # 裸 6 位
    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("6", "9", "5")):
            return f"{s}.SH"
        return f"{s}.SZ"
    return ""


SIDE_RANK = {"BUY": 3, "ADD": 2, "HOLD": 1, "SELL": 0, "REDUCE": 0}


def load_picks(picks_file: Path) -> list[dict]:
    """picks.json → [{code, name, industry, side, score, fusion, rank}]"""
    data = json.loads(picks_file.read_text(encoding="utf-8"))
    out = []
    for c in data.get("candidates") or []:
        code = normalize_stock_code(c.get("symbol", ""))
        if not code:
            continue
        out.append({
            "code": code,
            "name": c.get("name", ""),
            "industry": c.get("industry", ""),
            "side": c.get("side", "HOLD"),
            "score": float(c.get("score") or 0),
            "fusion": float(c.get("fusion") or 0),
            "rank": c.get("rank"),
            "source": f"picks:{picks_file.name}",
        })
    return out


def _is_index(code: str) -> bool:
    """过滤大盘指数：SH 数字 < 600000（000001 上证、000300 沪深300 等）、SZ 399xxx、BJ 899xxx"""
    num = code.split(".")[0]
    return (code.endswith(".SH") and int(num) < 600000) or \
        num.startswith(("399", "899"))


def load_from_md(md_file: Path) -> list[dict]:
    """market_analysis md 资金流表（| 生益科技 | SH600183 | 11.01 | ...）"""
    out = []
    for line in md_file.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 3:
            continue
        code = normalize_stock_code(cells[1])
        if not code or _is_index(code):
            continue
        out.append({
            "code": code,
            "name": cells[0],
            "industry": "",
            "side": "HOLD",
            "score": 0,
            "fusion": 0,
            "rank": None,
            "source": f"md:{md_file.name}",
        })
    return out


def latest_picks_file() -> Path:
    files = sorted(REPORTS.glob("stock_picks/*_picks.json"))
    return files[-1] if files else None


def latest_md_file() -> Path:
    files = sorted(REPORTS.glob("market_analysis/*_report.md"))
    return files[-1] if files else None


def main() -> None:
    ap = argparse.ArgumentParser(description="从历史市场分析报告选股")
    ap.add_argument("--date", help="picks 日期 YYYYMMDD（默认最新一期）")
    ap.add_argument("--source", choices=["picks", "md"], default="picks",
                    help="数据源：picks=结构化候选（默认），md=市场分析资金流表")
    ap.add_argument("--top", type=int, default=0, help="只输出前 N 只（按 score 排序）")
    ap.add_argument("--min-side", choices=["BUY", "ADD", "HOLD"],
                    help="最低 side 门槛（BUY=只出买入信号）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（供管道消费）")
    args = ap.parse_args()

    if args.source == "picks":
        picks_file = latest_picks_file()
        if args.date:
            picks_file = REPORTS / "stock_picks" / f"{args.date}_picks.json"
        if not picks_file or not picks_file.exists():
            sys.exit(f"未找到 picks 报告: {picks_file}")
        picks = load_picks(picks_file)
        print(f"# 数据源: {picks_file}（{date.today()} 读取）", file=sys.stderr)
    else:
        md_file = latest_md_file()
        if not md_file or not md_file.exists():
            sys.exit(f"未找到 market_analysis md: {md_file}")
        picks = load_from_md(md_file)
        print(f"# 数据源: {md_file}", file=sys.stderr)

    if args.min_side:
        picks = [p for p in picks if SIDE_RANK.get(p["side"], 0) >= SIDE_RANK[args.min_side]]
    if args.top:
        picks = sorted(picks, key=lambda p: (SIDE_RANK.get(p["side"], 0), p["score"]),
                       reverse=True)[: args.top]

    if args.json:
        print(json.dumps(picks, ensure_ascii=False, indent=1))
        return
    print(f"{'代码':<12}{'名称':<10}{'side':<6}{'得分':<8}行业")
    for p in picks:
        print(f"{p['code']:<12}{p['name']:<10}{p['side']:<6}{p['score']:<8.3f}{p['industry']}")
    print(f"# 共 {len(picks)} 只", file=sys.stderr)


if __name__ == "__main__":
    main()
