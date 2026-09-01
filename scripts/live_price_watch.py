#!/usr/bin/env python3
"""分钟级价格哨兵：执行 LLM 盘中分析挂的 watch 条件位（跌破止损 / 到位止盈）。

背景：整点分析每小时才一轮，「跌破 ¥120 就减仓」这类条件位在两次整点之间
没有人盯。本脚本由 cron 每分钟拉起，闭环是：

  live_hourly_analysis 解析 LLM 决策中的 watch 项
    → data/live_watch.json（每 agent 每小时整组刷新，最新分析说了算）
    → 本脚本查桥日K最新价（交易时段日K最后一根=当日实时价）
    → 现价 ≤ stop_loss 减仓 pct 比例 / ≥ take_profit 止盈 pct 比例
    → 与盘中执行同一套卖出闸门（T+1 可卖量、跌停不接、100 股整数倍）
    → record_sell 分账记账 + logs/live_watch_YYYYMMDD.jsonl
    → 触发并执行后该条规则即消费（一次性）；T+1 不可卖 / 跌停卖不出
      / 下单失败则保留规则，下一分钟或次日继续守

用法:
  python scripts/live_price_watch.py             # 交易时段才执行（cron 每分钟）
  python scripts/live_price_watch.py --force     # 忽略时段检查（调试）
  python scripts/live_price_watch.py --dry-run   # 触发只打印，不下单
  python scripts/live_price_watch.py --status    # 查看当前挂着的条件位

cron（本机 JST，北京=JST-1）: * 10-12,14-16 * * 1-5
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

WATCH_FILE = ROOT / "data" / "live_watch.json"
LOG_DIR = ROOT / "logs"
SELL_LIMIT_DOWN = -9.9   # 跌停不接（与 live_hourly_analysis 同口径）
POLL_SLEEP_SEC = 1       # 桥限流：每条规则之间隔 1 秒
SKIP_NOTIFY_HOUR = True  # 同一规则同一小时的重复跳过只提醒一次（防刷屏）


def _load_dotenv() -> None:
    """加载项目 .env（仅补缺省环境变量，不打印任何值）——桥地址/token 在这里。"""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"')


_load_dotenv()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def in_window(now: datetime) -> bool:
    """A股交易时段（北京）：9:30-11:30 / 13:00-15:00 工作日。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 930 <= hm <= 1130 or 1300 <= hm <= 1500


# ---------- watch 规则文件（本模块是 data/live_watch.json 的唯一属主） ----------

