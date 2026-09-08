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
                           "price": float(fp or 0),
                           "intent": r.get("intent_volume")})
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
            note = ""
            if e.get("intent") and e["vol"] != int(e["intent"]):
                note = f"（申报意图 {int(e['intent'])} 股，手数合规后实际 {e['vol']} 股）"
            lines.append(f"- {d} {act} {e['code']} {e['vol']}股{px}{note}")
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
        _all = [h for h in hyp.values()
                if h.get("status") in ("verified", "contradicted") and h.get("n")]
        # 反向假设（AI_*）只取 |差异| 前 8 条；正向按既有分组逻辑
        _ai = sorted([h for h in _all if str(h.get("source")) == "factor_screen_inverse"],
                     key=lambda h: -abs(h.get("vs_base_pp") or 0))[:8]
        _ai_ids = {id(h) for h in _ai}
        _v = [h for h in _all if id(h) in _ai_ids
              or str(h.get("source")) != "factor_screen_inverse"]
        # A_ 因子假设只展示 |vs_base_pp| 前 12 条（防 30+ 条刷屏），其余注明条数
        _a_verified = [h for h in _v if str(h.get("source")) == "factor_screen"]
        # 按方向各取前 6（防偏空因 |差异| 大而挤掉偏多展示）
        _a_pos = sorted([h for h in _a_verified if h.get("vs_base_pp", 0) > 0],
                        key=lambda h: -h.get("vs_base_pp", 0))
        _a_neg = sorted([h for h in _a_verified if h.get("vs_base_pp", 0) <= 0],
                        key=lambda h: abs(h.get("vs_base_pp") or 0))
        _a_show = {id(h) for h in (_a_pos[:6] + _a_neg[:6])}
        _a_hidden = len(_a_verified) - len(_a_show)
        for h in _v:
            if h.get("win_rate") is None:
                lines.append(f"- {'✅已验证' if h['status'] == 'verified' else '🚫证伪'} {h.get('name')}")
                continue
            if id(h) in _a_show or str(h.get("source")) != "factor_screen":
                lines.append(f"- {'✅已验证' if h['status'] == 'verified' else '🚫证伪'} {h.get('name')}: "
                             f"次5日胜率 {h['win_rate']:.0%} "
                             f"(vs 基准差 {h.get('vs_base_pp'):+.1f}pp · n={h['n']} · {h.get('updated', '')})")
        if _a_hidden > 0:
            lines.append(f"- ……另有 {_a_hidden} 条 A_ 因子假设已验证，见 configs/hypotheses.json")
        # WF 未通过验证的假设：证据展示但明确标注未确认（防 pooled 小样本被当真理）；
        # 只展示 |vs_base_pp| 前 8 条（proposed 增长无上界，防刷屏挤占预算），其余注明条数
        _wf = [h for h in hyp.values()
               if h.get("status") == "proposed" and h.get("n") and h.get("win_rate") is not None]
        _wf.sort(key=lambda h: -abs(h.get("vs_base_pp") or 0))
        for h in _wf[:8]:
            wf = h.get("wf") or {}
            oos = wf.get("oos_diff_pp")
            oos_txt = f"，OOS {oos:+.0f}pp" if oos is not None else ""
            lines.append(f"- ⏳未过WF {h.get('name')}: pooled 胜率 {h['win_rate']:.0%}"
                         f"（{h.get('vs_base_pp', 0):+.1f}pp · n={h['n']}{oos_txt}）"
                         "——证据仅供参考，勿当已验证结论")
        if len(_wf) > 8:
            lines.append(f"- ……另有 {len(_wf) - 8} 条未过WF假设，见 configs/hypotheses.json")
        return ("\n【事件研究假设（近1年×60样本，walk-forward 纪律；"
                "仅 ✅/🚫 过滚动验证，⏳ 证据未确认）】\n"
                + "\n".join(lines)) if lines else ""
    except (OSError, ValueError):
        return ""


def _parse_cn_ts(s: str):
    """ISO 时间容错解析：无时区视为北京（自采快照/窗口边界均为 +08:00 口径）。"""
    from datetime import timezone as _tz
    from datetime import timedelta as _td

    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_tz(_td(hours=8)))
    return d


