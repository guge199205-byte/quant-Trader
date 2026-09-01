#!/usr/bin/env python3
"""模型自主调仓：候选池(20只) + 现有持仓 + 大盘方向 → 各模型独立决策 → 桥执行。

与 live_trade_picks.py（确定性规则轮值分配）不同：这里是**模型自己选择**买卖——
每 agent 用自己的模型看自己的持仓 + 候选池打分，输出 JSON 决策
（持有/卖出/买入 + 比例 + 理由），脚本只做闸门校验与执行。

流程：
  1. 候选池: select_from_reports.py --source picks --top 20 --min-side HOLD
     （picks.json 来自盘后 6 维打分：L2/融合/L1/持仓/板块/新闻 + 大盘方向门控）
  2. 账户 + 分账账本: 现有持仓（成本/现价/盈亏/可卖量 T+1）与额度（¥10 万/agent）
  3. 决策: 每 agent 独立调 LLM，prompt 含持仓表/候选表/大盘方向/额度，
     「现有持股更优 → 全 hold 不换」由模型自行判断
  4. 校验: 买入不追涨停、单票 ≤ 剩余额度 20%、100 股整数倍、账户现金兜底；
     卖出只卖可卖量（T+1 当日买入跳过）、不接跌停
  5. 执行: 先卖后买 → 桥限价(±1%) → 分账记账 → 决策全文写模型对话 tab

安全：
  - 默认 --dry-run：只出决策清单，不下单（LLM 调用照常，便于看决策质量）
  - --execute 才真下；LLM 返回非 JSON/解析失败 → 该 agent 跳过并记录，不影响他人

用法：
  python scripts/live_llm_trade.py                      # 全部 agent dry-run
  python scripts/live_llm_trade.py --execute            # 全部 agent 实盘调仓
  python scripts/live_llm_trade.py --agents deepseek-v4-flash --execute
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))  # live_trade_picks/live_ledger 顶层互引依赖 scripts/ 在 path
sys.path.insert(0, str(ROOT))

from live_trade_picks import (  # noqa: E402
    compute_order,
    enabled_agents,
    log_line,
    now_cn,
    TdxBridgeBroker,
)
from live_ledger import (  # noqa: E402
    AGENT_QUOTA,
    agent_remaining,
    agent_used,
    find_holder,
    load_ledger,
    record_buy,
    record_sell,
    save_ledger,
)

CN_TZ = ZoneInfo("Asia/Shanghai")
PICKS_JSON = ROOT / ".." / "projects" / "quantmind" / "data" / "reports" / "stock_picks"
PER_STOCK_PCT = 0.2   # 单票买入 ≤ 剩余额度 20%
BUY_LIMIT_UP = 9.9    # 涨停不追
SELL_LIMIT_DOWN = -9.9  # 跌停不接


def intraday_exec_enabled() -> bool:
    """自动执行开关：configs/intraday_exec.json {"enabled": true}。
    外部调度（cron/面板）不传 --execute 时，开关开启则同样自动执行。"""
    try:
        cfg = json.loads((ROOT / "configs" / "intraday_exec.json").read_text(encoding="utf-8"))
        return bool(cfg.get("enabled"))
    except (OSError, json.JSONDecodeError):
        return False


# ---------- 候选池 ----------

def load_pool(top: int = 20) -> tuple[list, dict]:
    """候选池：picks.json → 决策用表格行 + 大盘方向。
    返回 (rows, market_direction)；无池子返回 ([], {})。"""
    sel = subprocess.run(
        [sys.executable, "scripts/select_from_reports.py", "--source", "picks",
         "--top", str(top), "--min-side", "HOLD", "--json"],
        capture_output=True, text=True, check=False, cwd=ROOT)
    try:
        pool = json.loads(sel.stdout or "[]")
    except json.JSONDecodeError:
        pool = []
    # 大盘方向：最新 picks.json 顶层 market_direction
    direction = {}
    files = sorted(PICKS_JSON.glob("*_picks.json"))
    if files:
        try:
            d = json.loads(files[-1].read_text(encoding="utf-8"))
            direction = d.get("market_direction") or {}
        except (OSError, json.JSONDecodeError):
            pass
    return pool, direction


def pool_rows(pool: list) -> list[str]:
    """候选池 → markdown 表格行（select_from_reports --json 字段：
    code/name/industry/side/score/fusion/rank）。"""
    rows = []
    for p in pool:
        fusion = p.get("fusion")
        fus = f"{fusion:.3f}" if isinstance(fusion, (int, float)) else "—"
        rows.append(
            f"| {p.get('rank', '—')} | {p.get('code', '')} | {p.get('name', '')} "
            f"| {p.get('industry', '')} | {p.get('score', 0):.3f} | {p.get('side', 'HOLD')} "
            f"| {fus} |")
    return rows


# ---------- 持仓 ----------

def holding_rows(broker, positions: list) -> list[dict]:
    """桥持仓 → 决策行：名称/成本/现价/盈亏%/可卖量（T+1）/今日涨跌。"""
    from live_hourly_analysis import load_names

    names = load_names()
    rows = []
    for p in positions:
        code = p.get("stock_code") or ""
        if not code:
            continue
        cost = float(p.get("cost_price") or 0)
        price = float(p.get("last_price") or 0)
        volume = float(p.get("total_volume") or 0)
        if not price:
            try:
                quote = broker.get_quote(code, "")
                price = float((quote or {}).get("close") or 0)
            except Exception:  # noqa: BLE001
                price = 0
        day_chg = None
        try:
            klines = broker.get_klines(code, interval="daily")
            if len(klines) >= 2:
                prev = float(klines[-2]["close"])
                if prev:
                    day_chg = (price - prev) / prev * 100
        except Exception:  # noqa: BLE001
            pass
        rows.append({
            "code": code, "name": names.get(code, code),
            "volume": int(volume), "cost": round(cost, 2),
            "price": round(price, 2),
            "pnl_pct": round((price - cost) / cost * 100, 2) if cost else 0.0,
            "day_chg": round(day_chg, 2) if day_chg is not None else None,
            "avail": int(p.get("available_volume") or 0),
        })
    return rows


# ---------- LLM 决策 ----------

DECISION_SCHEMA = (
    '{"decisions": [{"action": "hold|sell|buy", "code": "600519.SH", '
    '"pct": 0.2, "reason": "一句话理由"}]}')


def build_prompt(agent: str, holdings: list[dict], pool_rows: list[str],
                 direction: dict, quota_remaining: float) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（开盘后）。你是 {agent} 的 A股实盘调仓决策模型，"
        f"管理 ¥{AGENT_QUOTA:,.0f} 虚拟额度（已用 ¥{agent_used(load_ledger(), agent):,.0f}，"
        f"剩余 ¥{quota_remaining:,.0f}）。",
        "",
        "【你名下的现有持仓】（成本/现价/盈亏%/可卖量，可卖量 0 = 今日买入 T+1 不可卖）：",
        "",
        "| 代码 | 名称 | 数量 | 成本 | 现价 | 盈亏% | 今日涨跌% | 可卖量 |",
        "|------|------|------|------|------|-------|-----------|--------|",
    ]
    for h in holdings:
        lines.append(
            f"| {h['code']} | {h['name']} | {h['volume']} | {h['cost']} | {h['price']} "
            f"| {h['pnl_pct']:+.2f}% | {h['day_chg']:+.2f}% | {h['avail']} |")
    lines += ["", "【今日大盘方向】（盘后 6 维模型打分产出）：",
              f"{direction.get('direction', '—')}（总分 {direction.get('total_score', '—')}/11）", ""]
    if pool_rows:
        lines += ["【候选池】（盘后 6 维打分：L2 40% + 融合 25% + L1 15% + 持仓 10% + 板块 5% + 新闻 5%）",
                  "",
                  "| 排名 | 代码 | 名称 | 行业 | 总分 | 信号 | 关键 | 备注 |",
                  "|------|------|------|------|------|------|------|------|"]
        lines += pool_rows
    lines += [
        "",
        "【决策规则】",
        "1. 逐只现有持仓判断：hold（继续持有）/ sell（减仓或清仓换股）。"
        "如果现有持股趋势/基本面仍优于候选池，可以全部 hold 不换股。",
        "2. 需要买入时从候选池选：优先分数高、行业顺大盘方向的。",
        "3. sell 的 pct = 卖出可卖量的比例（0~1）；buy 的 pct = 使用剩余额度的比例（每票 ≤0.2）。",
        "4. T+1：可卖量 0 的持仓不能卖。",
        "5. 输出**严格 JSON**（不要 markdown 代码块、不要额外文字），格式：",
        DECISION_SCHEMA,
    ]
    return "\n".join(lines)


def parse_decision(text: str) -> list | None:
    """LLM 输出 → 决策列表。容忍 ```json 围栏；解析失败返回 None。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    payload = m.group(1) if m else text
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return None
    out = []
    for x in (d.get("decisions") if isinstance(d, dict) else None) or []:
        action = (x.get("action") or "").lower()
        if action not in ("hold", "sell", "buy"):
            continue
        out.append({
            "action": action,
            "code": str(x.get("code") or "").strip(),
            "pct": float(x.get("pct") or 0),
            "reason": str(x.get("reason") or ""),
        })
    return out or None


# ---------- 执行 ----------

def sell_one(broker, code: str, volume: int, limit: float, agent: str | None) -> dict:
    """桥卖出 + 分账记账（agent 为空 = 总账户持仓，只记券商）。"""
    result = broker.sell(None, None, code, volume, price=limit)
    if agent:
        ledger = load_ledger()
        ledger = record_sell(ledger, agent, code, volume, limit, now_cn().isoformat())
        save_ledger(ledger)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="模型自主调仓（候选池 + 持仓 → LLM 决策 → 桥执行）")
    ap.add_argument("--execute", action="store_true", help="真下单（默认仅 dry-run 决策演练）")
    ap.add_argument("--agents", default="", help="只跑指定 agent（逗号分隔；默认全部 enabled）")
    ap.add_argument("--top", type=int, default=20, help="候选池上限（默认 20）")
    args = ap.parse_args()

    # 配置开关：外部调度不传 --execute 时也可自动执行（configs/intraday_exec.json）
    args.execute = args.execute or intraday_exec_enabled()

    mode = "🔴 实盘执行" if args.execute else "🟡 DRY-RUN 决策演练（不下单）"
    print(f"{mode}  {now_cn():%F %T}")
    if not args.execute:
        print("ℹ️  LLM 决策会真实调用（看模型判断质量），仅不下单")

    broker = TdxBridgeBroker()
    acct = broker._account_query()
    asset = float((acct.get("asset") or {}).get("asset") or 0)
    cash = float((acct.get("asset") or {}).get("cash") or 0)
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    holdings = holding_rows(broker, positions)
    print(f"💰 账户资产 ¥{asset:,.0f} 现金 ¥{cash:,.0f} 持仓 {len(holdings)} 只")

    pool, direction = load_pool(args.top)
    if not pool:
        print("❌ 无候选池（picks.json 缺失或为空），终止")
        return 1
    pool_table = pool_rows(pool)
    print(f"📋 候选池 {len(pool)} 只  大盘: {direction.get('direction', '—')}")

    from live_hourly_analysis import append_log, call_llm

    agents = [a.strip() for a in args.agents.split(",") if a.strip()] or enabled_agents()
    ledger = load_ledger()
    ok = 0
    for agent in agents:
        # 账本 positions 是 {code: {volume, cost_price, ...}} 字典（live_ledger 内部结构）
        mine = set((ledger.get("agents") or {}).get(agent, {}).get("positions", {}))
        my_holdings = [h for h in holdings if h["code"] in mine] if mine else holdings
        remaining = agent_remaining(ledger, agent)
        prompt = build_prompt(agent, my_holdings, pool_table, direction, remaining)
        content, usage = "", None
        try:
            content, usage = call_llm(prompt, agent)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [{agent}] LLM 调用失败: {exc}")
        decisions = parse_decision(content)
        if not decisions:
            print(f"⚠️ [{agent}] 决策解析失败，跳过（LLM 原文前 200 字）："
                  f"{content[:200]!r}")
            continue
        # 决策展示 + 校验
        sells, buys, summary = [], [], [f"（模型自主调仓决策，{now_cn():%F %T}）"]
        for d in decisions:
            code = d["code"]
            if d["action"] == "hold":
                print(f"  🟢 [{agent}] 持有 {code}: {d['reason']}")
                continue
            h = next((x for x in holdings if x["code"] == code), None)
            if d["action"] == "sell":
                if not h:
                    print(f"  ⚠️ [{agent}] 卖出 {code}: 非持仓，跳过")
                    continue
                avail = h["avail"]
                if avail <= 0:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: T+1 不可卖（可卖量 0），跳过")
                    continue
                vol = int(avail * min(max(d["pct"], 0), 1) / 100) * 100
                if vol <= 0:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 比例 {d['pct']} 不足 1 手，跳过")
                    continue
                if h["day_chg"] is not None and h["day_chg"] <= SELL_LIMIT_DOWN:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 跌停（{h['day_chg']:+.2f}%），不接")
                    continue
                sells.append((code, vol, d["reason"]))
                print(f"  📉 [{agent}] 卖出 {code} {vol}/{avail}股 "
                      f"({d['pct']:.0%}): {d['reason']}")
            elif d["action"] == "buy":
                if code not in {p["code"] for p in pool}:
                    print(f"  ⚠️ [{agent}] 买入 {code}: 不在候选池，跳过")
                    continue
                pct = min(max(d["pct"], 0), PER_STOCK_PCT)
                if pct <= 0:
                    print(f"  ⏭️ [{agent}] 买入 {code}: pct=0，跳过")
                    continue
                buys.append((code, pct, d["reason"]))
                print(f"  📈 [{agent}] 买入 {code} 用剩余额度 {pct:.0%}"
                      f"（≤{PER_STOCK_PCT:.0%}）: {d['reason']}")
        if not sells and not buys:
            print(f"  ⏸️ [{agent}] 无买卖动作（全 hold）")
        # 决策摘要 → 模型对话 tab（决策 + 理由可回看）
        if not sells and not buys:
            summary.append("决策：全部持有，不调仓（现有持股更优或候选不具吸引力）。")
        for code, vol, reason in sells:
            summary.append(f"- 卖出 {code} {vol} 股：{reason}")
        for code, pct, reason in buys:
            summary.append(f"- 买入 {code}（用剩余额度 {pct:.0%}）：{reason}")
        for d in decisions:
            if d["action"] == "hold":
                summary.append(f"- 持有 {d['code']}：{d['reason']}")
        try:
            append_log(prompt, "\n".join(summary), agent, usage)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ [{agent}] 对话日志写入失败: {exc}")
        if not args.execute:
            ok += 1
            continue

        # 执行：先卖后买
        from agent_tools.datasources import tdx_aidata

        try:
            bars_map = tdx_aidata.get_klines_batch(
                [c for c, _, _ in sells + buys], interval="daily", count=5)
        except Exception:  # noqa: BLE001
            bars_map = {}
        for code, vol, _ in sells:
            bars = bars_map.get(code) or broker.get_klines(code, interval="daily")[-5:]
            if len(bars) < 2:
                print(f"  ⚠️ [{agent}] 卖出 {code}: 行情不足，跳过")
                continue
            price = float(bars[-1].get("close") or 0)
            if price <= 0:
                continue
            try:
                result = broker.sell(None, None, code, vol, price=round(price * 0.99, 2))
                print(f"  ✅ [{agent}] 卖出 {code} 已受理: {result}")
                # 分账记账：卖出扣减名下持仓、释放额度、虚拟现金加回（与买入对称）
                ledger = record_sell(ledger, agent, code, vol,
                                     round(price * 0.99, 2), now_cn().isoformat())
                save_ledger(ledger)
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "volume": vol, "price": round(price * 0.99, 2),
                          "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "volume": vol, "error": str(exc)})
            time.sleep(1)  # 桥限流
        # 卖出回款后账户现金可能变化，重新查一次
        acct2 = broker._account_query()
        cash = float((acct2.get("asset") or {}).get("cash") or 0)
        ledger = load_ledger()
        for code, pct, _ in buys:
            remaining = agent_remaining(ledger, agent)
            bars = bars_map.get(code) or broker.get_klines(code, interval="daily")[-5:]
            o = compute_order(bars, remaining, pct)
            if not o["ok"]:
                print(f"  ⏭️ [{agent}] 买入 {code}: {o['reason']}")
                continue
            if o["cost"] > cash:
                print(f"  ⏭️ [{agent}] 买入 {code}: 账户现金不足 ¥{o['cost']:,.0f} < ¥{cash:,.0f}")
                continue
            cash -= o["cost"]
            try:
                result = broker.buy(None, None, code, o["volume"], price=o["limit_price"])
                print(f"  ✅ [{agent}] 买入 {code} {o['volume']}股 "
                      f"限价 ¥{o['limit_price']:.2f} 已受理: {result}")
                ledger = record_buy(ledger, agent, code, o["volume"], o["price"],
                                    now_cn().isoformat())
                save_ledger(ledger)
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "volume": o["volume"], "price": o["price"],
                          "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "error": str(exc)})
            time.sleep(1)
        ok += 1

    print(f"\n{'✅ 调仓执行完成' if args.execute else '🟡 决策演练完成（--execute 才下单）'}"
          f"：{ok}/{len(agents)} agent 成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
