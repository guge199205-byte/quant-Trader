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

from picks_source import (KIND_AGENT, freshness_note,  # noqa: E402
                          parse_picks_name)

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
    """picks.json → [{code, name, industry, side, score, fusion, rank}]

    兼容两种写入方结构：
    1. quantmind postmarket：{candidates:[{symbol,name,industry,side,score,fusion,rank}]}
    2. baymax night_pool_agent：{picks:[{code,name,side,score,events,reason}]}
       （2026-09-06 起两者可能互为"最新"——周日晚/长假末夜研究会刷新 agent_picks，
        格式不兼容会让周一 09:35 读到空池直接终止）
    """
    data = json.loads(picks_file.read_text(encoding="utf-8"))
    out = []
    cands = data.get("candidates")
    if cands is None and data.get("picks") is not None:
        # night_pool 结构：code 已带后缀，score 为晚间研究复合分（与 quantmind 0~1 不同量纲，
        # 但 select 只做文件内排序；events/reason 透传给池行备注）
        for i, c in enumerate(data["picks"], 1):
            code = normalize_stock_code(c.get("code", ""))
            if not code:
                continue
            out.append({
                "code": code,
                "name": c.get("name", ""),
                "industry": c.get("industry", ""),
                "side": str(c.get("side", "HOLD")).upper(),
                "score": float(c.get("score") or 0),
                "fusion": float(c["fusion"]) if c.get("fusion") is not None else None,
                "rank": i,
                "remark": "；".join(list(filter(None, (
                    str(c.get("events") or ""), str(c.get("reason") or "")))))[:140],
                "source": f"picks:{picks_file.name}",
            })
        return out
    for c in cands or []:
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
            "remark": str(c.get("reason") or "")[:140] if c.get("reason") else "",
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
    """最新一期池文件（**按文件名日期**取最新，同日晚间研究池优先）。

    2026-09-12 P1-3：选择逻辑统一收进 picks_source（原来这里 sorted(glob)[-1]
    裸取字典序最后：同日 agent 池被 quantmind 池压过、最新一期缺失无人知晓）。
    """
    from picks_source import latest_picks

    info = latest_picks(REPORTS / "stock_picks")
    return info[0] if info else None


def picks_file_for_date(date8: str) -> Path:
    """--date 指定日的池文件：`{d}_agent_picks.json` 优先，回退 `{d}_picks.json`。"""
    d = REPORTS / "stock_picks"
    for name in (f"{date8}_agent_picks.json", f"{date8}_picks.json"):
        if (d / name).is_file():
            return d / name
    return d / f"{date8}_picks.json"


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
        picks_file = picks_file_for_date(args.date) if args.date else latest_picks_file()
        if not picks_file or not picks_file.exists():
            sys.exit(f"未找到 picks 报告: {picks_file}")
        picks = load_picks(picks_file)
        # 数据源行：池日期/类型 + 新鲜度——滞后必须显式（2026-09-12 P1-3），
        # 下游（load_pool 提示词/log.jsonl）另走 picks_source.pool_meta。
        src = f"# 数据源: {picks_file.name}（{date.today()} 读取）"
        parsed = parse_picks_name(picks_file.name)
        if parsed:
            d8, kind = parsed
            src = (f"# 数据源: {picks_file.name}（池日期 {d8}，"
                   f"{'晚间研究池' if kind == KIND_AGENT else '盘后池'}；"
                   f"{date.today()} 读取）")
            if not args.date:
                note = freshness_note(d8, kind)
                if note:
                    src += f"\n{note}"
        print(src, file=sys.stderr)
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
