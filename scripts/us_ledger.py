#!/usr/bin/env python3
"""美股分账账本（per-agent，独立于 A股/港股账本）。

- data/us_ledger.json：{agents: {name: {quota, virtual_cash, positions: {code: {...}}}}}
- 初始虚拟现金 $10,000/agent（us_config.json initial_cash 可配）
- 买入扣虚拟现金、卖出加回；持仓成本加权平均
- 与 A股/港股同一套不可变记账语义
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LEDGER_FILE = ROOT / "data" / "us_ledger.json"
AGENT_QUOTA = 10_000.0  # USD


def load_ledger() -> dict:
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("agents"), dict) else {"agents": {}}
    except (OSError, json.JSONDecodeError):
        return {"agents": {}}


def save_ledger(ledger: dict) -> None:
    """原子写账本：先写 tmp 再 rename，避免并发读到半写文件。"""
    LEDGER_FILE.parent.mkdir(exist_ok=True)
    tmp = LEDGER_FILE.with_name(LEDGER_FILE.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


def _positions(ledger: dict, agent: str) -> dict:
    return ((ledger.get("agents") or {}).get(agent) or {}).get("positions") or {}


def ensure_agent(ledger: dict, agent: str, quota: float = AGENT_QUOTA) -> dict:
    """确保 agent 有账本条目（初始虚拟现金）。"""
    agents = {**ledger.get("agents", {})}
    if agent not in agents:
        agents[agent] = {"quota": quota, "virtual_cash": quota, "positions": {}}
    return {**ledger, "agents": agents}


def agent_used(ledger: dict, agent: str) -> float:
    """该 agent 名下持仓成本合计（used 额度）。"""
    return round(
        sum(float(p["volume"]) * float(p["cost_price"])
            for p in _positions(ledger, agent).values()),
        2,
    )


def agent_remaining(ledger: dict, agent: str) -> float:
    """剩余可买额度 = quota - used。"""
    return round(AGENT_QUOTA - agent_used(ledger, agent), 2)


def agent_virtual_cash(ledger: dict, agent: str) -> float:
    """该 agent 虚拟现金（初始 $10,000；买入扣、卖出加）。"""
    rec = (ledger.get("agents") or {}).get(agent) or {}
    return float(rec.get("virtual_cash") or AGENT_QUOTA)


def record_buy(ledger: dict, agent: str, code: str, volume: int,
               cost_price: float, ts: str) -> dict:
    """买入记账（不可变）：加仓按加权平均更新成本；扣虚拟现金。"""
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
        pos[code] = {"volume": volume, "cost_price": round(cost_price, 4),
                     "buy_ts": ts, "last_ts": ts}
    rec["positions"] = pos
    rec["virtual_cash"] = round(float(rec.get("virtual_cash") or AGENT_QUOTA)
                                - volume * float(cost_price), 2)
    agents[agent] = rec
    return {**ledger, "agents": agents}


def record_sell(ledger: dict, agent: str, code: str, volume: int,
                sell_price: float, ts: str) -> dict:
    """卖出记账（不可变）：扣减数量，减到 0 移除；虚拟现金加回卖出金额。"""
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
    rec["virtual_cash"] = round(float(rec.get("virtual_cash") or AGENT_QUOTA)
                                + volume * float(sell_price), 2)
    agents[agent] = rec
    return {**ledger, "agents": agents}
