#!/usr/bin/env python3
"""影子代价账读数面 —— 每条闸门**事后看**值多少钱（唯一读数入口）。

读法（写反了整份报告都会反过来）：
  * veto/unseen 事件的远期**正超额 = 这条闸在花钱**（当时放行本可以赚）、
    负超额 = 它在省钱。sell 侧闸门暂未计入（v1 只覆盖买入路径）。
  * 和 0 比没有意义："不买"天然跑平市场。参考线是**决策池同期看多基线**
    ——闸门省下的钱要和"模型自己的选股"比（见 §参考线）。

样本口径（这条不守，数字会漂亮但没用）：
  * **事件 ≠ 样本**。同一只票同一天被三个 agent 各剔一次 = 3 条事件、1 个样本；
    独立单位是 (日, 标的, 规则)——同一天同一只票的价格结果是同一个。报告两个数
    都给，**判读一律按样本数**。
  * 样本 < MIN_SAMPLE（30）只展示不结论（与决策池同一条纪律）。
  * 未到期（t5/t20/t60 还没到日子）不算"无样本"，单独计数展示。

用法：
  python scripts/ghost_report.py                 # 打印
  python scripts/ghost_report.py --out logs/ghost_scorecard.json
  python scripts/ghost_report.py --push          # 追加推 QQ（摘要一行）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as stats
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules  # noqa: E402
import ghost_ledger as GL  # noqa: E402
from decision_track import MIN_SAMPLE, load_pool  # noqa: E402

POOL = ROOT / "logs" / "decision_pool.jsonl"
HORIZONS = (1, 5, 20, 60)


def _excess(rec: dict, h: int):
    fwd = (rec.get("fwd") or {}).get(f"t{h}") or {}
    v = fwd.get("excess")
    return float(v) if isinstance(v, (int, float)) else None


def _samples(rows: list[dict]) -> list[dict]:
    """独立样本：按 (日, 标的, 规则) 去重，取首条（价格结果与 agent 无关）。"""
    seen, out = set(), []
    for r in rows:
        key = (str(r.get("date") or ""), str(r.get("code") or ""), str(r.get("rule") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def summarize(rows: list[dict]) -> dict:
    """一组事件的读数：事件数 / 样本数 / 各期限超额。"""""
    smp = _samples(rows)
    out = {"n_events": len(rows), "n_samples": len(smp),
           "kinds": sorted({str(r.get("kind") or "") for r in rows}), "horizons": {}}
    for h in HORIZONS:
        vals = [v for v in (_excess(r, h) for r in smp) if v is not None]
        out["horizons"][f"t{h}"] = {
            "n": len(vals),
            "mean": round(stats.mean(vals), 5) if vals else None,
            "median": round(stats.median(vals), 5) if vals else None,
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3) if vals else None,
        }
    out["unpriced"] = sum(1 for r in smp if _excess(r, 1) is None)
    return out


def coverage(rows: list[dict]) -> dict:
    """全池期限覆盖：按 (日, 标的) 去重——同一只票跨规则只问一次"后来涨了吗"。"""
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        seen.setdefault((str(r.get("date") or ""), str(r.get("code") or "")), r)
    out = {"n": len(seen)}
    for h in HORIZONS:
        out[f"t{h}"] = sum(1 for r in seen.values() if _excess(r, h) is not None)
    return out


def baseline(pool_path: Path = POOL, day_range: tuple[str, str] | None = None) -> dict:
    """参考线：决策池同期**看多**意向的远期超额（闸门的省/花要和它比）。

    day_range 给的是影子账样本覆盖的日期区间——不同市场环境的基线不可比。
    """
    if not pool_path.is_file():
        return {"n_samples": 0, "horizons": {}}
    rows = [r for r in load_pool(pool_path) if str(r.get("kind") or "") == "bullish"]
    if day_range:
        lo, hi = day_range
        rows = [r for r in rows if lo <= str(r.get("date") or "") <= hi]
    return summarize(rows)


def report(ghost_path: Path | None = None, pool_path: Path = POOL) -> dict:
    rows = GL.load_ghost(ghost_path)
    reg = GL.load_registry()
    days = [str(r.get("date") or "") for r in rows]
    win = (min(days), max(days)) if days else None
    doc = {
        "generated": date.today().isoformat(),
        "n_events": len(rows),
        "n_samples": len(_samples(rows)),
        "window": win,
        "min_sample": MIN_SAMPLE,
        "rules": {},
        "domains": {},
        "coverage": coverage(rows),
        "baseline": baseline(pool_path, win),
        "unregistered": [],
        "expired": GL.expired_rules(registry=reg),
        "sources": {},
    }
    for r in rows:
        src = str(r.get("source") or "?")
        doc["sources"][src] = doc["sources"].get(src, 0) + 1
    by_rule: dict[str, list] = {}
    for r in rows:
        by_rule.setdefault(str(r.get("rule") or ""), []).append(r)
    for rule, group in by_rule.items():
        s = summarize(group)
        ent = (reg.get("rules") or {}).get(rule) or {}
        s["title"] = ent.get("title") or ""
        s["registered"] = bool(ent)
        s["review_by"] = ent.get("review_by") or ""
        doc["rules"][rule] = s
    for rule in by_rule:
        d = gate_rules.domain_of(rule)
        doc["domains"].setdefault(d, []).extend(by_rule[rule])
    doc["domains"] = {d: summarize(v) for d, v in doc["domains"].items()}
    doc["unregistered"] = sorted(r for r in by_rule if r not in (reg.get("rules") or {}))
    return doc


# ---------------------------------------------------------------- 渲染

def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def render(doc: dict) -> str:
    L = []
    L.append(f"📊 影子代价账（{doc['generated']}）")
    L.append(f"事件 {doc['n_events']} 条 → 独立样本 {doc['n_samples']} 个"
             f"（同一票同一天多 agent = 1 个样本）"
             + (f"；窗口 {doc['window'][0]} ~ {doc['window'][1]}" if doc["window"] else ""))
    if doc["sources"]:
        L.append("来源：" + "，".join(f"{k}={v}" for k, v in sorted(doc["sources"].items())))
    if not doc["rules"]:
        L.append("（池是空的：接线已就绪，等第一条被拦下的买入意图）")
        return "\n".join(L)

    L.append("")
    L.append("按规则（**正超额 = 这条闸在花钱**；判读按样本数 n，样本 <"
             f"{doc['min_sample']} 只展示不结论）")
    head = f"{'规则':<26}{'类别':<8}{'事件':>5}{'样本':>5}{'t1超额':>9}{'t1中位':>9}{'胜率':>7}{'已定价':>7}"
    L.append(head)
    L.append("-" * len(head))
    for rule in gate_rules.ALL:                    # 词表顺序 = 展示序
        s = doc["rules"].get(rule)
        if not s:
            continue
        t1 = s["horizons"]["t1"]
        win = "—" if t1["win_rate"] is None else f"{t1['win_rate']:.0%}"
        L.append(f"{rule:<26}{','.join(s['kinds']):<8}{s['n_events']:>5}{s['n_samples']:>5}"
                 f"{_pct(t1['mean']):>9}{_pct(t1['median']):>9}{win:>7}{t1['n']:>7}")
    cov = doc.get("coverage") or {}
    if cov:
        L.append("期限覆盖（按 日×标的 去重，n="
                 f"{cov.get('n', 0)}）："
                 + " · ".join(f"t{h} {cov.get(f't{h}', 0)}/{cov.get('n', 0)}" for h in HORIZONS)
                 + "（未到期 = 还没到日子，不是没样本）")
    L.append("")
    L.append("按域（同域的花费才可比）")
    for d, s in sorted(doc["domains"].items()):
        t1 = s["horizons"]["t1"]
        L.append(f"  {d:<10} 事件 {s['n_events']:>3}  样本 {s['n_samples']:>3}  "
                 f"t1 超额 {_pct(t1['mean'])}")
    L.append("")
    b = doc["baseline"]
    bt1 = (b.get("horizons") or {}).get("t1") or {}
    L.append(f"参考线（决策池同期看多基线）：样本 {b.get('n_samples', 0)}，"
             f"t1 超额 {_pct(bt1.get('mean'))}"
             + ("（基线样本不足，暂不可比）" if b.get("n_samples", 0) < doc["min_sample"] else ""))
    L.append("  读法：unseen（候选没进模型视野）是**弱反事实**——「假设模型会选它」"
             "本身没有证据；veto（模型明确想买被拦）才是强反事实。两类不可混算。")
    L.append("  —— 判读「省还是花」：**拦掉的比基线差**才算省；比基线好 = 拦错了。")
    unpriced = sum(s["unpriced"] for s in doc["rules"].values())
    if unpriced:
        L.append(f"未定价（t1 还没到期或行情缺失）：{unpriced} 个样本——是「还没有答案」，"
                 "不是「没发生」。")
    if doc["unregistered"]:
        L.append(f"⚠️ 未登记规则（有人加了闸没进 configs/gate_registry.json）：{doc['unregistered']}")
    for rule, rb in doc["expired"]:
        L.append(f"⚠️ 复核已过期：{rule}（复核日 {rb}）——规则默认会过期，不默认正确。")
    return "\n".join(L)


def push_line(doc: dict) -> str:
    """QQ 摘要：一行说清"哪条闸花了/省了多少钱"（没有可读数字时不说）。"""
    parts = []
    for rule in gate_rules.ALL:
        s = doc["rules"].get(rule)
        if not s:
            continue
        t1 = s["horizons"]["t1"]
        if not t1["n"]:
            continue
        parts.append(f"{rule} n={s['n_samples']} t1={_pct(t1['mean'])}")
    head = f"影子代价账 {doc['n_events']} 事件/{doc['n_samples']} 样本"
    return head + ("：" + "；".join(parts) if parts else "（尚无可读数字）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影子代价账读数面")
    ap.add_argument("--path", default="", help="影子账路径（默认 logs/ghost_ledger.jsonl）")
    ap.add_argument("--out", default="", help="把读数写成 JSON（原子写）")
    ap.add_argument("--push", action="store_true", help="把摘要推送到 QQ")
    args = ap.parse_args(argv)

    doc = report(Path(args.path) if args.path else None)
    text = render(doc)
    print(text)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)          # 原子替换：读到一半的 JSON 比没有更坏
        print(f"✅ 已写入 {p}")
    if args.push:
        from push_notify import notify

        notify("影子代价账", push_line(doc))
        print("📤 已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
