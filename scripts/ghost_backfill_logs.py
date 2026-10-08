#!/usr/bin/env python3
"""影子账历史回填 —— 把"当时发生了、只是没记账"的闸门事件补进池（只读日志）。

影子账 2026-09-19 才接线。但闸门事件此前就已经躺在日志里：
  * 提示词侧的资金剔除 → `💸 ... 候选池剔除 N 只买不起的：<code><name>(最小q股 ¥v)`
    出现在 logs/live_llm_trade.log（09:35 主入口）与 logs/live_hourly_analysis.log；
  * "意图无落地" → 决策池里的 buy 意图 与 logs/live_trade_YYYYMMDD.jsonl 的
    委托证据（order_id）做差集。
回填的意义不是给报告凑样本，而是**生产回测**：拿真实发生过的剔除/否决，
走同一套远期收益引擎定价，先回答一句"这台机器上，闸门至今花了多少钱"。

口径纪律：
  1. **只读**。不碰交易状态、不碰决策池；写入只经 ghost_ledger.record（幂等）。
  2. 默认**预览**，`--write` 才落盘——回填是一次性动作，数字要先被人看过。
  3. 判定"落地"只认委托号：`result.order_id` / `fill.order_id`。没有委托号的
     记录（error/pending 无号）**不算落地**——那正是要抓的东西。
  4. 成交流水缺失的那一天**不可比**，单独计数，不当作"未落地"（否则会把
     "日志没留"误读成"订单丢了"）。
  5. 演练轮（dry-run）无法从日志可靠区分（hourly 的 DRY-RUN 标记只在有成交时
     打印）——回填行的 source 因此统一带 `backfill:` 前缀，报告按 source 单列，
     不与接线后的实时记录混算。

用法：
  python scripts/ghost_backfill_logs.py            # 预览（不写）
  python scripts/ghost_backfill_logs.py --write    # 落盘
  python scripts/ghost_backfill_logs.py --only unaffordable|noexec
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules  # noqa: E402
import ghost_ledger  # noqa: E402

LOGS = ROOT / "logs"
LLM_LOG = LOGS / "live_llm_trade.log"
HOURLY_LOG = LOGS / "live_hourly_analysis.log"
DECISION_POOL = LOGS / "decision_pool.jsonl"

#: `300750.SZ宁德时代(最小100股 ¥34,021)`（名称可能为空，金额可能带千分位）
ITEM_RE = re.compile(r"([0-9]{6}\.[A-Z]{2})([^()]*?)\(\s*最小\s*(\d+)\s*股\s*¥\s*([0-9,]+)")
BUDGET_RE = re.compile(r"单票预算\s*¥\s*([0-9,]+)")
#: 09:35 主入口的轮头（🔴 实盘 / 🟡 演练），💸 行本身不带日期。
#: 标记与日期之间的模式文案会变（"🔴 实盘执行" / "🟡 DRY-RUN 决策演练（不下单）"），
#: 所以按"标记之后跳过所有非数字字符"取日期，不锚死在具体文案上。
LLM_HEAD_RE = re.compile(r"^\s*(🔴|🟡)[^\d]*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")
#: 整点轮每行自带时间戳
HOURLY_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]")


def _int(s: str) -> int:
    return int(str(s).replace(",", ""))


def parse_drop_line(line: str, day: str, ts: str, agent_hint: str = "") -> dict | None:
    """一行 💸 → 一条 unseen 记录的原料（纯函数，便于单测）。

    agent：09:35 格式带 `[agent]`，整点轮格式是 `💸 <agent> 单票预算`。
    解析不出 agent 或代码组时返回 None（宁可少记，不记来源不明的行）。
    """
    if "💸" not in line:
        return None
    m_agent = re.search(r"💸\s*\[([^\]]+)\]", line)
    if m_agent:
        agent = m_agent.group(1).strip()
    else:
        m_agent = re.search(r"💸\s+(\S+)\s*单票预算", line)
        agent = m_agent.group(1).strip() if m_agent else agent_hint
    items = ITEM_RE.findall(line)
    if not (agent and items and day):
        return None
    mb = BUDGET_RE.search(line)
    budget = _int(mb.group(1)) if mb else None
    return {"agent": agent, "day": day[:10], "ts": ts, "budget": budget,
            "items": [{"code": c, "name": n.strip(), "min_qty": _int(q),
                       "min_cost": _int(v)} for c, n, q, v in items]}


def rows_from_llm_log(text: str, origin: str = "live_llm_trade.log") -> list[dict]:
    """09:35 主入口日志：💸 行靠轮头（🔴 实盘执行 2026-09-14 09:35:01）定位日期。"""
    rows, day, ts, live = [], "", "", True
    for line in text.splitlines():
        m = LLM_HEAD_RE.search(line)
        if m and ("实盘执行" in line or "演练" in line):
            live = m.group(1) == "🔴"
            day, ts = m.group(2), f"{m.group(2)}T{m.group(3)}+08:00"
            continue
        parsed = parse_drop_line(line, day, ts)
        if not parsed:
            continue
        rows.extend(_drop_rows(parsed, origin, dry=not live))
    return rows


def rows_from_hourly_log(text: str, origin: str = "live_hourly_analysis.log") -> list[dict]:
    """整点轮日志：每行自带 `[2026-09-14 10:19:03]` 前缀。"""
    rows = []
    for line in text.splitlines():
        m = HOURLY_LINE_RE.match(line)
        if not m:
            continue
        parsed = parse_drop_line(line, m.group(1), f"{m.group(1)}T{m.group(2)}+08:00")
        if parsed:
            rows.extend(_drop_rows(parsed, origin, dry=False))
    return rows


def _drop_rows(parsed: dict, origin: str, dry: bool) -> list[dict]:
    src = f"backfill:{origin}" + (".dry" if dry else "")
    n = len(parsed["items"])
    out = []
    for it in parsed["items"]:
        r = ghost_ledger.row(
            parsed["agent"], it["code"], gate_rules.BUDGET_UNAFFORDABLE,
            day=parsed["day"], ts=parsed["ts"], name=it["name"],
            ref_px=(it["min_cost"] / it["min_qty"]) if it["min_qty"] else None,
            reason=(f"单票预算 ¥{parsed['budget']:,} < 最小 {it['min_qty']} 股 "
                    f"¥{it['min_cost']:,}" if parsed.get("budget") else "资金剔除（历史回填）"),
            source=src,
            extra={"min_cost": it["min_cost"], "min_qty": it["min_qty"],
                   "n_dropped": n, "budget": parsed.get("budget"), "origin": origin})
        if r:
            out.append(r)
    return out


# ---------------------------------------------------------------- 意图无落地

def trade_evidence(trade_dir: Path) -> dict[str, set[tuple[str, str]]]:
    """日 → {(agent, code)}：当日**有委托号**的买/卖记录（证明单子真的下出去了）。"""
    out: dict[str, set[tuple[str, str]]] = {}
    for p in sorted(trade_dir.glob("live_trade_[0-9]*.jsonl")):
        day = p.stem.split("_")[-1]
        ev: set[tuple[str, str]] = set()
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            oid = ((rec.get("fill") or {}).get("order_id")
                   or (rec.get("result") or {}).get("order_id"))
            if oid and rec.get("agent") and rec.get("code"):
                ev.add((str(rec["agent"]), str(rec["code"])))
        out[day] = ev
    return out


def rows_no_execution(pool_path: Path = DECISION_POOL, trade_dir: Path = LOGS,
                      origin: str = "decision_pool") -> tuple[list[dict], dict]:
    """决策池的 buy 意图 × 成交流水差集 → kind=unknown 的"意图无落地"。

    返回 (rows, stats)。stats 里 uncomparable_days 是**成交流水缺失**的日期
    （不可比，单独计数）：把"日志没留"算成"订单丢了"会凭空造出一批假事件。
    """
    if not pool_path.is_file():
        return [], {"n_intents": 0, "matched": 0, "uncomparable": 0, "uncomparable_days": []}
    pool = [json.loads(l) for l in pool_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ev = trade_evidence(trade_dir)
    rows, stats = [], {"n_intents": 0, "matched": 0, "uncomparable": 0,
                       "uncomparable_days": [], "noexec": 0}
    for r in pool:
        if str(r.get("action") or "").lower() != "buy":
            continue
        stats["n_intents"] += 1
        day = str(r.get("date") or "")[:10]
        key = (str(r.get("agent") or ""), str(r.get("code") or ""))
        d8 = day.replace("-", "")
        if d8 not in ev:                      # 没有那天的成交流水 = 不可比
            stats["uncomparable"] += 1
            if day not in stats["uncomparable_days"]:
                stats["uncomparable_days"].append(day)
            continue
        if key in ev[d8]:
            stats["matched"] += 1
            continue
        stats["noexec"] += 1
        row = ghost_ledger.row(
            key[0], key[1], gate_rules.UNKNOWN_NO_EXEC, day=day, ts=str(r.get("ts") or ""),
            name=str(r.get("name") or ""), pct=r.get("pct"),
            reason="决策池里的买入意图在当日成交流水中找不到委托号（原因不可考）",
            source=f"backfill:{origin}",
            extra={"pool_id": r.get("id"), "pool_source": r.get("source")})
        if row:
            rows.append(row)
    return rows, stats


# ---------------------------------------------------------------- CLI

def collect(only: str = "") -> tuple[list[dict], dict]:
    rows, stats = [], {}
    if only in ("", "unaffordable"):
        if LLM_LOG.is_file():
            got = rows_from_llm_log(LLM_LOG.read_text(encoding="utf-8", errors="ignore"))
            rows.extend(got)
            stats["live_llm_trade.log"] = len(got)
        if HOURLY_LOG.is_file():
            got = rows_from_hourly_log(HOURLY_LOG.read_text(encoding="utf-8", errors="ignore"))
            rows.extend(got)
            stats["live_hourly_analysis.log"] = len(got)
    if only in ("", "noexec"):
        got, st = rows_no_execution()
        rows.extend(got)
        stats["no_execution"] = st
    return rows, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影子账历史回填（默认预览，--write 落盘）")
    ap.add_argument("--write", action="store_true", help="真正写入 logs/ghost_ledger.jsonl")
    ap.add_argument("--only", choices=["unaffordable", "noexec"], default="",
                    help="只回填某一类（默认两类都做）")
    args = ap.parse_args(argv)

    rows, stats = collect(args.only)
    existing = {r.get("id") for r in ghost_ledger.load_ghost()}
    seen, uniq = set(), []
    for r in rows:                       # 批内去重（同一天多轮剔除同一只票是同一件事）
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        uniq.append(r)
    todo = [r for r in uniq if r["id"] not in existing]

    print(f"📜 影子账历史回填：解析出 {len(rows)} 条事件 → 去重后 {len(uniq)} 个独立事件；"
          f"账内已有 {len(uniq) - len(todo)} 个，待写入 {len(todo)} 个")
    for k, v in stats.items():
        print(f"    {k}: {v}")
    if isinstance(stats.get("no_execution"), dict) and stats["no_execution"].get("uncomparable"):
        print(f"    ⚠️ 成交流水缺失的日期（不可比，未计入）："
              f"{stats['no_execution']['uncomparable_days']}")
    by_day: dict[str, int] = {}
    for r in todo:
        by_day[str(r["date"])] = by_day.get(str(r["date"]), 0) + 1
    for day in sorted(by_day):
        print(f"    {day}: {by_day[day]} 条")

    if not args.write:
        print("🟡 预览模式（未写盘）。确认无误后加 --write。")
        return 0
    n = ghost_ledger.record(todo)
    print(f"✅ 已写入 {n} 条 → {ghost_ledger.GHOST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
