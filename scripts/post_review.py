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


def run_review(agent: str, date: str, dry: bool = False) -> int:
    from dsh_agent import run_agent

    display = {"deepseek-v4-flash": "v4-flash", "deepseek-v4-pro": "v4-pro",
               "glm-5.3-flash": "glm"}.get(agent, agent)
    facts = collect_facts(agent, date)
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
    # JSON 抽取：平衡块扫描（兼容围栏/尾随文本/多块），失败留空结构
    def json_blocks(text: str) -> list:
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

    payload = {"lessons": [], "plan": [], "watch": [], "hypothesis_candidates": []}
    for b in json_blocks(content):
        if not isinstance(b, dict):
            continue
        for k in payload:
            if b.get(k) is not None:
                payload[k] = b[k]
    payload["date"] = date
    payload["agent"] = agent
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