#!/usr/bin/env python3
"""给已有 report.json 补指标序列：重跑一遍沙箱回测（不调模型）。

指标是**运行时**捕获的（``backend/services/lab_indicators.py``），老报告里没有这段数据，
界面就画不出 K 线上的 MACD/EMA。这里把报告重跑一遍补上——执行仍走宿主的 bwrap 沙箱
（只读根 + 断网 + 只挂该策略目录可写），容器侧一个字节的模型代码都不跑。

安全：``validate()`` 只在成功后写盘，所以跑失败的报告原样保留，不会把好数据弄丢。

用法::

    pine_lab/.venv/bin/python scripts/pine_backfill_indicators.py --workers 8
    ... --ids 0002,0858     # 只补这几条
    ... --missing-only      # 只补还没有指标的（断点续跑）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pine_transpile_worker as worker  # noqa: E402  沙箱命令只此一份，别在这重写

OUT_DIR = worker.OUT_DIR


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _last_line(text: str) -> str:
    for ln in reversed((text or "").strip().splitlines()):
        if ln.strip():
            return ln.strip()[:200]
    return ""


def _job(req: tuple[str, str, str]) -> tuple[str, str, str]:
    """子进程：沙箱重跑一条回测 → (id, 状态, 说明)。"""
    item_id, symbol, adj = req
    item_dir = OUT_DIR / item_id
    cmd = worker.sandbox_cmd(item_dir, ["--id", item_id, "--out", str(item_dir),
                                        "--no-transpile", "--validate", symbol,
                                        "--adj", adj])
    try:
        got = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             timeout=worker.BACKTEST_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return item_id, "timeout", f">{worker.BACKTEST_TIMEOUT}s"
    if got.returncode != 0:
        return item_id, "failed", _last_line(got.stdout + "\n" + got.stderr)
    ind = _read_json(item_dir / "report.json").get("indicators") or {}
    n = len(ind.get("overlays") or []) + len(ind.get("panes") or [])
    return item_id, "ok", f"{n} 条序列"


def _pick(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    else:
        ids = sorted(d.name for d in OUT_DIR.iterdir()
                     if d.is_dir() and (d / "report.json").exists())
    todo: list[tuple[str, str, str]] = []
    for item_id in ids:
        d = OUT_DIR / item_id
        rep = _read_json(d / "report.json")
        if not (d / "candidate.py").exists():
            print(f"跳过 {item_id}：没有 candidate.py")
        elif rep.get("symbol") != args.symbol or rep.get("adj") != args.adj:
            print(f"跳过 {item_id}：报告口径 {rep.get('symbol')}/{rep.get('adj')}")
        elif args.missing_only and rep.get("indicators"):
            pass
        else:
            todo.append((item_id, args.symbol, args.adj))
    return todo


def main() -> int:
    ap = argparse.ArgumentParser(description="重跑沙箱回测，给老报告补指标序列")
    ap.add_argument("--symbol", default="600309.SH", help="报告的口径标的（须与报告一致）")
    ap.add_argument("--adj", default="backward")
    ap.add_argument("--ids", help="逗号分隔；默认所有已有报告的策略")
    ap.add_argument("--missing-only", action="store_true", help="跳过已有指标的（断点续跑）")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    todo = _pick(args)
    if not todo:
        print("没有要补的报告")
        return 0
    print(f"待补 {len(todo)} 条，{args.workers} 个并发…")
    bad: list[tuple[str, str, str]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_job, req) for req in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            item_id, status, detail = fut.result()
            if status != "ok":
                bad.append((item_id, status, detail))
            print(f"[{i}/{len(todo)}] {item_id} {status} {detail}")
    if bad:
        print(f"\n失败 {len(bad)} 条（原报告保留）：")
        for item_id, status, detail in bad:
            print(f"  {item_id} {status} {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
