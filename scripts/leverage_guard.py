#!/usr/bin/env python3
"""独立杠杆守护：分账子账户持仓市值 > 权益×1.5 时，不依赖 LLM 直接限价减仓。

2026-09-01 复盘：glm 分账实际杠杆曾达 ~3 倍（已用 ¥30.8 万/额度 ¥10 万），
系统强平只嵌在整点分析路径里、还要求模型先出决策——一旦分析没跑（桥断链/
LLM 超时）杠杆约束就没人管。本守护每分钟独立巡检：

  - 只卖不买；只减到 ≤1.5×权益（不是全清）
  - 优先从市值最大的一腿减起，按整手向上取整
  - T+1 可卖量复核；跌停不接；行情停更不下手（宁可等也不卖飞）
  - 成交后挂 pending 由 live_fills.reconcile 按真实成交价记账

cron 计划：* 10-12,14-16 * * 1-5 python scripts/leverage_guard.py（北京盘中分钟）
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from live_ledger import (agent_virtual_cash, find_holder,  # noqa: E402
                         load_ledger)
from live_hourly_analysis import (LEVERAGE_MAX, SELL_LIMIT_DOWN,  # noqa: E402
                                  in_trading_window, now_cn)
from live_fills import add_pending  # noqa: E402


def main() -> int:
    now = now_cn()
    if now.weekday() >= 5 or not in_trading_window(now):
        return 0

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker
    from live_hourly_analysis import market_data_stale

    broker = TdxBridgeBroker()
    if market_data_stale(broker):
        print(f"[{now:%F %T}] ⚠️ 行情停更，强平守护暂缓")
        return 0

    ledger = load_ledger()
    agents = ledger.get("agents") or {}
    if not agents:
        return 0

    # 可卖量（T+1）+ 实时价
    available, quotes = {}, {}
    try:
        for p in (broker._account_query().get("positions") or []):
            available[p["stock_code"]] = int(p.get("available_volume") or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[{now:%F %T}] ⚠️ 账户查询失败，守护跳过: {exc}")
        return 0
    for agent, rec in agents.items():
        for code in (rec.get("positions") or {}):
            if code in quotes:
                continue
            try:
                q = broker.get_quote(code, "")
                px = float((q or {}).get("close") or 0)
                quotes[code] = px
            except Exception:  # noqa: BLE001
                quotes[code] = 0.0

    for agent, rec in sorted(agents.items()):
        pos = rec.get("positions") or {}
        if not pos:
            continue
        vcash = agent_virtual_cash(ledger, agent)
        value = sum(max(quotes.get(c, 0), 0) * float(p["volume"]) for c, p in pos.items())
        equity = vcash + value
        if equity <= 0:
            continue
        limit_val = LEVERAGE_MAX * equity
        if value <= limit_val:
            continue
        exceed = value - limit_val
        # 从市值最大的一腿开始减，整手向上取整
        legs = sorted(pos.items(),
                      key=lambda kv: quotes.get(kv[0], 0) * kv[1]["volume"],
                      reverse=True)
        code, p = legs[0]
        price = quotes.get(code, 0)
        if price <= 0:
            print(f"[{now:%F %T}] ⚠️ {agent} 杠杆 {value / equity:.2f}× 超限 "
                  f"但 {code} 无行情，暂停该腿")
            continue
        want = int(-(-exceed // price // 100) * 100)  # ceil 到 100 股
        avail = available.get(code, 0)
        want = min(want, avail, int(p["volume"]))
        if want <= 0:
            print(f"[{now:%F %T}] ⏭️ {agent} 杠杆 {value / equity:.2f}× 超限，"
                  f"但 {code} 无可卖量，保留待 T+1 解锁")
            continue
        day_chg = 0.0
        try:
            klines = broker.get_klines(code, interval="daily")[-2:]
            if len(klines) >= 2 and float(klines[-2].get("close") or 0) > 0:
                day_chg = (float(klines[-1].get("close") or 0)
                           - float(klines[-2].get("close") or 0)) \
                    / float(klines[-2].get("close") or 0) * 100
        except Exception:  # noqa: BLE001
            pass
        if day_chg <= SELL_LIMIT_DOWN:
            print(f"[{now:%F %T}] ⏭️ {agent} {code} 跌停（{day_chg:+.2f}%），强平暂缓")
            continue
        limit = round(price * 0.98, 2)
        try:
            result = broker.sell(None, None, code, want, price=limit)
            print(f"[{now:%F %T}] 🔴 强平守护 {agent} 卖 {code} {want}股 "
                  f"限价 {limit}（杠杆 {value / equity:.2f}× > {LEVERAGE_MAX}×）: {result}")
            add_pending(result.get("order_id"), agent, code, "sell", want,
                        limit, now.isoformat())
            time.sleep(1)  # 桥限流
        except Exception as exc:  # noqa: BLE001
            print(f"[{now:%F %T}] ❌ 强平守护 {agent} 卖 {code} 失败: {exc}")
            from live_ledger import defer_on_exc

            defer_on_exc(agent, "sell", code, want, exc, now.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())