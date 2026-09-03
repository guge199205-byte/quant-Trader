#!/usr/bin/env python3
"""盘中快照采样器（TdxAiData 备胎·富字段版）：每 20 秒采一轮持仓快照落盘。

字段：px(Now)/vol(总量)/nowvol(即时手)/inside/outside(内外盘)/amount(万元)/
b5(Before5MinNow 五分钟前价)。一分钟内 3 轮 → 30/60min 动量、5min 动量、
内外盘失衡、5min 波动全部可自算，不再依赖 TdxAiData 分钟K。
cron：交易日 10-12,14-16 JST（=北京 9-11/13-15 盘中）每分钟跑，脚本内每 20s 一轮×3。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
BJ = None


def _bj() -> str:
    global BJ
    if BJ is None:
        from zoneinfo import ZoneInfo

        BJ = ZoneInfo("Asia/Shanghai")
    return datetime.now(BJ)


def in_window() -> bool:
    n = _bj()
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return (9 * 60 + 30 <= m < 11 * 60 + 30) or (13 * 60 <= m < 15 * 60)


def codes() -> list:
    try:
        led = json.loads((ROOT / "logs" / "live_ledger.json").read_text(encoding="utf-8"))
        out = []
        for rec in (led.get("agents") or {}).values():
            for code in (rec.get("positions") or {}):
                if code not in out:
                    out.append(code)
        return out
    except (OSError, ValueError):
        return []


def sample_once() -> None:
    cs = codes()
    if not cs:
        return
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    broker = TdxBridgeBroker()
    now = _bj()
    mdir = ROOT / "logs" / "min_snapshots"
    mdir.mkdir(parents=True, exist_ok=True)
    f = mdir / f"{now:%Y-%m-%d}.jsonl"
    for code in cs:
        try:
            r = broker.tdx_call("get_market_snapshot", {"stock_code": code}) or {}
            px = float(r.get("Now") or 0)
            if px <= 0:
                continue
            row = {"ts": now.isoformat(), "code": code, "px": px,
                   "vol": r.get("Volume"), "nowvol": r.get("NowVol"),
                   "inside": r.get("Inside"), "outside": r.get("Outside"),
                   "amount": r.get("Amount"), "b5": r.get("Before5MinNow"),
                   "open": r.get("Open"), "lastclose": r.get("LastClose")}
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            continue


def main() -> int:
    if not in_window():
        return 0
    for _ in range(3):
        sample_once()
        time.sleep(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())