def _min_rows_in(code: str, start_iso: str, end_iso: str,
                 snap_dir: Path | None = None) -> list:
    """logs/min_snapshots/ 持仓分钟快照行（ts/px 字段），窗口闭区间内按时间升序。
    桥断期间备胎源（TdxAiData 自采）若在跑仍能回放；文件缺失/坏行静默跳过。"""
    start, end = _parse_cn_ts(start_iso), _parse_cn_ts(end_iso)
    if not start or not end:
        return []
    dirp = snap_dir or (ROOT / "logs" / "min_snapshots")
    try:
        files = sorted(dirp.glob("*.jsonl"))[-2:]  # 当日 + 昨日兜底
    except OSError:
        return []
    out: list = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("code") != code or not r.get("px"):
                continue
            ts = _parse_cn_ts(str(r.get("ts") or ""))
            if ts and start <= ts <= end:
                out.append({"ts": ts, "px": float(r["px"])})
    out.sort(key=lambda r: r["ts"])
    return out


def build_gap_note(start_iso: str, end_iso: str, holdings: list,
                   snap_dir: Path | None = None) -> str:
    """断线窗口补跑提示（系统注入）：桥中断恢复后立即补跑时，告知模型本次
    分析覆盖了断线窗口，并附持仓窗口内的自采分钟轨迹（有则回放、无则明说，
    不臆造行情）。holdings: [{"code","name"}]；空持仓返回空串（无决策标的）。

    纪律注入（保守默认）：窗口跳变无法解释 → watch 等量价确认，禁止凭
    跳变追买/恐慌杀跌；闸门与平时一致。
    """
    if not holdings:
        return ""
    start, end = _parse_cn_ts(start_iso), _parse_cn_ts(end_iso)
    if not start or not end:
        return ""
    span = f"{(end - start).total_seconds() / 60:.0f}"
    rows_txt = f"{start:%m-%d %H:%M}（北京）中断 → {end:%H:%M} 恢复，约 {span} 分钟"
    lines = [f"【断线窗口提示（系统注入）】行情桥于 {rows_txt}，本次为恢复补跑分析。",
             "以下为持仓在窗口内的自采分钟记录（备胎源；桥中断期间可能缺失）："]
    for h in holdings:
        code, name = str(h.get("code") or ""), str(h.get("name") or "")
        if not code:
            continue
        rows = _min_rows_in(code, start_iso, end_iso, snap_dir)
        if not rows:
            lines.append(f"- {name}({code})：无窗口内记录（断线期间采样中断，行情无法回放）")
            continue
        first, last = rows[0], rows[-1]
        pct = (last["px"] / first["px"] - 1) * 100 if first["px"] else 0.0
        if len(rows) == 1:
            lines.append(f"- {name}({code})：窗口内记录 1 条"
                         f"（{last['ts']:%H:%M} ¥{last['px']:.2f}）")
        else:
            hi = max(r["px"] for r in rows)
            lo = min(r["px"] for r in rows)
            lines.append(f"- {name}({code})：窗口内记录 {len(rows)} 条："
                         f"{first['ts']:%H:%M} ¥{first['px']:.2f} → "
                         f"{last['ts']:%H:%M} ¥{last['px']:.2f}"
                         f"（{pct:+.2f}%），高 ¥{hi:.2f} / 低 ¥{lo:.2f}")
    lines.append("恢复快照为当前唯一事实基准。若现价相对窗口记录跳变且无法解释"
                 "→ 挂 watch 等量价确认再动，禁止仅凭窗口跳变追买或恐慌杀跌；"
                 "T+1/涨跌停/分账额度/杠杆闸门与平时一致。")
    return "\n".join(lines)


def load_news_brief(max_age_min: int = 180) -> str:
    """『新闻分子』（新闻主编 latest.json → text）注入交易提示词。
    新鲜度闸门：分子缺失或 age>max_age_min 分钟 → 返回带滞后标注的空壳
    （宁旧勿堵：交易侧拿旧分子也强于没有，但要明示滞后，风控决策不依赖它）。"""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    p = ROOT / "data" / "news_brief" / "latest.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(d, dict) or not d.get("text"):
        return ""
    age_min = None
    try:
        ts = _dt.fromisoformat(str(d.get("ts")))
        age_min = (_dt.now(_tz(_td(hours=8))) - ts).total_seconds() / 60
    except (ValueError, TypeError):
        pass
    text = str(d.get("text") or "")
    if age_min is not None and age_min > max_age_min:
        return (f"（注意：以下新闻分子生成于 {age_min:.0f} 分钟前，已滞后——"
                "风控决策以盘面与持仓现状为准，本分子仅作背景参考）\n" + text)
    return text
