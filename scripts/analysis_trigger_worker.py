#!/usr/bin/env python3
"""手动分析触发 worker：消费 logs/analysis_jobs.jsonl 的 pending 任务。

前端「对话 tab → 立即分析」→ api_server 写任务行 → 本 worker（cron 每
分钟）拉起 live_hourly_analysis.py 执行：
  - 交易时段内：与整点分析同权（闸门/执行开关一致，时段内会真下单）
  - 盘前/盘后/午休：--force 只出决策记录对话，不真下单（force 禁执行）
管线自身 _try_lock 防叠跑；管线忙时任务留 pending 下轮自动补跑。
"""
import fcntl
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

JOBS = ROOT / "logs" / "analysis_jobs.jsonl"
LOCK = ROOT / "logs" / ".analysis_worker.lock"
TIMEOUT_S = 900
BJ = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(BJ).isoformat(timespec="seconds")


def load() -> list:
    if not JOBS.is_file():
        return []
    try:
        return [json.loads(l) for l in JOBS.read_text(encoding="utf-8").splitlines()
                if l.strip()]
    except (OSError, ValueError):
        return []


def save(rows: list) -> None:
    JOBS.parent.mkdir(exist_ok=True)
    tmp = JOBS.with_name(JOBS.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(JOBS)


def main() -> int:
    rows = load()
    pend = [r for r in rows if r.get("status") == "pending"]
    if not pend:
        return 0

    try:
        lock = LOCK.open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # 上一轮未结束，保持 pending 下轮再试

    try:
        from live_hourly_analysis import in_trading_window

        now = datetime.now(BJ)
        in_window = in_trading_window(now)
        for r in pend:
            agents = r.get("agents") or "all"
            names = "" if agents == "all" else ",".join(agents)
            r["status"] = "running"
            r["start_ts"] = _now()
            save(rows)

            cmd = [sys.executable, str(ROOT / "scripts" / "live_hourly_analysis.py")]
            if names:
                cmd += ["--agents", names]
            if not in_window:
                cmd.append("--force")  # 盘外：只出决策不下单
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=TIMEOUT_S)
                out = (proc.stdout or "") + (proc.stderr or "")
                tail = " | ".join(out.strip().splitlines()[-2:])[:200]
                if "已有分析实例在跑" in out:
                    r["status"] = "pending"
                    r["note"] = "分析实例正忙，留待下轮补跑"
                elif proc.returncode == 0:
                    r["status"] = "done"
                    r["done_ts"] = _now()
                    r["note"] = tail or "ok"
                else:
                    r["status"] = "failed"
                    r["done_ts"] = _now()
                    r["note"] = tail or f"rc={proc.returncode}"
                print(f"[{_now()}] {r['id']} → {r['status']}: {r.get('note', '')[:120]}")
            except subprocess.TimeoutExpired:
                r["status"] = "failed"
                r["done_ts"] = _now()
                r["note"] = f"超时（>{TIMEOUT_S}s）"
                print(f"[{_now()}] {r['id']} 超时")
            save(rows)
        # 清理 3 天前终态任务
        cutoff = (datetime.now(BJ) - timedelta(days=3)).isoformat()
        keep = [r for r in rows
                if r.get("status") == "pending" or (r.get("done_ts") or "") >= cutoff]
        if len(keep) != len(rows):
            save(keep)
        return 0
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
