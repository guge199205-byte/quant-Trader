#!/usr/bin/env python3
"""港股分账账本（**薄封装**）。

算法全部收敛在 `scripts/account_protocol.py`（QIFI 式协议层）；
本模块只做"绑定"：台账文件路径 + 额度（HK$10 万）+ 读写。

为什么改成薄封装（见 docs/ACCOUNT_PROTOCOL_PLAN.md §0）：
  本模块此前是 `live_ledger` 的**整份拷贝**，已实测漂移——A 股侧的改进
  （`find_holder` / 延期单重放 / 回合台账）从未回流，其中"延期单重放"是
  2026-09-01 事故复盘后建的防线，却只护住了 A 股。
  现由协议层保证三市场共用同一套算法，`tests/test_account_protocol.py`
  的 golden 基线 + 一致性断言防它再次漂移。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import account_protocol as _p  # noqa: E402

LEDGER_FILE = ROOT / "data" / "hk_ledger.json"
AGENT_QUOTA = 100_000.0  # HK$


# ---------- IO（绑定层：本市场的文件路径与容错口径） ----------
def load_ledger() -> dict:
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("agents"), dict) else {"agents": {}}
    except (OSError, json.JSONDecodeError):
        return {"agents": {}}


def save_ledger(ledger: dict) -> None:
    """原子写账本：先写 tmp 再 rename，避免并发读到半写文件。

    同时把累积的回合记录落盘（协议层统一实现，三市场共用 live_roundtrips.jsonl，
    靠记录里的 market 字段区分）。落盘成功才从账本里清掉，失败留待下次重试。
    """
    ledger = _p.flush_roundtrips(ledger)
    LEDGER_FILE.parent.mkdir(exist_ok=True)
    tmp = LEDGER_FILE.with_name(LEDGER_FILE.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


# ---------- 协议层委托（本市场只钉额度） ----------
_positions = _p.positions
find_holder = _p.find_holder


def ensure_agent(ledger: dict, agent: str, quota: float = AGENT_QUOTA) -> dict:
    return _p.ensure_agent(ledger, agent, quota)


def agent_used(ledger: dict, agent: str) -> float:
    return _p.agent_used(ledger, agent)


def agent_remaining(ledger: dict, agent: str) -> float:
    return _p.agent_remaining(ledger, agent, AGENT_QUOTA)


def agent_virtual_cash(ledger: dict, agent: str) -> float:
    return _p.agent_virtual_cash(ledger, agent, AGENT_QUOTA)


def record_buy(ledger: dict, agent: str, code: str, volume: int,
               cost_price: float, ts: str) -> dict:
    return _p.record_buy(ledger, agent, code, volume, cost_price, ts, quota=AGENT_QUOTA)


def record_sell(ledger: dict, agent: str, code: str, volume: int,
                sell_price: float, ts: str, exit_reason: str | None = None) -> dict:
    return _p.record_sell(ledger, agent, code, volume, sell_price, ts,
                          exit_reason, quota=AGENT_QUOTA, market="HK")
