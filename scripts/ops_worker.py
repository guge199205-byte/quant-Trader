#!/usr/bin/env python3
"""Ops 指令执行器（每分钟 cron）：消费 logs/ops_cmds.jsonl。
类型：docker restart <target>（容器白名单） / bridge（写 Windows 共享 flag → 看门狗 30s 内重启桥）。
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "logs" / "ops_cmds.jsonl"
SHARE = Path("/mnt/tdx-shared/bridge-windows")

CONTAINERS = {
    "api": "baymax-api", "dsh": "baymax-dsh", "mcp-us": "baymax-mcp-us",
    "mcp-cn": "baymax-mcp-cn", "mcp-hk": "baymax-mcp-hk", "ui": "baymax-ui-arena",
}


def run() -> int:
    if not JOBS.is_file():
        return 0
    try:
        # errors="replace" + 逐行容错（批 10/13）：本文件含中文的指令说明且由前端追加写，
        # 一行坏（写一半/撕裂多字节）不许让**所有** ops 指令（含重启桥）集体静默不执行。
        text = JOBS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    rows, bad = [], 0
    for l in text.splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            r = None
        if isinstance(r, dict):
            rows.append(r)
        else:
            bad += 1
    if bad:
        print(f"⚠️ ops_cmds 有 {bad} 行无法解析，已跳过（其余 {len(rows)} 条照常执行）")
    if not rows:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    done = []
    for r in rows:
        if r.get("status") not in ("pending", None):
            done.append(r)
            continue
        typ, target = r.get("type"), r.get("target")
        if typ == "bridge":
            try:
                SHARE.mkdir(parents=True, exist_ok=True)
                (SHARE / "restart_bridge.flag").touch()
                r.update({"status": "done", "done_ts": now, "note": "flag 已投递（看门狗 30s 内重启桥）"})
            except OSError as e:
                r.update({"status": "failed", "done_ts": now, "note": str(e)})
        elif typ == "restart" and target in CONTAINERS:
            try:
                p = subprocess.run(["docker", "restart", CONTAINERS[target]],
                                   capture_output=True, text=True, timeout=90)
                r.update({"status": "done" if p.returncode == 0 else "failed",
                          "done_ts": now, "note": (p.stdout or p.stderr)[:200]})
            except Exception as e:  # noqa: BLE001
                r.update({"status": "failed", "done_ts": now, "note": str(e)})
        else:
            r.update({"status": "failed", "done_ts": now, "note": "未知指令"})
        done.append(r)
        print(f"[{now}] ops {r.get('id')} {r.get('type')}/{r.get('target')} → {r.get('status')}")
    # 只保留最近 50 条
    JOBS.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in done[-50:]) + "\n",
                    encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(run())