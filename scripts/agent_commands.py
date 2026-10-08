#!/usr/bin/env python3
"""QQ 用户指令闸门（2026-09-14）：用户在 QQ 给机器人发的命令，宿主侧消费执行。

指令由 dsh 工作区 agent（baymax.cordis.yml persona 路由规则）写入
  dsh/root-quantAI/commands.json（容器 /root/quantAI/commands.json）
本模块只做**强制执行**，不依赖任何 LLM 自觉：

  - halt_buy   冻结某 agent 买入（购闸门层硬拦，卖出/强平不受影响）
  - resume_buy 解除该 agent 的冻结（取指令触发后，同 agent 的 active halt_buy 失效）
  - sell       强制清仓某代码（T+1 可卖量/跌停/在途全闸门检查，执行一次即消费）

纪律：
  - 指令 24h 过期作废（隔日策略自恢复）
  - 本模块被交易执行路径调用，任何失败只打印，绝不阻断交易主流程
  - 卖出执行成功后推送 QQ（用户指令清仓回执）+ 标记 consumed，避免每轮重复卖

用法：
  python scripts/agent_commands.py list                    # 查看当前有效指令
  python scripts/agent_commands.py sell --agent X --code C # 手动强清（调试）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

COMMANDS_FILE = ROOT / "dsh" / "root-quantAI" / "commands.json"
MAX_AGE_HOURS = 24
CN_TZ = ZoneInfo("Asia/Shanghai")


def load_commands() -> list[dict]:
    """读指令文件；缺失/损坏 → []（闸门极端情况宁可放行也不崩）。"""
    try:
        data = json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))
        return data.get("commands", []) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_commands(cmds: list[dict]) -> None:
    try:
        COMMANDS_FILE.write_text(
            json.dumps({"version": 1, "commands": cmds}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except OSError:
        pass


def _is_active(cmd: dict, now_ts: float | None = None) -> bool:
    if not cmd.get("active"):
        return False
    try:
        created = float(str(cmd.get("ts_raw") or cmd.get("created_ts_raw") or 0))
    except (TypeError, ValueError):
        created = 0
    if created and time.time() - created > MAX_AGE_HOURS * 3600:
        return False
    return True


def _match_agent(cmd: dict, agent: str) -> bool:
    target = str(cmd.get("agent") or "")
    return target == agent


def active_for(agent: str, cmds: list | None = None) -> list[dict]:
    """某 agent 的当前有效指令（resume_buy 会抵消同时存在的 halt_buy）。"""
    cmds = load_commands() if cmds is None else cmds
    live = [c for c in cmds if _is_active(c) and _match_agent(c, agent)]
    if any(c.get("op") == "resume_buy" for c in live):
        live = [c for c in live if c.get("op") != "halt_buy"]
    return live


def has_halt_buy(agent: str) -> bool:
    """闸门判断：该 agent 是否被用户冻结买入。"""
    return any(c.get("op") == "halt_buy" for c in active_for(agent))


def consume(cmd: dict) -> None:
    """执行完毕 → 标记 inactive + executed_ts。"""
    cmds = load_commands()
    for c in cmds:
        if c.get("id") == cmd.get("id"):
            c["active"] = False
            # 2026-09-20 修复：time.strftime 取机器本地时间（JST）却标 +08:00
            c["executed_ts"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
            break
    _save_commands(cmds)


def apply_forced_sells(broker, agent: str, now, out=None) -> str:
    """执行该 agent 所有 active sell 指令（每个代码一次）。

    回流：""=无指令  "done:<code>x<vol>"=已执行  "skip:<code>:<原因>"=保留待下次
    """
    from ashare_rules import at_limit_down
    from live_fills import add_pending, inflight, round_sell_qty
    from live_ledger import load_ledger

    cmds = load_commands()
    sells = [c for c in cmds if _is_active(c) and _match_agent(c, agent)
             and c.get("op") == "sell" and c.get("code")]
    if not sells:
        return ""
    # 2026-09-21 审查 HIGH-1：本函数直接下真单，时段闸门必须在这里也有——调用方
    # live_llm_trade 曾把它排在窗口闸门之前，收盘后的清仓单照样发出（桥回「提交
    # 交易成功」的假象，随后柜台判废；与 09-18 600176 同形态）。指令保留待下一
    # 交易时段，不静默作废。
    from ashare_rules import in_continuous_auction

    if not in_continuous_auction(now):
        print(f"  ⏭️ 用户指令清仓：非连续竞价时段（现 {now:%H:%M}），"
              f"不下单，指令保留待下一交易时段")
        return "skip:" + ";".join(f"{c['code']}:非连续竞价时段" for c in sells)
    ledger = load_ledger()
    positions = (ledger.get("agents") or {}).get(agent, {}).get("positions") or {}
    done, skipped = [], []
    for cmd in sells:
        code = str(cmd["code"])
        held = int((positions.get(code) or {}).get("volume") or 0)
        if held <= 0:
            consume(cmd)
            skipped.append(f"{code}:无持仓")
            continue
        buy_ts = str((positions.get(code) or {}).get("buy_ts") or "")
        if buy_ts[:10] == now.strftime("%Y-%m-%d"):
            skipped.append(f"{code}:今日买入T+1不可卖")
            continue                                # 条件位/整点轮同口径：保留待明日
        if inflight(code, "sell"):
            skipped.append(f"{code}:在途卖单未确认")
            continue
        try:
            klines = broker.get_klines(code, interval="daily")[-2:]
            prev = float(klines[-2].get("close") or 0) if len(klines) >= 2 else 0.0
            price = float(klines[-1].get("close") or 0) if klines else 0.0
            day_chg = (price - prev) / prev * 100 if prev else 0.0
        except Exception:  # noqa: BLE001
            skipped.append(f"{code}:行情不可用")
            continue
        if at_limit_down(code, day_chg):
            skipped.append(f"{code}:跌停")
            continue
        if price <= 0:
            skipped.append(f"{code}:无行情价")
            continue
        vol = round_sell_qty(code, held, held)
        if vol <= 0:
            skipped.append(f"{code}:无合法申报量")
            continue
        try:
            # 卖出报单价（2026-09-21 修正）：旧口径报跌停价在连续竞价属越界申报 →
            # 柜台废单（002074 实录）；改 max(跌停价, 现价×0.99)，见 ashare_rules。
            from ashare_rules import aggressive_sell_price
            limit = aggressive_sell_price(code, prev, price)
            if limit is None:
                limit = round(price * 0.99, 2)
            result = broker.sell(None, None, code, vol, price=limit)
            # 成交推送由 reconcile 补记时发（2026-09-18：提交时刻不推成功文案；
            # 规则理由随在途单带上，成交补记的 QQ 通知沿用）
            add_pending(str(result.get("order_id") or ""), agent, code, "sell", vol,
                        limit, now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                        reason=f"用户指令清仓：{str(cmd.get('note') or '')[:60]}")
            consume(cmd)
            done.append(f"{code}x{vol}")
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{code}:下单失败{str(exc)[:80]}")
    if done:
        return f"done:{','.join(done)}"
    if skipped:
        return f"skip:{';'.join(skipped)}"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="QQ 用户指令闸门")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="查看当前指令")
    s = sub.add_parser("sell", help="手动强制清仓（调试）")
    s.add_argument("--agent", required=True)
    s.add_argument("--code", required=True)
    args = ap.parse_args()
    if args.cmd == "list":
        for c in load_commands():
            mark = "🟢" if _is_active(c) else "⚪"
            print(f"{mark} [{c.get('agent')}] {c.get('op')} {c.get('code') or ''} "
                  f"note={c.get('note') or ''} id={c.get('id')}")
    else:
        from datetime import datetime
        print(apply_forced_sells(None, args.agent, datetime.now(CN_TZ)))
    return 0


if __name__ == "__main__":
    sys.exit(main())