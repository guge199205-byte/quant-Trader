#!/usr/bin/env python3
"""Pine 转写/回测链路抽样审计：按分类分层抽样，跑真实队列，统计通过率与修复轮数。

衡量的是**链路健康度**（转写→静态闸→沙箱回测能不能跑完），不是策略好坏——同一个标的、
同一段行情，跑出负收益也算通过。

用法::

    .venv/bin/python scripts/pine_pipeline_audit.py --n 30      # 抽样并写入队列
    .venv/bin/python scripts/pine_transpile_worker.py           # 宿主 worker 消费
    .venv/bin/python scripts/pine_pipeline_audit.py --report    # 汇总最新一批

产物：``data/pine_audit/sample-<ts>.json``（样本清单）与 ``report-<ts>.json``（汇总）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIB_DIR = ROOT / "data/pine_library"
AUDIT_DIR = ROOT / "data/pine_audit"
TRANSPILE_DIR = ROOT / "data/pine_transpile"
QUEUE_DIR = TRANSPILE_DIR / "queue"
SEED = 20260909


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _unique(path: Path) -> Path:
    """同一秒里连跑两次（回归 + 抽样）不能互相覆盖——真实踩过，回归清单被抽样清单顶掉。"""
    n = 2
    while path.exists():
        path = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """把 n 个名额按分类规模分摊，每类至少 1（n 小于类数时只取最大的几类）。"""
    names = sorted(counts, key=lambda k: (-counts[k], k))
    if n <= len(names):
        return {k: 1 for k in names[:n]}
    total = sum(counts.values())
    quota = {k: n * counts[k] / total for k in names}
    alloc = {k: max(1, int(quota[k])) for k in names}
    while sum(alloc.values()) < n:
        alloc[max(names, key=lambda k: (quota[k] - alloc[k], counts[k]))] += 1
    while sum(alloc.values()) > n:
        shrinkable = [k for k in names if alloc[k] > 1]
        alloc[min(shrinkable, key=lambda k: (quota[k] - alloc[k], -counts[k]))] -= 1
    return alloc


def pick(n: int, seed: int = SEED) -> list[dict]:
    """分层抽样：分类内随机、跨分类按规模分摊名额，确定性（同一 seed 同一结果）。"""
    items = _read_json(LIB_DIR / "index.json").get("items") or []
    if not items:
        raise SystemExit("索引为空，先跑 scripts/pine_library_index.py")
    # 已经跑过的不再抽，避免用旧结果充数
    fresh = [it for it in items if not (TRANSPILE_DIR / it["id"] / "job.json").exists()]
    by_cat: dict[str, list[dict]] = {}
    for it in fresh:
        by_cat.setdefault(it.get("category") or "未分类", []).append(it)

    slots = allocate({k: len(v) for k, v in by_cat.items()}, n)
    out: list[dict] = []
    for cat, k in slots.items():
        cands = sorted(by_cat[cat], key=lambda it: it["id"])
        out += random.Random(f"{seed}:{cat}").sample(cands, min(k, len(cands)))
    return sorted(out, key=lambda it: it["id"])


def enqueue(sample: list[dict], symbol: str, adj: str, force: bool = False) -> Path:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    for it in sample:
        req = {"id": it["id"], "symbol": symbol, "adj": adj, "model": "", "force": force,
               "requested": _now()}
        tmp = QUEUE_DIR / f"{it['id']}.json.tmp"
        tmp.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        tmp.replace(QUEUE_DIR / f"{it['id']}.json")
    path = _unique(AUDIT_DIR / f"sample-{_stamp()}.json")
    path.write_text(json.dumps({"created": _now(), "symbol": symbol, "adj": adj,
                                "seed": SEED, "ids": [it["id"] for it in sample],
                                "items": sample}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def failed_ids(report: Path) -> list[str]:
    """某次报告里没跑通的 id——修完管线拿它们回归，比重新抽样更能看出改动有没有效。

    「通过（0 成交）」不算：链路是通的，多半是策略本身在这个标的上不触发，重转写也白搭。
    """
    return [r["id"] for r in _read_json(report).get("rows") or []
            if not (r.get("verdict") or "").startswith("通过") and r.get("id")]


def classify(job: dict, trades: int | None = None) -> str:
    """失败归类——汇总里要能一眼看出主要卡在哪。

    ``trades == 0`` 的「通过」单独算一类：链路是通的，但很可能转写时把开仓条件弄丢了，
    混在通过里会把成功率说高。
    """
    stage = job.get("stage") or "?"
    err = (job.get("error") or "") + " " + " ".join(job.get("problems") or [])
    if stage == "done":
        return "通过（0 成交）" if trades == 0 else "通过"
    if "max_tokens 截断" in err:
        return "转写截断"
    if stage == "static" or "静态检查未通过" in err:
        return "静态检查不过"
    if "TypeError" in err:
        return "运行时 TypeError"
    if "超时" in err:
        return f"{stage}超时"
    if stage in ("transpile", "repair", "backtest", "worker"):
        return f"{stage}失败"
    return "其他失败"


def _error_of(job: dict) -> str:
    """失败原因优先给静态检查的第一条具体问题——「静态检查未通过」本身没有信息量。"""
    problems = job.get("problems") or []
    if problems:
        return "；".join(problems[:2])[:160]
    return (job.get("error") or "")[:160]


def summarize(manifest: Path) -> dict:
    data = _read_json(manifest)
    ids = data.get("ids") or []
    rows = []
    for iid in ids:
        job = _read_json(TRANSPILE_DIR / iid / "job.json")
        meta = _read_json(TRANSPILE_DIR / iid / "meta.json")
        rep = _read_json(TRANSPILE_DIR / iid / "report.json")
        stats = rep.get("stats") or {}
        trades = rep.get("trades")
        rows.append({
            "id": iid,
            "category": next((it.get("category") for it in data.get("items") or []
                              if it["id"] == iid), ""),
            "title": (meta.get("title") or "")[:28],
            "status": job.get("status") or "未跑",
            "verdict": classify(job, trades),
            "repairs": len(meta.get("repairs") or []),
            "think": meta.get("think"),
            "transpile_sec": meta.get("elapsed_sec"),
            "net_pct": (stats.get("Net profit") or {}).get("pct"),
            "trades": trades,
            "error": _error_of(job),
        })

    done = [r for r in rows if r["status"] == "done"]
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["verdict"]] = buckets.get(r["verdict"], 0) + 1
    times = [r["transpile_sec"] for r in rows if r["transpile_sec"]]
    return {
        "sample": str(manifest), "symbol": data.get("symbol"), "total": len(rows),
        "done": len(done), "first_try": sum(1 for r in done if r["repairs"] == 0),
        "after_repair": sum(1 for r in done if r["repairs"] > 0),
        "zero_trade": sum(1 for r in done if r["trades"] == 0),
        "think_fallback": sum(1 for r in rows if r["think"]),
        "avg_transpile_sec": round(sum(times) / len(times), 1) if times else None,
        "buckets": buckets, "rows": rows,
        "finished": len(rows) > 0 and all(r["status"] in ("done", "failed") for r in rows),
    }


def print_report(rep: dict) -> None:
    print(f"样本 {rep['total']} 条 | 标的 {rep['symbol']} | 完成 {rep['done']} | "
          f"首轮通过 {rep['first_try']} | 修复后通过 {rep['after_repair']} | "
          f"零成交 {rep.get('zero_trade', 0)} | 平均转写 {rep['avg_transpile_sec']}s")
    print("结果分布：" + "，".join(f"{k}={v}" for k, v in
                                   sorted(rep["buckets"].items(), key=lambda kv: -kv[1])))
    print()
    print(f"{'id':6s} {'分类':10s} {'结论':14s} {'修复':>4s} {'转写s':>6s} {'净收益%':>8s} {'笔数':>5s}  标题")
    for r in rep["rows"]:
        net = f"{r['net_pct']:.1f}" if isinstance(r["net_pct"], (int, float)) else "—"
        print(f"{r['id']:6s} {r['category'][:8]:10s} {r['verdict']:14s} "
              f"{r['repairs']:>4d} {str(r['transpile_sec'] or '—'):>6s} {net:>8s} "
              f"{str(r['trades'] if r['trades'] is not None else '—'):>5s}  {r['title']}")
    bad = [r for r in rep["rows"] if r["verdict"] != "通过"]
    if bad:
        print("\n失败明细：")
        for r in bad:
            print(f"  {r['id']} [{r['verdict']}] {r['error']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pine 转写/回测链路抽样审计")
    ap.add_argument("--n", type=int, default=30, help="抽样条数（默认 30）")
    ap.add_argument("--symbol", default="600309.SH")
    ap.add_argument("--adj", default="backward")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--report", action="store_true", help="汇总最新一批样本（不入队）")
    ap.add_argument("--sample", metavar="PATH", help="配合 --report：指定清单文件（默认取最新）")
    ap.add_argument("--retry", metavar="REPORT", help="把该报告里没通过的 id 重新入队（强制重转写）")
    args = ap.parse_args()

    if args.retry:
        index = {it["id"]: it for it in _read_json(LIB_DIR / "index.json").get("items") or []}
        sample = [index[i] for i in failed_ids(Path(args.retry)) if i in index]
        path = enqueue(sample, args.symbol, args.adj, force=True)
        print(f"回归 {len(sample)} 条（强制重转写）→ {path}")
        for it in sample:
            print(f"  {it['id']}  {it.get('category','')[:10]:12s} {it.get('title','')[:40]}")
        print("\n下一步：.venv/bin/python scripts/pine_transpile_worker.py")
        return 0

    if args.report:
        manifests = sorted(AUDIT_DIR.glob("sample-*.json"))
        if args.sample:
            manifest = Path(args.sample)
        elif manifests:
            manifest = manifests[-1]
        else:
            print("还没有样本清单", file=sys.stderr)
            return 1
        rep = summarize(manifest)
        print_report(rep)
        out = _unique(AUDIT_DIR / f"report-{_stamp()}.json")
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总已存 {out}")
        return 0 if rep["finished"] else 2

    sample = pick(args.n, seed=args.seed)
    path = enqueue(sample, args.symbol, args.adj)
    print(f"抽样 {len(sample)} 条 → {path}")
    for it in sample:
        print(f"  {it['id']}  {it.get('category','')[:10]:12s} {it.get('title','')[:40]}")
    print("\n下一步：.venv/bin/python scripts/pine_transpile_worker.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
