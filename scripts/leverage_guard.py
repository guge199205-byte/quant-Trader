#!/usr/bin/env python3
"""独立杠杆守护：分账子账户持仓市值 > 权益×1.5 时，不依赖 LLM 直接限价减仓。

2026-09-01 复盘：glm 分账实际杠杆曾达 ~3 倍（已用 ¥30.8 万/额度 ¥10 万），
系统强平只嵌在整点分析路径里、还要求模型先出决策——一旦分析没跑（桥断链/
LLM 超时）杠杆约束就没人管。本守护每分钟独立巡检：

  - 只卖不买；只减到 ≤1.5×权益（不是全清）
  - 优先从市值最大的一腿减起，按整手向上取整
  - T+1 可卖量复核；跌停不接；行情停更不下手（宁可等也不卖飞）
  - 自动执行总开关（configs/intraday_exec.json {"enabled": true}）关掉时
    只报不卖——与哨兵同口径：总闸关了就不该有自动路径真下单
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
from live_hourly_analysis import (LEVERAGE_MAX, in_trading_window,  # noqa: E402
                                  intraday_exec_enabled, now_cn)
from live_fills import add_pending, load_pending, round_sell_qty  # noqa: E402
from live_quotes import klines as lq_klines, quote as lq_quote  # noqa: E402
from ashare_rules import at_limit_down  # noqa: E402


def _degraded_check(now) -> int:
    """桥断/行情停更时的降级杠杆巡检（2026-09-15）：估算 + QQ 告警，不下任何单。

    数据 = 账户快照（≤4h，含未分账持仓）→ 否则本地台账 + AiData 现价。
    口径为「估算」，消息与日志均注明；桥恢复后按真实口径复核。
    """
    from live_account_cache import (ledger_positions, load_snapshot,
                                    notify_throttled, snapshot_positions)

    agents = (load_ledger().get("agents") or {})
    snap = load_snapshot()
    if snap:
        pos_map = snapshot_positions(snap)
        cash_all = float((snap.get("asset") or {}).get("cash") or 0)
        src = f"账户快照（{snap.get('age_min', '?')} 分钟前）"
    else:
        pos_map = ledger_positions()
        cash_all = sum(float((a or {}).get("virtual_cash") or 0) for a in agents.values())
        src = "本地台账（不含未分账持仓）"
    if not pos_map:
        print(f"[{now:%F %T}] 降级监控：无快照也无台账持仓，跳过")
        return 0

    prices = {}
    for code in pos_map:
        prices[code] = float(lq_quote(code, prefer="aidata").get("close") or 0)

    def _lev(codes, cash):
        value = sum(prices.get(c, 0.0) * pos_map[c]["volume"] for c in codes)
        equity = cash + value
        return (value / equity if equity > 0 else 0.0), value, equity

    msgs = [f"口径：{src} + AiData 现价（估算）"]
    lev_all, v_all, e_all = _lev(list(pos_map), cash_all)
    if lev_all > LEVERAGE_MAX:
        msgs.append(f"全账户杠杆 {lev_all:.2f}×（市值 ¥{v_all:,.0f} / 权益 ¥{e_all:,.0f}）"
                    f"超 {LEVERAGE_MAX}×")
    for agent, rec in sorted(agents.items()):
        codes = [c for c in (rec.get("positions") or {}) if c in pos_map]
        if not codes:
            continue
        lev, v, _e = _lev(codes, float(rec.get("virtual_cash") or 0))
        if lev > LEVERAGE_MAX:
            msgs.append(f"[{agent}] 分账杠杆 {lev:.2f}×（市值 ¥{v:,.0f}）超限")
    print(f"[{now:%F %T}] 降级监控：" + "；".join(msgs))
    if len(msgs) > 1:
        notify_throttled("leverage", "🧯 桥断降级 · 杠杆超限 ⚠️",
                         "\n".join(msgs) + "\n桥在线前只能告警，恢复后应尽快处置并复核")
    return 0


def main() -> int:
    now = now_cn()
    if not in_trading_window(now):  # 日历感知（含法定节假日休市）
        return 0
    # 自动执行总开关：关了只报不卖（哨兵/整点轮同口径；本脚本无 --execute，纯无人值守）
    exec_on = intraday_exec_enabled()

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker
    from live_hourly_analysis import market_data_stale

    broker = TdxBridgeBroker()
    if market_data_stale(broker):
        print(f"[{now:%F %T}] ⚠️ 行情停更（或桥不可达），转降级监控")
        return _degraded_check(now)

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
        print(f"[{now:%F %T}] ️ 账户查询失败，转降级监控: {exc}")
        return _degraded_check(now)
    for agent, rec in agents.items():
        for code in (rec.get("positions") or {}):
            if code in quotes:
                continue
            try:
                q = lq_quote(code, prefer="aidata", broker=broker)
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
        raw_want = -(-exceed // price)  # 补齐缺口所需股数（向上取整到股）
        avail = available.get(code, 0)
        want = round_sell_qty(code, raw_want, min(avail, int(p["volume"])))
        if want <= 0:
            print(f"[{now:%F %T}] ⏭️ {agent} 杠杆 {value / equity:.2f}× 超限，"
                  f"但 {code} 无可卖量，保留待 T+1 解锁")
            continue
        day_chg = 0.0
        try:
            klines = lq_klines(code, interval="daily", count=2,
                               prefer="aidata", broker=broker)
            if len(klines) >= 2 and float(klines[-2].get("close") or 0) > 0:
                day_chg = (float(klines[-1].get("close") or 0)
                           - float(klines[-2].get("close") or 0)) \
                    / float(klines[-2].get("close") or 0) * 100
        except Exception:  # noqa: BLE001
            pass
        if at_limit_down(code, day_chg):
            print(f"[{now:%F %T}] ⏭️ {agent} {code} 跌停（{day_chg:+.2f}%），强平暂缓")
            continue
        limit = round(price * 0.98, 2)
        if not exec_on:
            # 只报不卖：本行说明「一切检查都过了、本该强平」，但总闸关着
            print(f"[{now:%F %T}] 🔒 {agent} 杠杆 {value / equity:.2f}× > {LEVERAGE_MAX}×，"
                  f"应减 {code} {want}股 限价 {limit}——自动执行开关已关"
                  f"（intraday_exec.json），强平守护只报不卖")
            continue
        # 在途去重：账本要等 reconcile 才更新，无去重会每分钟叠加卖单造成超卖
        # （2026-09-04 目标 200 股实际叠加到 400 股）
        inflight = [p for p in load_pending()
                    if p.get("agent") == agent and p.get("code") == code
                    and p.get("side") == "sell"]
        if inflight:
            print(f"[{now:%F %T}] ⏭️ {agent} {code} 已有在途卖单 {len(inflight)} 笔，"
                  f"守护本轮跳过（防叠加超卖）")
            continue
        try:
            result = broker.sell(None, None, code, want, price=limit)
            print(f"[{now:%F %T}] 🔴 强平守护 {agent} 卖 {code} {want}股 "
                  f"限价 {limit}（杠杆 {value / equity:.2f}× > {LEVERAGE_MAX}×）: {result}")
            # 成交推送由 reconcile 补记时发（2026-09-18：提交时刻不推成功文案）
            add_pending(result.get("order_id"), agent, code, "sell", want,
                        limit, now.isoformat(),
                        reason=f"杠杆 {value / equity:.2f}× 超标强平")
            time.sleep(1)  # 桥限流
        except Exception as exc:  # noqa: BLE001
            print(f"[{now:%F %T}] ❌ 强平守护 {agent} 卖 {code} 失败: {exc}")
            from live_ledger import defer_on_exc

            defer_on_exc(agent, "sell", code, want, exc, now.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())