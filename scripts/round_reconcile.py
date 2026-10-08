#!/usr/bin/env python3
"""整点轮死进程对账（2026-09-21 磁盘满事故后加固）。

背景（2026-09-21 实录）：14:45 尾盘决策轮撞上 ENOSPC——record_equity 写不进去，
finally 里的 failed 心跳（原子 tmp+replace）同样失败，状态文件原样停在
`running=true` + 死 pid。alert_checks.round_stuck 只按心跳新鲜度判定：
  - alerts.log 每 5 分钟一条，推送层指纹 2 小时重推一次，一直报到次日新轮覆盖；
  - 且文案「后续 cron 轮会被锁跳过」对死进程是**假陈述**——flock 随进程终止由内核
    自动释放，死轮并不阻塞下一轮。

本脚本由 alert.sh 在 round_stuck 判定**之前**调用（幂等）：
  - 进程活着（/proc/<pid>/cmdline 含 live_hourly_analysis）→ 不动，真卡死交
    round_stuck 报「心跳停滞」（此时「锁被占用」是事实）；
  - 进程死了且 running=true → 打印一次性告警并把状态显式收尾成 failed
    （保留 pid/start_ts 供追溯）——下一轮对账看到 running=false 自然静默；
  - 状态文件存在但坏（0 字节/非法 JSON，磁盘满时 truncate-then-write 的签名）→
    告警、隔离成 .corrupt.<ts>、重置为 failed。

只读判断 + 一次显式状态写（与 order_events 的 auto-resolve 同思路：状态机推进
必须显式发生，而不是靠告警文案掩盖）。退出码恒 0（alert.sh 只看 stdout 有无）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUND_FILE = ROOT / "logs" / "live_analysis_round.json"
BJ = timezone(timedelta(hours=8))
CMD_MARKER = "live_hourly_analysis"    # 轮进程 cmdline 的特征串（防 pid 复用误判）


def now_bj() -> datetime:
    return datetime.now(BJ)


def pid_state(pid, marker: str = CMD_MARKER) -> str:
    """轮进程存活判定 → alive / dead / unknown（unknown = 无从判定，保守不动）。

    只看 /proc（Linux 唯一可靠的 liveness 源）：目录不存在 = 死；cmdline 不含特征串
    = pid 被复用（那是别人的进程，我们的轮已死）；读不到（hidepid 等）= unknown。
    注意 fork→exec 窗口内 cmdline 为空（实测 Popen 后首读约半数如此）——空cmdline
    一律 unknown：宁可交给 round_stuck 保守报「卡死」，绝不在此误判「已死」。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return "unknown"
    if pid <= 0:
        return "unknown"
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return "dead"
    except OSError:
        return "unknown"
    if not cmd:
        return "unknown"                       # 僵尸/内核线程：无从比对
    return "alive" if marker.encode() in cmd else "dead"


def dead_alert(doc: dict) -> str:
    start = str(doc.get("start_ts") or "?")
    return (f"整点轮进程已死（pid {doc.get('pid')}，阶段 {doc.get('phase')}，"
            f"起于 {start[5:16]}）——该轮已中断，锁随进程释放不影响下一轮；"
            f"状态已自动标记 failed（本条只发一次）")


def final_state(doc: dict, now: datetime) -> dict:
    """收尾状态：保留 pid/start_ts 追溯，语义与 round_trace 异常路径一致。"""
    return {"pid": doc.get("pid"), "start_ts": doc.get("start_ts"),
            "updated_ts": now.isoformat(), "phase": "failed", "running": False}


def _write_state(path: Path, doc: dict) -> None:
    """原子写（同 round_heartbeat）：盘满时失败保留旧文件，绝不截成 0 字节。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else ROUND_FILE
    if not path.exists():
        return 0                               # 全新安装/尚未跑过：不建文件
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    now = now_bj()
    if not isinstance(doc, dict):
        q = path.with_name(f"{path.name}.corrupt.{now:%Y%m%dT%H%M%S}")
        try:
            path.replace(q)
            note = f"已隔离为 {q.name}"
        except OSError as exc:                 # 隔离失败也别把告警吞了
            note = f"隔离失败（{exc}）"
        print(f"整点轮状态文件损坏（{path.name}，{note}）——上次运行中断时写坏"
              f"（典型：磁盘满）；已重置为 failed")
        try:
            _write_state(path, {"pid": None, "start_ts": None,
                                "updated_ts": now.isoformat(),
                                "phase": "failed", "running": False})
        except OSError as exc:
            print(f"⚠️ 重置失败（下轮重试）: {exc}", file=sys.stderr)
        return 0
    if not doc.get("running"):
        return 0
    if pid_state(doc.get("pid")) != "dead":
        return 0                               # 活着或无从判定：交给 round_stuck
    print(dead_alert(doc))
    try:
        _write_state(path, final_state(doc, now))
    except OSError as exc:                     # 打印已出：下轮重报，指纹去重兜底
        print(f"⚠️ 收尾失败（下轮重试）: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
