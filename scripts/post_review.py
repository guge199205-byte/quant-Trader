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
from trading_cal import is_trading_day, why_not  # noqa: E402


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
            # errors="replace"：含中文追加流水可能截断在多字节字符中间，严格解码会
            # 整文件抛 UnicodeDecodeError（不是 OSError）→ 当日事实整段丢失（批 10）
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("agent") != agent or r.get("error") or not r.get("side"):
                continue
            if r.get("pending") or r.get("untracked"):
                # 未确认行不许算成交：部分成交时同一单会写两行（fill 行 + pending
                # 行 volume=整单量），不过滤会把 100 股实际成交记成 400（2026-09-12
                # 审查 MEDIUM-1）；纯 pending 行（无成交回报）同样不是成交。
                # untracked（桥缺委托号）更没有成交确认，同样剔除。
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
        rows = []
        try:
            # errors="replace"：模型对话流是含中文的追加日志（批 10 同一文件族），
            # 撕裂的多字节字符会让严格解码整文件抛 UnicodeDecodeError；逐行 try 则
            # 一行坏 JSON 只丢那一行（旧实现整表推导式 → 摘要整段静默变空，批 13）。
            for l in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                if not l.strip():
                    continue
                try:
                    r = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
        except OSError:
            rows = []
        for r in rows[-12:]:
            for m in (r.get("new_messages") or []):
                if str(m.get("role")) in ("assistant", "ai"):
                    c = str(m.get("content") or "").replace("\n", " ")
                    lines.append(f"- {r.get('timestamp', '')[:16]} {c[:360]}")
                    break
    else:
        lines.append("【当日分析轮次摘要】无日志")

    # 3) 当日净值轨迹（逐行容错：一行坏不许让当日净值整段消失，批 13）
    eq = []
    try:
        for l in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            if r.get("agent") == agent and str(r.get("date")) == date and r.get("value") is not None:
                eq.append(r["value"])
    except OSError:
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
    # 当前条件位（让复盘能自审止损/止盈的放宽与收紧）
    try:
        _w = (json.loads((ROOT / "data" / "live_watch.json").read_text(encoding="utf-8"))
              .get(agent) or [])
        if _w:
            lines.append("【当前条件位】")
            lines += [f"- {x.get('code')} 止损{x.get('stop_loss')}/止盈{x.get('take_profit')}"
                      f"/触发减{x.get('pct', 1.0):.0%}" for x in _w]
    except (OSError, ValueError):
        pass
    # 持仓事件雷达：解禁/质押/回购风险标注（数据缺失时静默跳过）
    try:
        from event_radar import tag_events
        from live_ledger import load_ledger

        _held = sorted({code for rec in (load_ledger().get("agents") or {}).values()
                        for code in (rec.get("positions") or {})})
        _tags = tag_events(_held)
        _ev = [f"{c}: {'；'.join(v)}" for c, v in _tags.items() if v]
        if _ev:
            lines.append("【持仓事件雷达】")
            lines += [f"- {x}" for x in _ev]
    except Exception:  # noqa: BLE001
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


def _pct_from_text(txt: str) -> float:
    """文字里的减仓比例：『减50%』>『半』> 其他（默认全减）。

    2026-09-10 实录：『今日区间…距该位仅0.8%缓冲』被旧正则取到 "8" → pct 8%——
    缓冲比例不是减仓比例，小数里的整数位也不能当比例。故裸百分号前不允许再出现
    数字/小数点/正负号，并兼容全角 ％。

    契约：**永远返回 (0,1] 内的有效比例**——『减0%』/裸『0%』是文字噪声，按「未表达」
    回退全减（2026-09-12 审查 MEDIUM）。返回 0.0 会被 live_price_watch 读成
    「明说 0 = 不表达卖出量」→ 该防守位整条不挂（只留一条 alert=False 的事件），
    预案里的保护价就此消失。"""
    def _clamp(n: int) -> float:
        return min(n / 100, 1.0) or 1.0

    m_cut = _re.search(r"减\s*(\d{1,3})\s*[%％]", txt)
    if m_cut:
        return _clamp(int(m_cut.group(1)))
    if "半" in txt:
        return 0.5
    m_bare = _re.search(r"(?<![\d.+\-])(\d{1,3})\s*[%％]", txt)
    return _clamp(int(m_bare.group(1))) if m_bare else 1.0


_PREV_CLOSE_CACHE: dict = {}


