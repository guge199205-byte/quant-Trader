#!/usr/bin/env python3
"""补 2026-09-09 断线空窗的净值曲线点（桥 09:25–10:26 账户通道不可用）。

2026-09-09 事故：通达信桥进程崩溃循环，09:25–10:26 净值采样全部失败，ARENA
净值图左半段（09:25 起）整段缺失。恢复后桥限制拿不到分钟K线，无法还原真实
逐分钟价格，因此本脚本用**两个真实锚点 + 线性插值**重建，并标记 rebuilt:true：

  起点 09:25 = 今日日K开盘价 × 账本持仓 + 虚拟现金（现金当日无成交，恒定）
  终点      = 恢复后第一条真实采样（分账 10:25 / 总账户 10:27）

只补总账户线（agent=null）与各分账线；已存在的 (key, agent) 不覆盖。
用法：python scripts/backfill_equity_20260909.py [--apply]
"""
import fcntl
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

BJ = ZoneInfo("Asia/Shanghai")
LOG = ROOT / "logs" / "live_equity.jsonl"
LOCK = ROOT / "logs" / ".live_equity.lock"
DAY = "2026-09-09"
START = datetime(2026, 9, 9, 9, 25, tzinfo=BJ)


def _minute_key(t: datetime) -> str:
    return f"{t:%Y-%m-%d %H:%M}"


def _read_rows() -> list[dict]:
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    apply_ = "--apply" in sys.argv

    from live_ledger import agent_virtual_cash, load_ledger
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    ledger = load_ledger()
    agents = ledger.get("agents") or {}
    broker = TdxBridgeBroker()

    # 开盘价：全部持仓标的的今日日K open
    codes = sorted({c for rec in agents.values() for c in (rec.get("positions") or {})})
    open_px = {}
    for code in codes:
        bars = broker.get_klines(code, DAY, DAY, interval="daily")
        if not bars or not bars[-1].get("open"):
            print(f"❌ {code} 取不到今日开盘价，中止")
            return 1
        open_px[code] = float(bars[-1]["open"])

    rows = _read_rows()
    existing = {(r.get("key"), r.get("agent")) for r in rows if r.get("date") == DAY}

    # 真实锚点：今日每条序列的第一条
    first_real: dict[object, dict] = {}
    for r in rows:
        if r.get("date") != DAY:
            continue
        a = r.get("agent")
        if a not in first_real:
            first_real[a] = r
    if None not in first_real:
        print("❌ 今日没有总账户真实点，中止")
        return 1
    missing_agents = [a for a in agents if a not in first_real]
    if missing_agents:
        print(f"❌ 缺分账真实锚点: {missing_agents}，中止")
        return 1

    # 起点值：09:25 开盘价口径
    start_val = {}
    for a, rec in agents.items():
        mv = sum(open_px[c] * float(p["volume"]) for c, p in (rec.get("positions") or {}).items())
        start_val[a] = agent_virtual_cash(ledger, a) + mv
    total_cash = float(first_real[None].get("cash") or 0)
    if total_cash <= 0:
        print("❌ 总账户真实点缺 cash 字段，中止")
        return 1
    start_val[None] = total_cash + sum(
        open_px[c] * float(p["volume"])
        for rec in agents.values()
        for c, p in (rec.get("positions") or {}).items()
    )

    new_rows = []
    for a, anchor in first_real.items():
        end_val = float(anchor["value"])
        end_t = datetime.fromisoformat(anchor["ts"])
        span = (end_t - START).total_seconds()
        t = START
        while t < end_t:
            key = _minute_key(t)
            if (key, a) not in existing:
                frac = (t - START).total_seconds() / span
                val = round(start_val[a] + (end_val - start_val[a]) * frac, 2)
                row = {"key": key, "agent": a, "date": DAY,
                       "ts": t.isoformat(), "value": val, "rebuilt": True}
                if a is None:
                    row["asset"] = val
                    row["cash"] = total_cash
                new_rows.append(row)
            t += timedelta(minutes=1)

    print(f"开盘价: {open_px}")
    print(f"锚点: { {('总账户' if k is None else k): (round(start_val[k], 2), float(v['value'])) for k, v in first_real.items()} }")
    print(f"待补 {len(new_rows)} 行"
          f"（分账 {sum(1 for r in new_rows if r['agent'])} / 总账户 {sum(1 for r in new_rows if r['agent'] is None)}）")
    if new_rows:
        print("首行:", json.dumps(new_rows[0], ensure_ascii=False))
        print("末行:", json.dumps(new_rows[-1], ensure_ascii=False))
    if not apply_:
        print("（dry-run，加 --apply 落盘）")
        return 0

    with open(LOCK, "a+", encoding="utf-8") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                for r in new_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
    print(f"✅ 已追加 {len(new_rows)} 行到 {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
