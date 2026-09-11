#!/usr/bin/env python3
"""长期排除清单报告：把当日各层买入硬拦汇总成可读表格（md + csv）。

区分两个概念：
  - **长期排除**（本表主体）：黑名单 / 财务差 / 长期下跌 / 长期横盘 / 流动性枯竭 /
    次新股 / 低价股 —— 由结构性判据产生，只有基本面或走势真正改善才出列；
  - **临时事件**（另一节）：解禁 / 负面新闻 / 立案 —— 有明确失效日，过期自动放行。

只读已落盘的产物（configs/live_symbols.json、data/fundamental_flags.json、
data/risk_block.json），不重算任何判据——保证报表与闸门**同源**。

用法：
  python scripts/exclusion_report.py            # 写 data/长期排除清单_<日期>.md|.csv
  python scripts/exclusion_report.py --print    # 只打印摘要
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SYMBOLS = ROOT / "configs" / "live_symbols.json"
FUND = ROOT / "data" / "fundamental_flags.json"
RISK = ROOT / "data" / "risk_block.json"
BJ = timezone(timedelta(hours=8))

# 长期层：fundamental_flags 的 flag → 中文层名（顺序即表格列序）
FUND_LAYERS = [("fin", "财务差"), ("shell", "保壳"), ("trend", "长期下跌"),
               ("flat", "横盘"), ("illiquid", "流动性"), ("new", "次新股")]


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def load_layers() -> dict:
    """{code6: {"name": str, "layers": [str], "reasons": [str]}}（长期排除各层）。"""
    out: dict = {}

    def _add(code, layer, reason):
        c6 = str(code or "").split(".")[0]
        if len(c6) != 6 or not c6.isdigit():
            return
        it = out.setdefault(c6, {"layers": [], "reasons": []})
        if layer not in it["layers"]:
            it["layers"].append(layer)
        if reason and reason not in it["reasons"]:
            it["reasons"].append(reason)

    for sym in _read(SYMBOLS).get("block_buy") or []:
        _add(sym, "永久黑名单", "新闻复核入永久黑名单")

    doc = _read(FUND)
    for code, it in (doc.get("items") or {}).items():
        flags = it.get("flags") or []
        reason = str(it.get("reason") or "")
        for tag, label in FUND_LAYERS:
            if tag in flags:
                _add(code, label, reason)

    for code, it in (_read(RISK).get("items") or {}).items():
        if "penny" in _kinds(it):
            _add(code, "低价股", str(it.get("reason") or ""))
    return out


# risk_list 合并多层时 kind 是 "penny+weak" 这样的拼接串，必须按成分判而不是等值判。
LAYER_KINDS = {"unlock", "news", "grave"}


def _kinds(it: dict) -> set:
    return {k for k in str(it.get("kind") or "").split("+") if k}


def load_transient() -> list:
    """临时事件条目 [(code, kind, reason, expire)]（解禁/新闻/立案）。"""
    out = []
    for code, it in (_read(RISK).get("items") or {}).items():
        ks = _kinds(it) & LAYER_KINDS
        c6 = str(code).split(".")[0]
        if ks and len(c6) == 6 and c6.isdigit():
            out.append((c6, "+".join(sorted(ks)), str(it.get("reason") or ""),
                        str(it.get("expire") or "")))
    return sorted(out, key=lambda r: (r[1], r[0]))


def _names() -> dict:
    try:
        from news_blacklist_scan import load_name_index

        _idx, by_code = load_name_index()
        return {str(k).split(".")[0]: v for k, v in by_code.items()}
    except Exception:  # noqa: BLE001 名称表拿不到不影响清单本体
        return {}


def build(names: dict) -> tuple:
    layers = load_layers()
    order = ["永久黑名单", "财务差", "保壳", "长期下跌", "横盘", "流动性", "次新股", "低价股"]
    rows = []
    for code, it in layers.items():
        ls = sorted(it["layers"], key=lambda x: order.index(x) if x in order else 99)
        rows.append({"code": code, "name": names.get(code, ""), "layers": ls,
                     "reason": "；".join(it["reasons"])})
    rows.sort(key=lambda r: (-len(r["layers"]), r["code"]))
    return rows, load_transient()


def write(rows: list, transient: list, names: dict, asof: str) -> tuple:
    md = ROOT / "data" / f"长期排除清单_{asof}.md"
    csv_p = ROOT / "data" / f"长期排除清单_{asof}.csv"
    total = len(rows)
    pct = total / 5563 * 100
    lines = [f"# 长期排除清单（买入硬拦，{asof}）", "",
             f"共 {total} 只（全市场 5563 只的 {pct:.1f}%）。卖出不受限；本表随每日闸门刷新",
             "（重跑：python scripts/exclusion_report.py）。", "",
             "| # | 代码 | 名称 | 命中层数 | 层 | 理由 |", "|---:|---|---|---:|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['code']} | {r['name']} | {len(r['layers'])} | "
                     f"{'/'.join(r['layers'])} | {r['reason']} |")
    lines += ["", f"## 临时事件（{len(transient)} 只，到期自动放行，不计入上表）", "",
              "| 代码 | 名称 | 类型 | 失效日 | 理由 |", "|---|---|---|---|---|"]
    for code, kind, reason, expire in transient:
        lines.append(f"| {code} | {names.get(code, '')} | {kind} | {expire[:10]} | {reason} |")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cols = ["永久黑名单", "财务差", "长期下跌", "横盘", "流动性", "次新股", "低价股"]
    with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["代码", "名称", "命中层数", *cols, "理由"])
        for r in rows:
            w.writerow([r["code"], r["name"], len(r["layers"]),
                        *["✓" if o in r["layers"] else "" for o in cols], r["reason"]])
    return md, csv_p


def main() -> int:
    ap = argparse.ArgumentParser(description="长期排除清单报告")
    ap.add_argument("--print", dest="print_only", action="store_true", help="只打印摘要")
    a = ap.parse_args()
    names = _names()
    rows, transient = build(names)
    asof = datetime.now(BJ).date().isoformat()
    from collections import Counter

    c = Counter(layer for r in rows for layer in r["layers"])
    print(f"长期排除 {len(rows)} 只（{len(rows) / 5563 * 100:.1f}%）："
          + " / ".join(f"{k} {v}" for k, v in c.most_common())
          + f" | 临时事件 {len(transient)} 只")
    if not a.print_only:
        md, csv_p = write(rows, transient, names, asof)
        print(f"→ {md}\n→ {csv_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