def _bridge_prev_close(code: str) -> float | None:
    """桥日K倒数第二根收盘价（=次日的"昨收"），判方向用；拿不到返回 None。

    进程内缓存：post_review 是一次性进程，日K收盘价当次运行内不变。"""
    if code in _PREV_CLOSE_CACHE:
        return _PREV_CLOSE_CACHE[code]
    val = None
    try:
        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        bars = TdxBridgeBroker().get_klines(code, interval="daily")
        if len(bars) >= 2:
            v = float(bars[-2].get("close") or 0)
            val = v if v > 0 else None
    except Exception:  # noqa: BLE001 桥不可用不阻塞复盘：方向退回文字线索
        val = None
    _PREV_CLOSE_CACHE[code] = val
    return val


def _held_codes(agent: str):
    """该 agent 台账持仓代码集合；账本缺失/没有该 agent → None（调用方不裁剪）。"""
    try:
        from live_ledger import load_ledger

        agents = load_ledger().get("agents") or {}
        if agent not in agents:
            return None
        return set(((agents[agent] or {}).get("positions") or {}).keys())
    except Exception:  # noqa: BLE001
        return None


def _watch_direction(txt: str, price: float, prev: float | None) -> tuple[str, str]:
    """观察位方向 → ("stop_loss"|"take_profit", 依据)。

    1) 文字线索（『涨到/站上/止盈』vs『跌破/失守/止损』）——agent 明说的意图；
       与昨收冲突时（价位挂在收盘价另一侧，一挂就会反向触发）以昨收为准；
    2) 无文字线索 → 按昨收：价位在上方=止盈、下方=止损；
    3) 兜底 stop_loss（与历史行为一致，运行时还有方向自洽护栏兜底）。"""
    from live_price_watch import classify_watch_direction

    kind, kw = classify_watch_direction(txt)
    if prev and prev > 0:
        if kind == "take_profit" and price < prev:
            return "stop_loss", f"文字『{kw}』与昨收 ¥{prev:.2f} 冲突（价位在收盘价下方）→ 改按跌破"
        if kind == "stop_loss" and price > prev:
            return "take_profit", f"文字『{kw}』与昨收 ¥{prev:.2f} 冲突（价位在收盘价上方）→ 改按涨到"
    if kind:
        return kind, f"文字『{kw}』"
    if prev and prev > 0:
        return ("take_profit" if price > prev else "stop_loss"), f"按昨收 ¥{prev:.2f} 判方向"
    return "stop_loss", "无方向线索（默认按跌破）"


def sync_plan_to_watch(agent: str, payload: dict, prev_close=None) -> int:
    """P0：复盘预案/观察 → 分钟哨兵条件位（live_watch.json），agent 整组替换。

    方向（2026-09-10 实录，此前一律写 stop_loss）：
      - plan 显式给了 stop_loss/take_profit 键 → 尊重原键；
      - 否则按 trigger/reason 文字线索 + 昨收判方向（'涨到17.90减50%' → take_profit，
        此前写 stop_loss，现价 17.50 在阈值下方 → 开盘反向假触发）；
      - plan 的 buy 项不进哨兵（哨兵只会卖，挂成 take_profit 会在目标买价卖掉持仓）。
    标的：只给该 agent 台账里的持仓挂卖出条件位——指数/买点闸门挂上也卖不出，
    还白烧触发日志（当日 000001.SH×3、低吸观察价 49.50 都是这么烧的）。
    文本比例：'减50%'→0.5、'半'→0.5，其余默认全减（见 _pct_from_text）。
    返回落盘规则数（0 = 该 agent 条件位被清空）。"""
    from live_price_watch import save_watch_rules

    prev_close = prev_close or _bridge_prev_close
    held = _held_codes(agent)
    dropped: list[str] = []
    rules: list[dict] = []

    def _prev(code: str):
        try:
            return prev_close(code)
        except Exception:  # noqa: BLE001
            return None

    def _arm(code: str, price, txt: str, reason: str, explicit: str | None = None) -> None:
        """挂一条卖出条件位；explicit 为显式键（stop_loss/take_profit）时不再推断。"""
        if not price:
            return
        if held is not None and code not in held:
            dropped.append(code)
            print(f"  ⏭️ 非持仓代码 {code}（{agent}）不挂卖出哨兵：{reason[:40]}")
            return
        if explicit:
            kind, why = explicit, "预案显式键"
        else:
            kind, why = _watch_direction(txt, float(price), _prev(code))
        rules.append({"code": code, kind: float(price), "pct": _pct_from_text(txt),
                      "reason": reason[:80], "_dir": why})

    for it in (payload.get("plan") or []):
        if not isinstance(it, dict) or not it.get("code"):
            continue
        act = str(it.get("action") or "").lower()
        if act not in ("sell", "buy", "watch"):
            continue
        code = str(it["code"])
        reason = str(it.get("reason") or "")
        txt = reason + str(it.get("trigger") or "")
        if act == "buy":
            print(f"  ⏭️ 买入预案 {code} 不进卖出哨兵（哨兵只卖不买）：{reason[:40]}")
            continue
        sl, tp = it.get("stop_loss"), it.get("take_profit")
        if sl is None and tp is None:
            price = _extract_price(txt)
            _arm(code, price, txt, reason)
        elif sl is not None:
            _arm(code, sl, txt, reason, explicit="stop_loss")
        else:
            _arm(code, tp, txt, reason, explicit="take_profit")
    for it in (payload.get("watch") or []):
        if not isinstance(it, dict) or not it.get("code"):
            continue
        code = str(it["code"])
        reason = str(it.get("reason") or "")
        txt = reason + str(it.get("trigger") or "")
        if str(it.get("action") or "sell").lower() == "buy":
            print(f"  ⏭️ 买入观察 {code} 不进卖出哨兵（哨兵只卖不买）：{reason[:40]}")
            continue
        sl, tp = it.get("stop_loss"), it.get("take_profit")
        price = it.get("price") or sl or tp or _extract_price(str(it.get("trigger") or ""))
        explicit = "stop_loss" if sl is not None else ("take_profit" if tp is not None else None)
        _arm(code, price, txt, reason, explicit=explicit)
    decisions = []
    for r in rules:
        why = r.pop("_dir", "")
        decisions.append({"action": "watch", "code": r["code"],
                          "stop_loss": r.get("stop_loss"), "take_profit": r.get("take_profit"),
                          "pct": r["pct"], "reason": r["reason"]})
        if why:
            print(f"  🧭 {r['code']} → "
                  f"{'止盈' if r.get('take_profit') else '止损'}"
                  f" ¥{r.get('take_profit') or r.get('stop_loss')}：{why}")
    n = save_watch_rules(agent, decisions)
    extra = f"（剔除非持仓 {len(dropped)} 条：{','.join(sorted(set(dropped)))}）" if dropped else ""
    print(f"  🔔 预案→哨兵同步 {agent}: {n} 条条件位{extra}")
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


