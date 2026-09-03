#!/usr/bin/env python3
"""日志治理（P1-3）：滚动清理产物目录（默认保留 30 天）+ 过期任务清理。
用法：python scripts/log_cleanup.py [--days 30] [--dry]
cron：每周日 北京 05:00。
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 产物目录（按 mtime 清理旧文件，保留目录结构）
TARGETS = [
    ROOT / "logs" / "review",          # 盘后复盘
    ROOT / "logs" / "night_pool",      # 晚间研究
    ROOT / "logs" / "debates",         # 分歧仲裁
    ROOT / "logs" / "daily_report",    # 系统日报
    ROOT / "logs" / "budget",          # 风险预算明细
    ROOT / "logs" / "min_snapshots",   # 自采分钟序列（TdxAiData 备胎）
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400
    removed = 0
    for d in TARGETS:
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                if not a.dry:
                    f.unlink()
                print(f"{'[dry] ' if a.dry else ''}清理 {f.relative_to(ROOT)}")
                removed += 1
    # analysis_jobs 终态 >3 天清理
    jf = ROOT / "logs" / "analysis_jobs.jsonl"
    if jf.is_file():
        import json

        rows = []
        for l in jf.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            if r.get("status") == "pending" or (r.get("done_ts") or "") >= \
                    time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() - 3 * 86400)):
                rows.append(r)
        if not a.dry and len(rows) != sum(1 for _ in jf.read_text(encoding="utf-8").splitlines() if _.strip()):
            jf.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                          encoding="utf-8")
    print(f"✅ {'dry-run: ' if a.dry else ''}共清理 {removed} 个过期文件（>{a.days} 天）")
    return 0


if __name__ == "__main__":
    sys.exit(main())