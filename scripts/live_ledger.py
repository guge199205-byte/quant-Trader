#!/usr/bin/env python3
"""实盘分账记账本：每 agent ¥10 万虚拟子账户（券商真实账户只有一个，分账只是记账）。

规则（2026-08-31 用户确认）：
  - 每个 agent 初始额度 AGENT_QUOTA = ¥100,000，累计买入成本不得超过该额度
  - 买入：记录到该 agent 名下，used = Σ volume×cost_price（加仓按加权成本）
  - 卖出：从持有该股票的 agent 名下扣减，释放额度
  - 2026-08-31 已买的 5 只（约 ¥92 万）属于总账户，不入分账
  - 持有同一股票的 agent 用 find_holder 查找（轮候分配下每只只归属一个 agent）

用法：
  python scripts/live_ledger.py            # 打印当前分账状态
  python -m pytest scripts/test_live_ledger.py
"""
import json
from pathlib import Path

LEDGER_FILE = Path(__file__).resolve().parent.parent / "logs" / "live_ledger.json"
AGENT_QUOTA = 100_000.0


def load_ledger() -> dict:
    """读账本；文件不存在/损坏时返回空账本（不抛异常）。"""
    if not LEDGER_FILE.is_file():
        return {"version": 1, "agents": {}}
    try:
        return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"version": 1, "agents": {}}


def save_ledger(ledger: dict) -> None:
    """原子写账本：先写 tmp 再 rename，避免半写文件。"""
    LEDGER_FILE.parent.mkdir(exist_ok=True)
    tmp = LEDGER_FILE.with_name(LEDGER_FILE.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


def _positions(ledger: dict, agent: str) -> dict:
    return ((ledger.get("agents") or {}).get(agent) or {}).get("positions") or {}


def agent_used(ledger: dict, agent: str) -> float:
    """该 agent 名下持仓成本合计（used 额度）。"""
    return round(
        sum(float(p["volume"]) * float(p["cost_price"]) for p in _positions(ledger, agent).values()),
        2,
    )


def agent_remaining(ledger: dict, agent: str) -> float:
    """剩余可买额度 = quota - used。"""
    return round(AGENT_QUOTA - agent_used(ledger, agent), 2)


def record_buy(ledger: dict, agent: str, code: str, volume: int,
               cost_price: float, ts: str) -> dict:
    """买入记账（不可变，返回新账本）：加仓时按加权平均更新成本。
    同时扣减该 agent 虚拟现金（初始 ¥10 万，虚拟子账户口径）。"""
    agents = {**ledger.get("agents", {})}
    rec = dict(agents.get(agent) or {})
    pos = dict(rec.get("positions") or {})
    prev = pos.get(code)
    if prev:
        new_vol = prev["volume"] + volume
        new_cost = (prev["volume"] * prev["cost_price"] + volume * cost_price) / new_vol
        pos[code] = {"volume": new_vol, "cost_price": round(new_cost, 4),
                     "buy_ts": prev["buy_ts"], "last_ts": ts}
    else:
        pos[code] = {"volume": volume, "cost_price": round(float(cost_price), 4),
                     "buy_ts": ts, "last_ts": ts}
    cash = float(rec.get("virtual_cash") or AGENT_QUOTA) - volume * float(cost_price)
    rec["positions"] = pos
    rec["virtual_cash"] = round(cash, 2)
    agents[agent] = rec
    return {**ledger, "agents": agents}


def record_sell(ledger: dict, agent: str, code: str, volume: int,
                sell_price: float, ts: str) -> dict:
    """卖出记账（不可变）：扣减数量，减到 0 移除；虚拟现金加回卖出金额；
    不存在的持仓原样返回。"""
    pos = dict(_positions(ledger, agent))
    if code not in pos:
        return ledger
    remaining = pos[code]["volume"] - volume
    if remaining <= 0:
        del pos[code]
    else:
        pos[code] = {**pos[code], "volume": remaining, "last_ts": ts}
    agents = {**ledger.get("agents", {})}
    rec = dict(agents.get(agent) or {})
    rec["positions"] = pos
    cash = float(rec.get("virtual_cash") or AGENT_QUOTA) + volume * float(sell_price)
    rec["virtual_cash"] = round(cash, 2)
    agents[agent] = rec
    return {**ledger, "agents": agents}


def agent_virtual_cash(ledger: dict, agent: str) -> float:
    """该 agent 虚拟现金（初始 ¥10 万；买入扣、卖出加）。"""
    return float(((ledger.get("agents") or {}).get(agent) or {}).get("virtual_cash")
                 or AGENT_QUOTA)


def find_holder(ledger: dict, code: str) -> str | None:
    """谁持有该股票（轮候分配下每只只归属一个 agent；找不到返回 None）。"""
    for agent, rec in (ledger.get("agents") or {}).items():
        if (rec.get("positions") or {}).get(code):
            return agent
    return None


# ---------- 延期单（拒单补执行）：桥行情断开被拒的决策，恢复后自动重放 ----------

def load_deferred(ledger: dict) -> list:
    """待重放订单：在途、被拒（行情断开/桥不可达）的买卖意图。"""
    return list(ledger.get("deferred") or [])


def save_deferred(ledger: dict, agent: str, side: str, code: str, volume: int,
                  reason: str, ts: str) -> dict:
    """登记一笔延期单（不可变，返回新账本）。同 agent+code+side 只保留最新一笔，
    防止断链期间每轮重复堆积。"""
    item = {"agent": agent, "side": side, "code": code, "volume": int(volume),
            "reason": reason, "ts": ts}
    deferred = [d for d in load_deferred(ledger)
                if not (d.get("agent") == agent and d.get("code") == code
                        and d.get("side") == side)]
    deferred.append(item)
    return {**ledger, "deferred": deferred}


def clear_deferred(ledger: dict, agent: str, side: str, code: str) -> dict:
    """清除一条延期单（成功后调用，防重复下单）。"""
    deferred = [d for d in load_deferred(ledger)
                if not (d.get("agent") == agent and d.get("code") == code
                        and d.get("side") == side)]
    return {**ledger, "deferred": deferred}


def defer_on_exc(agent: str, side: str, code: str, volume: int,
                 exc: Exception, ts: str) -> bool:
    """桥断链/拒单时登记延期单（命中断链关键字才登记），返回是否登记。

    2026-09-01 事故教训：行情断开时 4 次减仓决策全部被透传拒单且无人重试，
    白白浪费一个交易时段——被拒意图落盘，恢复后由 replay_deferred.py 重放。
    """
    msg = str(exc)
    if not any(k in msg for k in ("断开", "Connection", "refused", "拒绝", "超时")):
        return False
    try:
        save_ledger(save_deferred(load_ledger(), agent, side, code, volume,
                                  msg[:120], ts))
        return True
    except OSError:
        return False


def main() -> None:
    ledger = load_ledger()
    agents = ledger.get("agents") or {}
    if not agents:
        print("📒 分账账本为空（尚无 agent 分账持仓）")
        return
    for agent in sorted(agents):
        used = agent_used(ledger, agent)
        print(f"📒 {agent}: 已用 ¥{used:,.0f} / ¥{AGENT_QUOTA:,.0f}"
              f" 剩余 ¥{agent_remaining(ledger, agent):,.0f}")
        for code, p in sorted(_positions(ledger, agent).items()):
            print(f"   {code} ×{p['volume']} @ {p['cost_price']} = "
                  f"¥{p['volume'] * p['cost_price']:,.0f}")


if __name__ == "__main__":
    main()
