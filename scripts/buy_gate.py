#!/usr/bin/env python3
"""买入闸门（纯函数）：整点轮 live_hourly_analysis 与开盘轮 live_llm_trade 共用。

为什么收敛：两处原本各写一份判定，并已实际漂移——09:35 主入口缺新开仓上限、
没接风险预算档位（2026-09-08 发现），而它才是每天第一笔买入的入口。
闸门逻辑一旦分叉，风险预算就只兑现一半，且改动只能改到一半的路径上。

职责边界：本模块只判"这笔买入能不能下"（halt/pct/池成员/新开仓上限），
不碰行情、不碰账本；在途单去重、涨停不追、资金/杠杆/现金校验仍在调用方
（它们需要实时行情与账户快照，不属于纯判定）。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BuyGate:
    """买入闸门的静态参数（当日风险预算档位 + 候选池）。"""

    pool_codes: frozenset
    per_stock_pct: float
    max_new_buys: int
    halted: bool = False
    halt_reason: str = ""


@dataclass(frozen=True)
class BuyDecision:
    """判定结果：ok=False 时 reason 说明原因；ok=True 时 pct/new_buys 为已归一化的值。"""

    ok: bool
    reason: str = ""
    pct: float = 0.0
    new_buys: int = 0


def check_buy(code: str, pct: float, held: bool, new_buys: int,
              opened_today, gate: BuyGate) -> BuyDecision:
    """买入放行判定。

    held=True（持仓内加仓）只受熔断与 pct 约束；held=False（新开仓）还须是
    候选池成员，且**单轮与当日累计**都 ≤ max_new_buys（风险预算口径=全天）。
    opened_today：当日已成交买入的代码集合（daily_buy_codes）。
    """
    if gate.halted:
        return BuyDecision(False, f"当日已熔断（{gate.halt_reason}）")
    try:  # 模型可能吐字符串/None/脏值：闸门自身必须容错，绝不因脏输入炸掉下单流程
        pct = float(pct or 0)
    except (TypeError, ValueError):
        return BuyDecision(False, "pct 非法（非数字）")
    pct = min(max(pct, 0.0), float(gate.per_stock_pct))
    if pct <= 0:
        return BuyDecision(False, "pct=0")
    if not held:
        if not gate.pool_codes or code not in gate.pool_codes:
            return BuyDecision(False, "非当前持仓且不在候选池")
        if new_buys >= gate.max_new_buys:
            return BuyDecision(False, f"本轮新开仓已达上限 {gate.max_new_buys} 只")
        if code not in opened_today and len(opened_today) >= gate.max_new_buys:
            tail = f"（已开 {'、'.join(sorted(opened_today))}）" if opened_today else ""
            return BuyDecision(False, f"当日新开仓已达上限 {gate.max_new_buys} 只{tail}")
        new_buys += 1
    return BuyDecision(True, "", pct, new_buys)
