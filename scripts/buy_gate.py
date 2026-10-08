#!/usr/bin/env python3
"""买入闸门（纯函数）：整点轮 live_hourly_analysis 与开盘轮 live_llm_trade 共用。

为什么收敛：两处原本各写一份判定，并已实际漂移——09:35 主入口缺新开仓上限、
没接风险预算档位（2026-09-08 发现），而它才是每天第一笔买入的入口。
闸门逻辑一旦分叉，风险预算就只兑现一半，且改动只能改到一半的路径上。

职责边界：本模块只判"这笔买入能不能下"（halt/标的边界/pct/池成员/新开仓上限），
不碰行情、不碰账本；在途单去重、涨停不追、资金/杠杆/现金校验仍在调用方
（它们需要实时行情与账户快照，不属于纯判定）。
"""
from dataclasses import dataclass
import math

import gate_rules
from symbol_policy import SymbolPolicy, check_symbol


@dataclass(frozen=True)
class BuyGate:
    """买入闸门的静态参数（当日风险预算档位 + 候选池 + 标的边界）。

    policy 默认 `SymbolPolicy()`：**默认禁买 ST**——调用方忘读配置也守住底线；
    黑名单需显式 `load_policy()`（见 symbol_policy）。
    """

    pool_codes: frozenset
    per_stock_pct: float
    max_new_buys: int
    halted: bool = False
    halt_reason: str = ""
    policy: SymbolPolicy = SymbolPolicy()


@dataclass(frozen=True)
class BuyDecision:
    """判定结果：ok=False 时 reason 说明原因；ok=True 时 pct/new_buys 为已归一化的值。

    rule：稳定的**规则 id**（gate_rules 词表，2026-09-19 影子代价账）——reason 是
    给人读的中文句子、可以随时改；影子账按规则分组算"这条闸花了多少钱"，拿句子
    当键会让历史样本在改文案的那天整批断档。放行时为空（放行不是某条规则的功劳）。
    """

    ok: bool
    reason: str = ""
    pct: float = 0.0
    new_buys: int = 0
    rule: str = ""


def check_buy(code: str, pct: float, held: bool, new_buys: int,
              opened_today, gate: BuyGate, name: str | None = None) -> BuyDecision:
    """买入放行判定。

    held=True（持仓内加仓）只受熔断/标的边界/pct 约束；held=False（新开仓）还须是
    候选池成员，且**单轮与当日累计**都 ≤ max_new_buys（风险预算口径=全天）。
    opened_today：当日已成交买入的代码集合（daily_buy_codes）。
    name：标的名称，仅用于 ST/退市识别；取不到按非风险股放行（fail-open，见 symbol_policy）。
    返回的 rule 见 BuyDecision；调用点把它原样带进影子账（不要自己解析 reason）。
    """
    if gate.halted:
        return BuyDecision(False, f"当日已熔断（{gate.halt_reason}）", rule=gate_rules.HALT_DAILY)
    risk = check_symbol(code, name, gate.policy)
    if risk:
        return BuyDecision(False, f"标的边界：{risk}", rule=gate_rules.SYMBOL_BOUNDARY)
    try:  # 模型可能吐字符串/None/脏值：闸门自身必须容错，绝不因脏输入炸掉下单流程
        pct = float(pct or 0)
    except (TypeError, ValueError):
        return BuyDecision(False, "pct 非法（非数字）", rule=gate_rules.PCT_INVALID)
    pct = min(max(pct, 0.0), float(gate.per_stock_pct))
    if pct <= 0:
        return BuyDecision(False, "pct=0", rule=gate_rules.PCT_ZERO)
    if not held:
        if not gate.pool_codes or code not in gate.pool_codes:
            return BuyDecision(False, "非当前持仓且不在候选池", rule=gate_rules.POOL_NOT_MEMBER)
        if new_buys >= gate.max_new_buys:
            return BuyDecision(False, f"本轮新开仓已达上限 {gate.max_new_buys} 只",
                               rule=gate_rules.CAP_ROUND_NEW_BUYS)
        if code not in opened_today and len(opened_today) >= gate.max_new_buys:
            tail = f"（已开 {'、'.join(sorted(opened_today))}）" if opened_today else ""
            return BuyDecision(False, f"当日新开仓已达上限 {gate.max_new_buys} 只{tail}",
                               rule=gate_rules.CAP_DAILY_NEW_BUYS)
        new_buys += 1
    return BuyDecision(True, "", pct, new_buys)


def position_cap_reason(code_value: float, add_cost: float, equity: float,
                        cap_pct: float) -> str:
    """单票集中度闸（纯函数，2026-09-18 机构级）：同票已有敞口 + 本单成本 >
    cap_pct×权益 → 返回拒绝原因（含数值），否则返回 ""。

    为什么需要：`per_stock_pct` 是"单笔占**剩余额度**"的比例，同一标的跨轮加仓
    此前不封顶（只有杠杆闸兜底，且它不区分集中度）。本闸把"单票累计敞口"封在
    权益的一个比例内（档位由 risk_budget 的 per_stock_pos_pct 提供）。

    口径由调用方保证同基（成本/成本，或 市值/市值——与本路径的权益口径一致）；
    equity<=0 或 cap_pct<=0 不判（无法计算时放行，与杠杆闸 `if equity > 0` 同口径，
    调用方另有杠杆/现金/虚拟现金三闸兜底）。脏输入/Nan/Inf/溢出 容错返回 ""（放行）。
    """
    try:
        code_value = float(code_value or 0)
        add_cost = float(add_cost or 0)
        equity = float(equity)
        cap_pct = float(cap_pct)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not all(map(math.isfinite, (code_value, add_cost, equity, cap_pct))):
        return ""      # NaN 会穿过所有比较（恒 False）产出假判定，一律不判
    if equity <= 0 or cap_pct <= 0:
        return ""
    cap = cap_pct * equity
    projected = code_value + add_cost
    if projected <= cap:
        return ""
    return (f"同票敞口 ¥{projected:,.0f}（已有 ¥{code_value:,.0f} + 本单 ¥{add_cost:,.0f}）"
            f" > 上限 {cap_pct:.0%}×权益 ¥{cap:,.0f}")