def load_watch() -> dict:
    """{"agent名": [{code, stop_loss, take_profit, pct, reason, created_ts}]}"""
    try:
        data = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_watch(rules: dict) -> None:
    """原子写：先写 tmp 再 rename，避免哨兵与整点分析并发读到半写文件。"""
    WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH_FILE.with_name(WATCH_FILE.name + ".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(WATCH_FILE)


def save_watch_rules(agent: str, decisions: list) -> int:
    """整点分析落 watch 条件位：该 agent 的旧规则整组替换（最新分析说了算），
    本轮无 watch 决策则清空该 agent 全部旧规则。返回落盘规则数。"""
    rules = {a: v for a, v in load_watch().items() if a != agent}
    mine = []
    for d in decisions or []:
        if d.get("action") != "watch":
            continue
        code = str(d.get("code") or "").strip()
        if not code or (d.get("stop_loss") is None and d.get("take_profit") is None):
            continue  # 没有价位的条件位没有意义
        mine.append({
            "code": code,
            "stop_loss": d.get("stop_loss"),
            "take_profit": d.get("take_profit"),
            "pct": min(max(float(d.get("pct") or 1.0), 0.0), 1.0),
            "reason": str(d.get("reason") or ""),
            "created_ts": now_cn().isoformat(),
        })
    if mine:
        rules[agent] = mine
    save_watch(rules)
    return len(mine)


# ---------- 行情与执行 ----------

def _last_price(broker, code: str):
    """桥日K 最后两根 → (现价, 昨收)。交易时段日K最后一根的 close 即当日实时价。"""
    try:
        bars = broker.get_klines(code, interval="daily")[-2:]
    except Exception:  # noqa: BLE001
        return None, None
    if len(bars) < 2:
        return None, None
    try:
        price = float(bars[-1].get("close") or 0)
        prev = float(bars[-2].get("close") or 0)
    except (TypeError, ValueError):
        return None, None
    if price <= 0 or prev <= 0:
        return None, None
    return price, prev


def _log_line(rec: dict) -> None:
    """哨兵动作日志 → logs/live_watch_YYYYMMDD.jsonl（按北京日期滚动）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"live_watch_{now_cn():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _execute_sell(broker, agent: str, rule: dict, price: float, prev: float,
                  trig: str, avail, dry_run: bool = False) -> bool:
    """条件位触发 → 卖出（与盘中执行同一套闸门）。
    返回 True=规则已消费（已执行/作废），False=保留规则下次再守。"""
    from live_ledger import load_ledger, record_sell, save_ledger

    code = rule["code"]
    if avail is None:
        print(f"  🗑️ [{agent}] {code}: 已不在持仓中，条件位作废")
        return True
    if avail <= 0:
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 可卖量 0（T+1 当日买入），条件位保留待明日")
        return False
    chg = (price - prev) / prev * 100
    if chg <= SELL_LIMIT_DOWN:
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 跌停（{chg:+.2f}%）卖不出，条件位保留")
        return False
    vol = int(avail * min(max(rule.get("pct", 1.0), 0.0), 1.0) / 100) * 100
    if vol <= 0:
        print(f"  🗑️ [{agent}] {code}: 可卖量 {avail} 股按 {rule.get('pct', 1.0):.0%}"
              f"不足 1 手，条件位作废")
        return True
    limit = round(price * 0.99, 2)  # 限价卖：现价 -1%
    label = "跌破止损" if trig == "stop_loss" else "达到止盈"
    if dry_run:
        print(f"  🟡 DRY-RUN 卖出 {code} {vol}/{avail} 股 限价 ¥{limit:.2f}（{label}）")
        return True
    try:
        result = broker.sell(None, None, code, vol, price=limit)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
        _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                   "code": code, "volume": vol, "price": limit,
                   "trigger": rule.get(trig), "error": str(exc)})
        return False  # 下次重试
    print(f"  ✅ [{agent}] 卖出 {code} {vol} 股 限价 ¥{limit:.2f} 已受理: {result}")
    ledger = record_sell(load_ledger(), agent, code, vol, limit, now_cn().isoformat())
    save_ledger(ledger)
    _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
               "code": code, "volume": vol, "price": limit, "trigger": rule.get(trig),
               "pct": rule.get("pct"), "reason": rule.get("reason"), "result": result})
    return True


def _notify_skip(rule: dict, msg: str) -> None:
    """同一规则同一小时只提醒一次跳过原因（T+1/跌停每分钟都会撞上，防刷屏）。"""
    hour_tag = f"{now_cn():%Y%m%d%H}"
    if rule.get("_skip_notified") == hour_tag:
        return
    rule["_skip_notified"] = hour_tag
    print(f"  {msg}")


def run_watch(broker, dry_run: bool = False) -> int:
    """轮询全部条件位，返回本轮触发笔数。"""
    rules = load_watch()
    if not rules:
        return 0
    positions = broker._account_query().get("positions") or []
    avail_map = {p.get("stock_code"): int(p.get("available_volume") or 0)
                 for p in positions}
    fired = 0
    for agent in list(rules):
        kept = []
        for r in rules[agent]:
            price, prev = _last_price(broker, r["code"])
            if price is None:
                _notify_skip(r, f"⚠️ [{agent}] {r['code']} 行情获取失败，条件位保留")
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            trig = None
            if r.get("stop_loss") and price <= r["stop_loss"]:
                trig = "stop_loss"
            elif r.get("take_profit") and price >= r["take_profit"]:
                trig = "take_profit"
            if not trig:
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            label = "跌破止损" if trig == "stop_loss" else "达到止盈"
            print(f"  🎯 [{agent}] {r['code']} 现价 ¥{price:.2f} {label}位 "
                  f"¥{r[trig]:.2f}（减仓 {r.get('pct', 1.0):.0%}）: {r.get('reason', '')}")
            if _execute_sell(broker, agent, r, price, prev, trig,
                             avail_map.get(r["code"]), dry_run=dry_run):
                fired += 1  # 触发并已消费
            else:
                kept.append(r)
            time.sleep(POLL_SLEEP_SEC)
        if kept:
            rules[agent] = kept
        else:
            rules.pop(agent, None)
    save_watch(rules)
    return fired


def print_status() -> None:
    rules = load_watch()
    if not rules:
        print("📭 无 watch 条件位（等整点分析挂入）")
        return
    for agent, rs in rules.items():
        for r in rs:
            sl = f"止损 ¥{r['stop_loss']:.2f}" if r.get("stop_loss") else "止损 —"
            tp = f"止盈 ¥{r['take_profit']:.2f}" if r.get("take_profit") else "止盈 —"
            print(f"[{agent}] {r['code']}: {sl} / {tp}"
                  f"（触发卖出 {r.get('pct', 1.0):.0%}）{r.get('reason', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="分钟级价格哨兵：watch 条件位触发卖出")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--dry-run", action="store_true", help="触发只打印，不下单")
    parser.add_argument("--status", action="store_true", help="查看当前挂着的条件位")
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    now = now_cn()
    if not args.force and not in_window(now):
        return 0  # 非交易时段静默退出（cron 每分钟跑，不刷屏）
    if not load_watch():
        return 0  # 无条件位，零开销退出（连桥都不碰）

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    broker = TdxBridgeBroker()
    fired = run_watch(broker, dry_run=args.dry_run)
    if fired:
        print(f"[{now:%F %T}] ⚠️ 条件位触发 {fired} 笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