_DESC_KEYS = ("description", "desc", "描述")
_DIR_KEYS = ("direction", "方向")


def register_review_hypotheses(agent: str, date: str, candidates,
                               hyp_path: Path | None = None) -> int:
    """复盘 hypothesis_candidates → 假设库（proposed 待复测），返回新增条数。
    name 取描述键（兼容中英混用：deepseek 系回 "desc"、glm 系回中文"描述"——
    2026-09-10 实录 3 条 v4-flash 候选全是 "desc" 键，静默丢失）；
    字符串候选取全文。key 含 agent 前缀：2026-09-08 实录全局 H_{date}_{i} 让
    先跑完的 agent 占 key，后跑 agent 的候选静默丢失。"""
    path = hyp_path or (ROOT / "configs" / "hypotheses.json")
    try:
        hyps = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return 0
    if not isinstance(hyps, dict):
        return 0
    added = 0
    skipped = 0
    for i, cand in enumerate(candidates or []):
        if isinstance(cand, str):
            txt = cand[:120]
            direction = ""
        elif isinstance(cand, dict):
            desc = next((cand.get(k) for k in _DESC_KEYS if cand.get(k)), "")
            txt = str(desc)[:120]
            direction = str(next((cand.get(k) for k in _DIR_KEYS if cand.get(k)), "") or "")
        else:
            continue
        key = f"H_{date}_{agent}_{i}"
        if key in hyps:
            continue          # 同一 agent+日期重跑：已登记过，不算丢失
        if not txt:
            skipped += 1      # 键名不认识（如 {"why": ...}）→ 说出来，别再静默丢
            continue
        hyps[key] = {"name": txt, "direction": direction,
                     "win_rate": None, "n": None, "updated": date,
                     "status": "proposed", "source": "review"}
        added += 1
    if skipped:
        print(f"  ⚠️ 假设候选 {skipped} 条缺描述键（认得的键：{'/'.join(_DESC_KEYS)}），未登记")
    if added:
        try:
            path.write_text(json.dumps(hyps, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return 0
    return added


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
    # v2：复盘 hypothesis_candidates 自动登记入假设库（proposed 待复测）
    try:
        n_new = register_review_hypotheses(
            agent, date, payload.get("hypothesis_candidates") or [])
        if n_new:
            print(f"✓ 假设登记 {n_new} 条 → configs/hypotheses.json")
    except Exception as exc:  # noqa: BLE001 假设登记失败不阻塞复盘
        print(f"⚠️ 假设登记失败: {exc}")
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
    # 交易日历闸门：法定节假日休市日无交易可复盘，跳过（显式 --date 手动补跑不受限）
    if not args.date and not is_trading_day(now_cn().date()):
        print(f"⏭️ {now_cn():%F %T} 非交易日（{why_not(now_cn().date())}），跳过盘后复盘")
        return 0
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