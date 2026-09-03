#!/usr/bin/env python3
"""live_hourly_analysis 的上下文/解析纯函数（P1-2 拆分，行为不变）。
仅依赖标准库；ROOT 与主模块同源（仓库根）。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_trade_recap(agent: str, days: int = 7) -> str:
    """近 N 日成交回顾（事实摘要，防隔日行为漂移）：从成交文件按 agent 聚合
    有成交回报的买卖，每只股票最多列 4 条。只陈述事实不替模型下结论——
    注入的是"我上周做了什么"，不是"我该继续做什么"。"""
    from datetime import datetime, timedelta

    cutoff = (datetime.now().astimezone()
              - timedelta(days=days)).strftime("%Y-%m-%d")
    events: list = []
    for f in sorted((ROOT / "logs").glob("live_trade_*.jsonl")):
        if any(m in f.name for m in ("_us_", "_hk_")):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("error") or r.get("agent") != agent or not r.get("side"):
                continue
            ts = str(r.get("ts") or "")
            if ts[:10] < cutoff:
                continue
            if r.get("mode") == "fill_confirm":
                fv, fp = int(r.get("volume") or 0), r.get("price")
            else:
                fill = r.get("fill") or {}
                fv, fp = int(fill.get("filled_volume") or 0), fill.get("filled_price")
            if fv <= 0:
                continue
            events.append({"ts": ts, "code": r.get("code"),
                           "side": str(r["side"]).lower(), "vol": fv,
                           "price": float(fp or 0)})
    if not events:
        return ""
    events.sort(key=lambda e: e["ts"])
    by_code: dict = {}
    for e in events:
        by_code.setdefault(e["code"], []).append(e)
    lines = []
    for code, evs in by_code.items():
        for e in evs[-4:]:
            d = e["ts"][5:10].replace("-", "/")
            act = "买入" if e["side"] == "buy" else "卖出"
            px = f" @{e['price']:.2f}" if e["price"] else ""
            lines.append(f"- {d} {act} {e['code']} {e['vol']}股{px}")
    if not lines:
        return ""
    return ("【近期交易回顾（近{days}日，仅事实参考）】".format(days=days)
            + "\n" + "\n".join(lines) + "\n"
            + "（回顾只为一致性参考：若当时理由已失效，允许改变方向并写明原因）")




def load_review_recap(agent: str) -> str:
    """昨日复盘要点（logs/review/{agent}/{date}.json）→ 注入今日分析。
    v1.5 自我进化闭环：昨天的教训/预案今天直接可见；行情若已变，以今日
    证据为准（防刻舟求剑）。"""
    import datetime as _dt

    today = _dt.date.today()
    for back in range(1, 6):
        d = today - _dt.timedelta(days=back)
        p = ROOT / "logs" / "review" / agent / f"{d:%Y-%m-%d}.json"
        if not p.is_file():
            continue
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lessons = [str(x)[:90] for x in (r.get("lessons") or [])][:2]
        plan = [f"{x.get('code') or ''} {str(x.get('trigger') or x.get('action') or '')[:40]}"
                for x in (r.get("plan") or []) if isinstance(x, dict)][:2]
        watch = [f"{x.get('code') or ''} {str(x.get('price') or x.get('action') or '')[:24]}"
                 for x in (r.get("watch") or []) if isinstance(x, dict)][:2]
        if not (lessons or plan or watch):
            continue
        lines = [f"【昨日复盘要点（{d:%Y-%m-%d}，参考非指令）】"]
        if lessons:
            lines.append("教训：" + "｜".join("- " + x for x in lessons))
        if plan or watch:
            lines.append("预案：" + "；".join(plan + watch))
        lines.append("注：若今日行情/理由已变化，以今日证据为准。")
        return "\n".join(lines)
    return ""




def parse_intraday_decision(text: str) -> list | None:
    """LLM 输出 → 盘中决策列表（与 live_llm_trade.parse_decision 同构）。
    依次尝试：整段 JSON → ```json 围栏 → 括号平衡块；
    只解析 decisions 数组；未知 action 忽略。
    返回 [{"action","code","pct","stop_loss","take_profit","reason"}]
    （stop_loss/take_profit 仅 watch 有，其余 None），解析失败返回 None。"""
    import re

    if not text:
        return None

    def _num(x) -> float | None:
        """价位解析：None/空/N/A → None；非数字 → None（不炸）。"""
        if x is None or x in ("", "N/A"):
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    def _extract(payload: str) -> list | None:
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            return None
        out = []
        for x in (d.get("decisions") if isinstance(d, dict) else None) or []:
            action = (x.get("action") or "").lower()
            if action not in ("hold", "sell", "buy", "watch"):
                continue
            out.append({
                "action": action,
                "code": str(x.get("code") or "").strip(),
                "name": str(x.get("name") or "").strip(),
                "pct": float(x.get("pct") or 0),
                "stop_loss": _num(x.get("stop_loss")),
                "take_profit": _num(x.get("take_profit")),
                "move_stop": _num(x.get("move_stop")),
                "invalidation": str(x.get("invalidation") or ""),
                "confidence": float(x.get("confidence") or 0),
                "risk_amount": _num(x.get("risk_amount")),
                "reason": str(x.get("reason") or ""),
            })
        return out or None

    # 1) 整段即 JSON（模型只输出决策块）
    r = _extract(text.strip())
    if r:
        return r
    # 2) ```json 围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        r = _extract(m.group(1).strip())
        if r:
            return r
    # 3) 括号平衡块：找所有 {…} 平衡片段，逐个尝试
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    r = _extract(text[i : j + 1])
                    if r:
                        return r
                    break
    return None





def load_hypotheses_summary() -> str:
    """已验证假设摘要（阶段2：带胜率证据，防拿未验证认知当真理）。"""
    try:
        hyp = json.loads((ROOT / "configs" / "hypotheses.json").read_text(encoding="utf-8"))
        lines = []
        for h in hyp.values():
            if h.get("status") not in ("verified", "contradicted") or not h.get("n"):
                continue
            tag = "✅已验证" if h["status"] == "verified" else "🚫证伪"
            if h.get("win_rate") is not None:
                lines.append(f"- {tag} {h.get('name')}: 次5日胜率 {h['win_rate']:.0%} "
                             f"(vs 基准差 {h.get('vs_base_pp'):+.1f}pp · n={h['n']} · {h.get('updated', '')})")
            else:
                lines.append(f"- {tag} {h.get('name')}")
        return ("\n【已验证假设（事件研究，近1年×60样本，对照全样本基准）】\n"
                + "\n".join(lines)) if lines else ""
    except (OSError, ValueError):
        return ""
