#!/usr/bin/env python3
"""盘前 Windows / 通达信桥在线探测（北京 09:18，cron 10:18 JST）。

「行情通 ≠ 账户通」：只探 /health 不够——桥进程活着但通达信交易端没起来时，
账户查询会超时（盘前健检 2026-09-14 实录）。所以本脚本双检：
  1) GET  /api/v1/health          桥进程在不在（免鉴权）
  2) POST /api/v1/account/query   账户通道通不通（15s 超时，重试 3 次）

任一失败 → QQ 推送（+ 本地日志）；恢复 → 推一条「已恢复」。失败不重复轰炸：
同一状态只推一次（state 落 logs/premarket_probe_state.json），翻转才推。

用法：cron 每日交易日上午跑一次；也可手动 `python scripts/premarket_win_probe.py`
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

from agent_tools.brokers.tdx_bridge import TdxBridgeBroker  # noqa: E402
from push_notify import _now_cn, notify  # noqa: E402

STATE_FILE = ROOT / "logs" / "premarket_probe_state.json"
WINDOWS_HOST = "192.168.31.13:8550 通达信桥（Windows 交易机）"


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        # 原子写（2026-09-21 盘满实录：非原子 write_text 先截断后写，ENOSPC 一撞就把
        # 共享状态清成 0 字节——当天本文件即如此，状态静默丢失。tmp+replace 失败时
        # 保留旧文件，绝不毁掉好状态）。
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def probe(broker: TdxBridgeBroker | None = None) -> dict:
    """返回 {"ok": bool, "detail": str}。不抛异常。broker 可注入（测试用）。"""
    from agent_tools.brokers.base import BrokerError  # noqa: F401

    broker = broker or TdxBridgeBroker()
    # 1) 桥进程健康（免鉴权，2s 超时）
    import requests

    try:
        r = requests.get(f"{broker.bridge_url}/api/v1/health", timeout=4)
        if r.status_code >= 400:
            return {"ok": False, "detail": f"health HTTP {r.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"桥进程不可达: {type(exc).__name__}"}
    # 2) 账户通道（行情通≠账户通：重试 3 次，15s 超时）
    for attempt in (1, 2, 3):
        try:
            data = broker._account_query()
            cash = (data.get("asset") or {}).get("cash")
            return {"ok": True,
                    "detail": f"账户通道正常（现金 ¥{float(cash):,.0f}）" if cash is not None
                    else "账户通道正常"}
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:160]
            if attempt == 3:
                return {"ok": False, "detail": f"账户查询 3 次失败: {last}"}
    return {"ok": False, "detail": "账户查询失败"}  # unreachable


def main() -> int:
    now = _now_cn()
    state = _load_state()
    prev_down = bool(state.get("down"))
    res = probe()
    if res["ok"]:
        state = {"down": False, "ts": now.isoformat()}
        _save_state(state)
        if prev_down:
            notify("Windows/桥已恢复 ✅",
                   f"{WINDOWS_HOST}\n{res['detail']}\n（{now:%F %T} 探测）")
        return 0
    state = {"down": True, "ts": now.isoformat(), "down_ts": now.isoformat(),
             "last_alert_ts": time.time()}
    _save_state(state)
    out = []
    for line in (f"⚠️ 盘前 · {WINDOWS_HOST} 不在线（{now:%F %T}）",
                 f"{res['detail']}", "",
                 "影响：调仓轮会空跑、哨兵条件位/杠杆守护/净值采样盲区",
                 "→ 请在 Windows 机上重登通达信交易端，或检查桥服务"):
        out.append(line)
    msg = "\n".join(out)
    notify("盘前 · 桥不在线 ⚠️", msg)
    print(f"[{now:%F %T}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())