#!/usr/bin/env python3
"""延期单重放：桥断链被拒的买卖意图，行情恢复后自动补执行。

2026-09-01 事故复盘：行情断开时 agent 的减仓决策连续 4 次被透传拒单且无
任何重试，恢复后只能等下一个整点窗口撞运气。现在被拒意图落盘
ledger['deferred']，cron 每个交易分钟跑本脚本，桥健康（行情新鲜）即重放。

安全网（宁可不动不可乱动）：
  - 只重放 sell（减仓）：buy 的额度/现金闸门是决策时刻算的，重放时空跑更危险
  - T+1 可卖量复核，可卖不足按可卖量缩量，0 可卖则保留延期
  - 行情新鲜度硬闸：连桥行情都在停更，延期单绝不重放
  - 限价卖（现价 -1%），跌停不接（SELL_LIMIT_DOWN）
  - 延期超过 24h 自动作废（隔日委托已失效，清掉防堆积）
"""
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from live_ledger import (clear_deferred, load_deferred, load_ledger,  # noqa: E402
                         save_ledger)
from live_hourly_analysis import (SELL_LIMIT_DOWN, in_trading_window,  # noqa: E402
                                  now_cn)
from live_fills import add_pending  # noqa: E402

MAX_DEFER_HOURS = 24


def main() -> int:
    now = now_cn()
    if now.weekday() >= 5 or not in_trading_window(now):
        return 0  # 非盘中静默（cron 每分钟跑）

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker
    from live_hourly_analysis import market_data_stale

    broker = TdxBridgeBroker()
    if market_data_stale(broker):
        print(f"[{now:%F %T}] ⚠️ 行情停更，延期单暂缓重放")
        return 0

    ledger = load_ledger()
    deferred = load_deferred(ledger)
    if not deferred:
        return 0
    stale_cut = (now - timedelta(hours=MAX_DEFER_HOURS)).isoformat()

    # T+1 可卖量复核（整单一次拉齐）
    avail = {}
    try:
        for p in (broker._account_query().get("positions") or []):
            avail[p["stock_code"]] = int(p.get("available_volume") or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[{now:%F %T}] ⚠️ 账户查询失败，延期单暂缓: {exc}")
        return 0

    final = []
    for d in deferred:
        if d.get("ts", "") < stale_cut:
            print(f"[{now:%F %T}] 🗑️ 作废过期延期单 {d.get('side')} "
                  f"{d.get('code')}（>{MAX_DEFER_HOURS}h）")
            continue  # 不保留，直接清除
        if d.get("side") != "sell":
            final.append(d)  # 买入延期只留档不重放（资金闸是决策时刻的）
            continue
        agent, code = d["agent"], d["code"]
        vol = int(d["volume"])
        av = avail.get(code, 0)
        if av <= 0:
            print(f"[{now:%F %T}] ⏭️ {agent} 卖 {code}: T+1 不可卖（可卖 0），保留延期")
            final.append(d)
            continue
        if vol > av:
            print(f"[{now:%F %T}] ⏭️ {agent} 卖 {code}: 需 {vol} > 可卖 {av}，缩量重放")
            vol = av
        try:
            klines = broker.get_klines(code, interval="daily")[-3:]
            price = float(klines[-1].get("close") or 0)
            day_chg = 0.0
            if len(klines) >= 2:
                prev = float(klines[-2].get("close") or 0)
                if prev > 0:
                    day_chg = (price - prev) / prev * 100
        except Exception:  # noqa: BLE001
            price, day_chg = 0.0, 0.0
        if price <= 0:
            print(f"[{now:%F %T}] ⏭️ {agent} 卖 {code}: 行情不可用，保留延期")
            final.append(d)
            continue
        if day_chg <= SELL_LIMIT_DOWN:
            print(f"[{now:%F %T}] ⏭️ {agent} 卖 {code}: 跌停（{day_chg:+.2f}%），保留延期")
            final.append(d)
            continue
        limit = round(price * 0.99, 2)
        try:
            result = broker.sell(None, None, code, vol, price=limit)
            print(f"[{now:%F %T}] ✅ 重放 {agent} 卖 {code} {vol}股 "
                  f"限价 {limit}: {result}")
            add_pending(result.get("order_id"), agent, code, "sell", vol,
                        limit, now.isoformat())
            ledger = clear_deferred(ledger, agent, "sell", code)
            save_ledger(ledger)
            time.sleep(1)  # 桥限流
        except Exception as exc:  # noqa: BLE001
            print(f"[{now:%F %T}] ❌ 重放 {agent} 卖 {code} 失败: {exc}")
            from live_ledger import defer_on_exc

            if not defer_on_exc(agent, "sell", code, vol, exc, now.isoformat()):
                final.append(d)  # 非断链类失败：原样保留等下一轮
    if final != deferred:
        save_ledger({**ledger, "deferred": final})
    return 0


if __name__ == "__main__":
    sys.exit(main())