#!/usr/bin/env python3
"""盘中桥哨兵：交易日每分钟探测通达信桥，状态翻转即推 QQ（2026-09-15 用户要求）。

背景：2026-09-14 上午桥断 09:25-11:02，调仓轮空跑、哨兵/杠杆守护盲区，用户
在电脑外完全不知情。本哨兵补上即时通道：
  - 断线（正常→断）：立即推「桥断线」+ 失败原因 + 影响面
  - 持续断：每 30 分钟重提醒一次（首推 + 周期提醒，防「不知道还没恢复」）
  - 恢复（断→正常）：推「已恢复」+ 断线时长

探测口径与盘前探测一致（premarket_win_probe.probe）：health + 账户查询双检，
「行情通 ≠ 账户通」——桥进程活着但交易端没起（假活）也算断。

状态文件与盘前探测共用 logs/premarket_probe_state.json（字段扩展 last_alert_ts），
两个入口互相看得见对方探到的状态，不会重复推送。

用法：cron 交易时段每分钟；手动 `python scripts/bridge_watch.py`（无视时段）。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from push_notify import _now_cn, notify  # noqa: E402
from premarket_win_probe import probe  # noqa: E402

STATE_FILE = ROOT / "logs" / "premarket_probe_state.json"
REALERT_MIN = 30                # 持续断线重提醒间隔（分钟）


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        # 原子写（2026-09-21 盘满实录：非原子 write_text 先截断后写，ENOSPC 一撞就把
        # 共享状态清成 0 字节——当天 premarket_probe_state.json 即如此，桥哨兵状态
        # 静默丢失。tmp+replace 失败时保留旧文件，绝不毁掉好状态）。
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def _in_market_hours(now: datetime) -> bool:
    from trading_cal import is_trading_day

    if not is_trading_day(now.date()):
        return False
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1505   # 含集合竞价与收盘定格段


def run_once(now: datetime | None = None, force: bool = False) -> str:
    """探测一次并按状态机推送。返回动作标记（ok/down/realert/recovered/skip）。"""
    now = now or _now_cn()
    if not force and not _in_market_hours(now):
        return "skip"
    res = probe()
    state = _load_state()
    prev_down = bool(state.get("down"))
    if res["ok"]:
        if prev_down:
            down_since = str(state.get("down_ts") or "")[:19]
            notify("✅ 桥已恢复",
                   f"通达信桥恢复在线（断开自 {down_since or '未知'}）\n"
                   f"盘中交易/哨兵恢复；期间被拒的延期单会自动重放")
            _save_state({"down": False, "ts": now.isoformat()})
            return "recovered"
        _save_state({"down": False, "ts": now.isoformat()})
        return "ok"
    detail = str(res.get("detail") or "未知原因")
    last_alert = float(state.get("last_alert_ts") or 0)
    if not prev_down:
        notify("⚠️ 通达信桥断线",
               f"时间 {now:%m-%d %H:%M} ｜ {detail[:120]}\n"
               f"影响：调仓/哨兵条件位/杠杆守护全部盲区\n"
               f"请在 Windows 机重登通达信交易端；恢复后会再通知")
        _save_state({"down": True, "ts": now.isoformat(),
                     "down_ts": now.isoformat(), "last_alert_ts": time.time()})
        return "down"
    minutes = int((time.time() - last_alert) / 60) if last_alert else 999
    if minutes >= REALERT_MIN:
        down_since = str(state.get("down_ts") or "")[:19]
        notify(f"⚠️ 桥仍离线（已断约 {minutes} 分钟）",
               f"自 {down_since or '未知'} ｜ 最新错误：{detail[:100]}\n"
               f"盲区持续中：调仓/哨兵/杠杆守护不工作")
        state["last_alert_ts"] = time.time()
        state["ts"] = now.isoformat()
        _save_state(state)
        return "realert"
    state["ts"] = now.isoformat()
    _save_state(state)
    return "down"


def main() -> int:
    force = "--force" in sys.argv
    act = run_once(force=force)
    print(f"[{_now_cn():%F %T}] bridge_watch: {act}")
    return 0


if __name__ == "__main__":
    sys.exit(main())