#!/usr/bin/env python3
"""盘后复盘 Agent：逐 agent 复盘当日交易 → memory 沉淀 → 明日预案。

设计见 docs/REVIEW_AGENT_DESIGN.md（阶段1：自我进化闭环）。
用法：
  python scripts/post_review.py                     # 全部 enabled 分账 agent，昨日/当日
  python scripts/post_review.py --agent glm-5.3-flash --date 2026-09-03
复盘只读+append_memory，不交易；失败降级为数据摘要，不静默。
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "prompts"))

from prompts.review_workbook import build_review_prompt  # noqa: E402


def now_cn() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai"))


def collect_facts(agent: str, date: str) -> str:
    """当日事实：成交/决策/净值/持仓/盘面。任何源失败都不阻塞。"""
    lines = []

    # 1) 成交（有回报的买卖）
    sells = []
    logs = sorted((ROOT / "logs").glob("live_trade_*.jsonl"))
    for f in logs:
        if "_us_" in f.name or "_hk_" in f.name:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("agent") != agent or r.get("error") or not r.get("side"):
                continue
            ts = str(r.get("ts") or "")
            if ts[:10] != date:
                continue
            fv = int((r.get("fill") or {}).get("filled_volume") or r.get("volume") or 0)
            fp = (r.get("fill") or {}).get("filled_price") or r.get("price")
            if fv > 0:
                sells.append({"ts": ts, "side": r["side"], "code": r.get("code"),
                              "vol": fv, "price": float(fp) if fp else None,
                              "mode": r.get("mode")})
    if sells:
        lines.append("【当日成交回报】")
        for s in sells:
            lines.append(f"- {s['ts'][5:16]} {s['side']} {s['code']} "
                         f"{s['vol']}股 @ {s['price']}（{s['mode']}）")
    else:
        lines.append("【当日成交回报】无（或全部被拒单）")

    # 2) 当日决策轮（assistant 摘要前 500 字/轮）
    lf = ROOT / "data" / "agent_data_astock" / agent / "log" / date / "log.jsonl"
    if lf.is_file():
        lines.append("【当日分析轮次摘要】")
        try:
            rows = [json.loads(l) for l in lf.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, ValueError):
            rows = []
        for r in rows[-12:]:
            for m in (r.get("new_messages") or []):
                if str(m.get("role")) in ("assistant", "ai"):
                    c = str(m.get("content") or "").replace("\n", " ")
                    lines.append(f"- {r.get('timestamp', '')[:16]} {c[:360]}")
                    break
    else:
        lines.append("【当日分析轮次摘要】无日志")

    # 3) 当日净值轨迹
    eq = []
    try:
        for l in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("agent") == agent and str(r.get("date")) == date and r.get("value") is not None:
                eq.append(r["value"])
    except (OSError, ValueError):
        pass
    if eq:
        lines.append(f"【当日净值】开盘后首 {eq[0]} · 末 {eq[-1]} · 高 {max(eq)} · 低 {min(eq)}"
                     f" · 采样 {len(eq)} 点")
    # 4) 当前持仓
    try:
        led = json.loads((ROOT / "logs" / "live_ledger.json").read_text(encoding="utf-8"))
        rec = (led.get("agents") or {}).get(agent) or {}
        pos = rec.get("positions") or {}
        if pos:
            lines.append("【当前持仓】" + "；".join(
                f"{c} {p.get('volume')}股@成本{p.get('cost_price')}" for c, p in pos.items()))
        else:
            lines.append("【当前持仓】空仓")
    except (OSError, ValueError):
        pass
    return "\n".join(lines)


def json_blocks(text: str) -> list:
    """平衡块扫描 JSON（P0-3 可测）。"""
    blocks, i = [], 0
    while i < len(text):
        st = text.find("{", i)
        if st < 0:
            break
        depth, in_str, j = 0, False, st
        while j < len(text):
            c = text[j]
            if in_str:
                if c == "\\":
                    j += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        try:
            blocks.append(json.loads(text[st:j]))
        except json.JSONDecodeError:
            pass
        i = j
    return blocks



import re as _re


def _extract_price(text: str) -> float | None:
    """从预案 trigger/文字提取首个价位（75.5 / 80.00）。"""
    if not text:
        return None
    m = _re.search(r"(\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None


def sync_plan_to_watch(agent: str, payload: dict) -> int:
    """P0：复盘预案/观察 → 分钟哨兵条件位（live_watch.json），agent 整组替换。
    plan: action sell+stop_loss/take_profit 或 trigger 含价 → watch 规则；
    watch: {code, price, action} → 按 action 映射。文本含'半' → pct 0.5。
    返回落盘规则数（0 = 该 agent 条件位被清空）。"""
    from live_price_watch import save_watch_rules

    rules = []
    for it in (payload.get("plan") or []):
        if not isinstance(it, dict) or not it.get("code"):
            continue
        act = str(it.get("action") or "").lower()
        if act not in ("sell", "buy", "watch"):
            continue
        code = str(it["code"])
        sl = it.get("stop_loss")
        tp = it.get("take_profit")
        if sl is None:
            sl = _extract_price(str(it.get("trigger") or "") +
                                (f" 跌破 {it.get('stop_loss')}" if it.get("stop_loss") else ""))
        if sl is None and tp is None:
            sl = _extract_price(str(it.get("trigger") or ""))
        txt = str(it.get("reason") or "") + str(it.get("trigger") or "")
        m_cut = _re.search(r"减\s*(\d{1,3})\s*%", txt)
        if m_cut:
            pct = min(max(int(m_cut.group(1)) / 100, 0.0), 1.0)
        elif "半" in txt:
            pct = 0.5
        else:
            m_bare = _re.search(r"(?<![+\-])(\d{1,3})\s*%", txt)
            pct = min(max(int(m_bare.group(1)) / 100, 0.0), 1.0) if m_bare else 1.0
        if act == "buy":
            rules.append({"code": code, "take_profit": tp or sl, "pct": pct,
                          "reason": str(it.get("reason") or "")[:80]})
        else:
            rules.append({"code": code, "stop_loss": sl or tp, "pct": pct,
                          "reason": str(it.get("reason") or "")[:80]})
    for it in (payload.get("watch") or []):
        if not isinstance(it, dict) or not it.get("code"):
            continue
        price = it.get("price") or _extract_price(str(it.get("trigger") or ""))
        act = str(it.get("action") or "sell").lower()
        if not price:
            continue
        txt = str(it.get("reason") or "") + str(it.get("trigger") or "")
        m_cut = _re.search(r"减\s*(\d{1,3})\s*%", txt)
        if m_cut:
            pct = min(max(int(m_cut.group(1)) / 100, 0.0), 1.0)
        elif "半" in txt:
            pct = 0.5
        else:
            m_bare = _re.search(r"(?<![+\-])(\d{1,3})\s*%", txt)
            pct = min(max(int(m_bare.group(1)) / 100, 0.0), 1.0) if m_bare else 1.0
        if act == "buy":
            rules.append({"code": str(it["code"]), "take_profit": price, "pct": pct,
                          "reason": str(it.get("reason") or "")[:80]})
        else:
            rules.append({"code": str(it["code"]), "stop_loss": price, "pct": pct,
                          "reason": str(it.get("reason") or "")[:80]})
    decisions = []
    for r in rules:
        decisions.append({"action": "watch", "code": r["code"],
                          "stop_loss": r.get("stop_loss"), "take_profit": r.get("take_profit"),
                          "pct": r["pct"], "reason": r["reason"]})
    n = save_watch_rules(agent, decisions)
    print(f"  🔔 预案→哨兵同步 {agent}: {n} 条条件位")
    return n


def load_yesterday_outcome(agent: str, date: str) -> str:
    """昨日预案结局对照（自动判定触发）→ 注入今日复盘。
    用桥日K最近两根（昨收与今日高低收）对照昨日 plan/watch 价位。"""
    import datetime as _dt

    today = _dt.date.fromisoformat(date)
    p = ROOT / "logs" / "review" / agent / f"{today - _dt.timedelta(days=1):%Y-%m-%d}.json"
    if not p.is_file():
        return ""
    try:
        y = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    items = (y.get("plan") or []) + (y.get("watch") or [])
    priced = []
    for it in items:
        if not isinstance(it, dict) or not it.get("code"):
            continue
        price = it.get("price") or it.get("stop_loss") or it.get("take_profit")             or _extract_price(str(it.get("trigger") or ""))
        if price:
            priced.append((str(it["code"]), str(it.get("action") or "watch"), float(price),
                           str(it.get("reason") or "")[:60]))
    if not priced:
        return ""
    try:
        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        broker = TdxBridgeBroker()
        lines = ["【昨日预案对照（自动判定，供归因）】"]
        ok = 0
        for code, act, price, reason in priced:
            try:
                bars = broker.get_klines(code, interval="daily")
            except Exception:  # noqa: BLE001
                continue
            if not bars or len(bars) < 2:
                continue
            last = bars[-1]
            if str(last.get("date") or "")[:8] != date.replace("-", ""):
                lines.append(f"- {code}: 今日 bar 未就绪，无法对照")
                continue
            low, high = float(last.get("low") or 0), float(last.get("high") or 0)
            close = float(last.get("close") or 0)
            if act == "buy" and high >= price:
                lines.append(f"- {code} 买入观察@{price}: ✅ 触发（高 {high}），今收 {close}")
            elif act == "sell" and low <= price:
                lines.append(f"- {code} 卖出预案@{price}: ✅ 触发（低 {low}），今收 {close}")
            else:
                lines.append(f"- {code} {act}@{price}: ⬜ 未触发（区间 {low}-{high}）")
            ok += 1
        lines.append("注：触发后的对错交给本轮归因判断（今日结果是否如预案预期）。")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def run_review(agent: str, date: str, dry: bool = False) -> int:
    from dsh_agent import run_agent

    display = {"deepseek-v4-flash": "v4-flash", "deepseek-v4-pro": "v4-pro",
               "glm-5.3-flash": "glm"}.get(agent, agent)
    facts = collect_facts(agent, date)
    outcome = load_yesterday_outcome(agent, date)  # 昨日预案结局对照（自动判定）
    if outcome:
        facts += "\n\n" + outcome
    prompt = build_review_prompt(agent, display, date, facts)
    try:
        from market_state import build_market_state

        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        try:
            prompt += "\n\n【盘面】" + build_market_state(TdxBridgeBroker())
        except Exception:  # noqa: BLE001
            prompt += "\n\n【盘面】桥不可用（降级）"
    except Exception:  # noqa: BLE001
        pass
    if dry:
        print(prompt[:2000])
        return 0
    from live_hourly_analysis import agent_model_for

    out_dir = ROOT / "logs" / "review" / agent
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        content = run_agent(prompt, timeout_s=360, model=agent_model_for(agent))
    except Exception as exc:  # noqa: BLE001
        content = f"（LLM 复盘失败，降级数据摘要：{exc}）\n\n{facts}"
    (out_dir / f"{date}.md").write_text(
        f"# {agent} 盘后复盘 · {date}\n\n" + content, encoding="utf-8")
    # JSON 抽取（json_blocks 模块级）
    payload = {"lessons": [], "plan": [], "watch": [], "hypothesis_candidates": []}
    for b in json_blocks(content):
        if not isinstance(b, dict):
            continue
        for k in payload:
            if b.get(k) is not None:
                payload[k] = b[k]
    # v2：复盘 hypothesis_candidates 自动登记入假设库（状态 proposed 待复测）
    try:
        hyp_path = ROOT / "configs" / "hypotheses.json"
        hyps = json.loads(hyp_path.read_text(encoding="utf-8")) if hyp_path.is_file() else {}
        changed = False
        for i, cand in enumerate(payload.get("hypothesis_candidates") or []):
            txt = str(cand.get("description") or cand if isinstance(cand, str) else cand)[:120]
            key = f"H_{date}_{i}"
            if key not in hyps and txt:
                hyps[key] = {"name": txt, "direction": str(cand.get("direction") or ""),
                             "win_rate": None, "n": None, "updated": date,
                             "status": "proposed", "source": "review"}
                changed = True
        if changed:
            hyp_path.write_text(json.dumps(hyps, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass
    payload["date"] = date
    payload["agent"] = agent
    try:
        sync_plan_to_watch(agent, payload)  # P0：预案/观察 → 分钟哨兵条件位
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 预案→哨兵同步失败: {exc}")
    (out_dir / f"{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    # 写回 agent 对话日志流（模型对话 tab 可见；kind=review 前端渲染为复盘卡）
    try:
        import zoneinfo

        lf = ROOT / "data" / "agent_data_astock" / agent / "log" / date / "log.jsonl"
        lf.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai")).isoformat(),
               "signature": agent, "kind": "review",
               "new_messages": [
                   {"role": "user", "content": f"【盘后复盘任务】{date} {agent} 当日复盘（只读+沉淀）"},
                   {"role": "assistant", "content": content},
               ]}
        with lf.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 复盘写回对话日志失败: {exc}")
    print(f"✅ {agent} 复盘完成 → logs/review/{agent}/{date}.md/.json "
          f"(lessons={len(payload['lessons'])}, plan={len(payload['plan'])})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="盘后复盘 agent")
    ap.add_argument("--agent", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry", action="store_true", help="只打印任务不执行")
    args = ap.parse_args()
    date = args.date or (now_cn() - timedelta(days=0)).strftime("%Y-%m-%d")
    if args.agent:
        agents = [args.agent]
    else:
        from live_trade_picks import enabled_agents

        agents = enabled_agents()
    rc = 0
    for a in agents:
        try:
            rc |= run_review(a, date, dry=args.dry)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {a} 复盘异常: {exc}")
            rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())