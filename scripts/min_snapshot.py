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

from trading_cal import is_trading_day  # noqa: E402

BJ = None


def _bj() -> str:
    global BJ
    if BJ is None:
        from zoneinfo import ZoneInfo

        BJ = ZoneInfo("Asia/Shanghai")
    return datetime.now(BJ)


def in_window() -> bool:
    n = _bj()
    if not is_trading_day(n.date()):
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
    fails: list = []
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
        except Exception as exc:  # noqa: BLE001
            # 绝不静默丢弃。2026-09-22 上午实录：盘满后这里的写盘全部 OSError(28)，
            # 被裸 `continue` 吞掉且**本文件没有任何 print** → min_snapshots 09-22
            # 首行 13:45:42，上午四个半小时的快照整段缺失而零日志零告警
            # （它正是 minute_feats 的唯一数据源）。成功路径仍不打印（cron 每分钟
            # 跑，不刷屏），只在真失败时每次调用一行。
            fails.append(f"{code}:{type(exc).__name__}")
            continue
    if fails:
        more = f" 等 {len(fails)} 只" if len(fails) > 3 else ""
        print(f"[{now:%F %T}] ⚠️ 快照采样失败 {len(fails)}/{len(cs)} 只："
              f"{'，'.join(fails[:3])}{more}")


def main() -> int:
    if not in_window():
        return 0
    for _ in range(3):
        sample_once()
        time.sleep(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())