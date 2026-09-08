#!/usr/bin/env python3
"""实盘循环熔断（第二道防线）：当日亏损/日内回撤超限 → 该 agent 当日禁止新开仓买入。

为什么需要（2026-09-08）：execute_intraday_decision 与 live_llm_trade 的买入闸门
已有候选池成员/新开仓上限/单票比例/虚拟现金/杠杆/账户现金六道硬约束，但**没有一条
盯"今天已经亏了多少"**——MCP 路径（agent_tools/risk.py）有日亏熔断，实盘循环不走
那条路，属 fail-open。对标 deepseek-harness-quant 的「自动循环熔断」补上这一环。

口径（两个独立触发，任一命中即熔断到当日收盘；**卖出永远放行**——降风险不受限）：
  - 已实现日亏：当日卖出累计已实现亏损 ≥ DAY_LOSS_PCT × 日初权益
  - 日内回撤：最新分账权益较当日首个快照回撤 ≥ INTRADAY_DD_PCT（含浮亏）

fail-safe 取舍：两项输入都取不到时**不阻断**（打印告警）。第一道闸门的硬约束仍在，
而"数据格式一变就静默停掉全部交易"是更糟的故障模式；取到一项就按一项判。

用法：
  python scripts/live_breaker.py            # 打印今日熔断状态
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BJ = timezone(timedelta(hours=8))
EQUITY_LOG = ROOT / "logs" / "live_equity.jsonl"
TRIP_DIR = ROOT / "logs" / "breaker"

DAY_LOSS_PCT = 0.05      # 当日已实现亏损 ≥5% 日初权益 → 熔断
INTRADAY_DD_PCT = 0.06   # 日内权益回撤 ≥6%（含浮亏）→ 熔断
FALLBACK_EQUITY = 100_000.0  # 日初权益取不到时的分母（子账户名义额度）


def _bj_today() -> str:
    return datetime.now(BJ).strftime("%Y-%m-%d")


def _trade_path(day: str) -> Path:
    return ROOT / "logs" / f"live_trade_{day.replace('-', '')}.jsonl"


def day_equity(agent: str, day: str, path: Path | None = None) -> tuple:
    """当日该 agent 的 (首个, 最新) 分账权益；缺失返回 (None, None)。

    live_equity.jsonl 是分钟级采样（record_equity 仅交易时段落盘），
    首个快照即日初权益基准，最新即当前（含持仓浮盈亏）。
    """
    src = path or EQUITY_LOG
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return None, None
    first = last = None
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("agent") != agent or r.get("date") != day:
            continue
        try:
            v = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        if first is None:
            first = v
        last = v
    return first, last


def realized_loss_today(agent: str, day: str, path: Path | None = None) -> float:
    """当日该 agent 卖出累计已实现亏损（正数=亏损；无卖出/无成本价记录计 0）。

    只认已成交记录：`fill_confirm`（成交回报补记）用 volume/price；
    `fill.filled_volume` 用真实成交价量；`pending`/`error` 一律不计
    （否则把"挂了没成的单"当成已实现亏损，会误熔断）。
    """
    src = path or _trade_path(day)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    loss = 0.0
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("error") or r.get("agent") != agent or r.get("pending"):
            continue
        if str(r.get("side") or "").lower() != "sell":
            continue
        if r.get("mode") == "fill_confirm":
            vol, px = int(r.get("volume") or 0), r.get("price")
        else:
            fill = r.get("fill") or {}
            vol, px = int(fill.get("filled_volume") or 0), fill.get("filled_price")
        try:
            vol, px = int(vol), float(px or 0)
            cost = float(r.get("cost_price") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or px <= 0 or cost <= 0:
            continue
        pnl = (px - cost) * vol
        if pnl < 0:
            loss += -pnl
    return round(loss, 2)


def _trip_path(day: str) -> Path:
    return TRIP_DIR / f"{day}.json"


def load_trips(day: str | None = None) -> dict:
    """当日已熔断记录 {agent: {"ts","reason"}}；文件缺失/损坏 → {}。"""
    try:
        d = json.loads(_trip_path(day or _bj_today()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_trip(agent: str, reason: str, day: str | None = None) -> None:
    """落盘熔断（幂等：同日同一 agent 保留首次原因，不覆盖）。写失败不影响主流程。"""
    day = day or _bj_today()
    trips = load_trips(day)
    if agent in trips:
        return
    trips[agent] = {"ts": datetime.now(BJ).isoformat(timespec="seconds"), "reason": reason}
    try:
        TRIP_DIR.mkdir(parents=True, exist_ok=True)
        p = _trip_path(day)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(trips, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def evaluate(agent: str, day: str | None = None, equity_path: Path | None = None,
             trade_path: Path | None = None) -> tuple:
    """只读评估 → (是否熔断, 原因)。原因同时给出触发的数值，便于复盘对账。"""
    day = day or _bj_today()
    first, last = day_equity(agent, day, equity_path)
    base = first if first else FALLBACK_EQUITY
    loss = realized_loss_today(agent, day, trade_path)
    if base > 0 and loss / base >= DAY_LOSS_PCT:
        return True, (f"当日已实现亏损 ¥{loss:,.0f} ≥ {DAY_LOSS_PCT:.0%}"
                      f"×日初权益 ¥{base:,.0f}")
    if first and last and last < first * (1 - INTRADAY_DD_PCT):
        dd = (first - last) / first
        return True, (f"日内权益回撤 {dd:.1%} ≥ {INTRADAY_DD_PCT:.0%}"
                      f"（¥{first:,.0f} → ¥{last:,.0f}，含浮亏）")
    # 未超限，或输入缺失 → 不阻断（见模块 docstring 的 fail-safe 取舍）
    return False, ""

def check_and_trip(agent: str, day: str | None = None, persist: bool = True,
                   equity_path: Path | None = None, trade_path: Path | None = None) -> tuple:
    """买入闸门调用口：已熔断 → 直接返回；新命中 → 落盘（persist=False 用于 dry-run）。"""
    day = day or _bj_today()
    hit = load_trips(day).get(agent)
    if hit:
        return True, str(hit.get("reason") or "当日已熔断")
    tripped, reason = evaluate(agent, day, equity_path, trade_path)
    if tripped and persist:
        save_trip(agent, reason, day)
    return tripped, reason


def main() -> int:
    day = _bj_today()
    trips = load_trips(day)
    print(f"熔断状态 {day}（阈值：日亏 ≥{DAY_LOSS_PCT:.0%} / 日内回撤 ≥{INTRADAY_DD_PCT:.0%}）")
    agents = sorted({*(trips or {}), *(load_trips_agents() or [])})
    for a in agents:
        if a in trips:
            print(f"  🛑 {a}: {trips[a].get('reason')}（{trips[a].get('ts')}）")
            continue
        tripped, reason = evaluate(a, day)
        print(f"  {'🛑 应熔断' if tripped else '✅ 正常'} {a}" + (f": {reason}" if reason else ""))
    return 0


def load_trips_agents() -> list:
    """当日出现过分账净值记录的 agent 名单（供 main 展示）。"""
    try:
        text = EQUITY_LOG.read_text(encoding="utf-8")
    except OSError:
        return []
    day, out = _bj_today(), set()
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("agent") and r.get("date") == day:
            out.add(r["agent"])
    return sorted(out)


if __name__ == "__main__":
    sys.exit(main())
