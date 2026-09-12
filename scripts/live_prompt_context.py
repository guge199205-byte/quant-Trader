#!/usr/bin/env python3
"""live_hourly_analysis 的上下文/解析纯函数（P1-2 拆分，行为不变）。
仅依赖标准库与同为纯标准库的同级 `llm_json`（解析抽取共用）；ROOT 与主模块同源（仓库根）。"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from llm_json import extract_json

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
                           "intent": r.get("intent_volume"),
                           "remain": int(r.get("remaining") or 0)})
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
            if e.get("remain"):
                # 部分成交：vol≠intent 是「只成交了一部分」，不是手数取整（2026-09-12
                # 审查 LOW-3）——成交行带 remaining 时不许归因到「手数合规后实际」
                note = f"（部分成交，剩余 {int(e['remain'])} 股未成交）"
            elif e.get("intent") and e["vol"] != int(e["intent"]):
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




PCT_GIVEN, PCT_MISSING, PCT_DIRTY = "given", "missing", "dirty"


def parse_pct(x) -> tuple[float, str]:
    """pct 解析 → (值, 状态)：given / missing / dirty 三态（2026-09-12 审查 HIGH-2）。

    - **given**：有效有限数；字符串先规范化（去空白、全角 ％→%、结尾 % 视作百分数，
      `"30%"` → 0.3）——模型确实表达了比例，就尽量读出来而不是当没看见；
    - **missing**：None/空串/N-A——**没表达**，消费点回退默认；
    - **dirty**：给了值但解析不出（`"0.3股"`、`"三成"`、`[0.3]`），**或解析出非有限数**
      （`NaN`/`Infinity`/`1e999`，含字符串形式）——不许等同于 missing：卖出链对
      missing 按清仓执行（模型只说了方向），对 dirty 必须停下 + 留痕（原始值进日志
      与事件）。把「想减 30%」静默执行成清仓是资金方向上不可逆的放大，而旧行为
      （脏值抛 ValueError 炸穿整轮）至少是「少做」。
      非有限值归 dirty 而非 missing（2026-09-12 审查 HIGH-4 终审）：`NaN` 不是
      「没表达」，是「表达了但没法用」——JSON 允许字面量 NaN，模型与手改都能给；
      按 missing 处理会走整仓卖出，方向与 dirty 的停手原则正好相反。
    """
    if x is None:
        return 0.0, PCT_MISSING
    if isinstance(x, str):
        s = x.strip().replace("％", "%")
        if s in ("", "N/A", "n/a"):
            return 0.0, PCT_MISSING
        if s.endswith("%"):
            try:
                v = float(s[:-1]) / 100.0
            except ValueError:
                return 0.0, PCT_DIRTY
        else:
            try:
                v = float(s)
            except ValueError:
                return 0.0, PCT_DIRTY
    else:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0, PCT_DIRTY
    return (v, PCT_GIVEN) if math.isfinite(v) else (0.0, PCT_DIRTY)


def parse_intraday_decision(text: str) -> list | None:
    """LLM 输出 → 盘中决策列表（与 live_llm_trade.parse_decision 同构，共用抽取器）。
    抽取顺序交给 llm_json.extract_json（整段 → ```json 围栏 → 括号平衡块）；
    只解析 decisions 数组；未知 action 忽略。
    返回 [{"action","code","pct","pct_given",...,"pct_bad_raw"?}]——pct_bad_raw 仅在
    「给了 pct 但解析不出」时存在（dirty 态标记，见 parse_pct）；
    解析失败返回 None。"""

    def _num(x) -> float | None:
        """价位解析：None/空/N/A → None；非数字 → None（不炸）。"""
        if x is None or x in ("", "N/A"):
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    def _fnum(x, default: float = 0.0) -> float:
        """宽松数字解析（confidence 等辅助字段）：坏了按缺省，不许炸穿整轮。"""
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    def _rows(obj: dict) -> list:
        out = []
        for x in (obj.get("decisions") if isinstance(obj, dict) else None) or []:
            if not isinstance(x, dict):
                continue
            action = str(x.get("action") or "").lower()
            if action not in ("hold", "sell", "buy", "watch"):
                continue
            pct, state = parse_pct(x.get("pct"))
            row = {
                "action": action,
                "code": str(x.get("code") or "").strip(),
                "name": str(x.get("name") or "").strip(),
                "pct": pct,
                # 「模型没给 pct」与「明说 pct=0」必须可区分（2026-09-12 审查 HIGH-1）：
                # 不区分时 watch 规则会把**漏给比例**的条件位也静默丢掉——该挂的止损不挂。
                "pct_given": state == PCT_GIVEN,
                "stop_loss": _num(x.get("stop_loss")),
                "take_profit": _num(x.get("take_profit")),
                "move_stop": _num(x.get("move_stop")),
                "invalidation": str(x.get("invalidation") or ""),
                "confidence": _fnum(x.get("confidence")),
                "risk_amount": _num(x.get("risk_amount")),
                "reason": str(x.get("reason") or ""),
            }
            if state == PCT_DIRTY:
                # 脏值 ≠ 没给（2026-09-12 审查 HIGH-2）：带上原始值供消费点留痕/审计
                row["pct_bad_raw"] = repr(x.get("pct"))[:40]
            out.append(row)
        return out

    # 「首个含有效 decisions 的块」语义：校验器要求至少有一条合法决策，
    # 空数组/全是被忽略 action 的块继续往后找（与旧 _extract 的 `out or None` 等价）
    obj = extract_json(text, want=lambda d: bool(_rows(d)))
    return _rows(obj) if obj is not None else None


def sell_fraction(d: dict) -> float:
    """卖出决策的执行比例口径（盘中/09:35 两条执行链共用，2026-09-12 P0-5）。

    - 缺 pct（pct_given=False，且无 pct_bad_raw）按**清仓**处理：旧代码
      `min(max(d["pct"],0),1)` 在 pct 缺失时算出 0 股 → 打一行「无合法可卖量」就
      跳过——模型明说「卖出」、只是漏了比例，结果静默不下单。与哨兵侧「没给 pct
      → 按全仓挂」同口径（漏挂的防守位当日无人执行）。
    - **脏 pct（pct_bad_raw 存在）返回 0**（2026-09-12 审查 HIGH-2）：模型给了值但
      没解析出来（如 `"0.3股"`）——把「想减 30%」执行成清仓是不可逆的方向放大。
      调用点必须先停下（print 原始值 + 落 pct_unparsed 事件）再跳过；这里的 0 是
      兜底：调用点万一漏检，宁可少卖（下一轮决策与哨兵兜住），不可多卖。
    - 明说 `pct=0` 仍按 0（不卖）；没带 pct_given 键的旧调用点（测试桩/手工构造
      决策）按「已给」处理，保持原语义。
    - **读不出的值一律 0**（与 dirty 同侧，2026-09-12 终审）：解析异常或非有限数
      （NaN/Inf）走 0，不再回退清仓——`NaN` 恰恰是「模型给了值但没法用」，
      回退 1.0 会把脏值放大成整仓卖出，方向与 dirty 停手原则相反。
    """
    if d.get("pct_bad_raw"):
        return 0.0
    if not d.get("pct_given", True):
        return 1.0
    try:
        v = float(d.get("pct") or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return min(max(v, 0.0), 1.0)


LEDGER_COST_NOTE = (
    "口径说明：**成本列 = 你名下分账账本**（你自己的加权平均买入成本，加仓加权、"
    "减仓不动余仓成本），该列盈亏/% 均按此成本计算；数量与可卖量 = 桥账户实时口径，"
    "执行（可卖量/手数）一律以桥为准。两本账在成交回报到账前可能短暂不一致"
    "（账本数量滞后于桥），属正常对账窗口。成本列带 `*` = 账本暂无该票记录、"
    "暂用券商账户混合成本（可能含历史仓位，仅供参照，勿据此算盈亏）。")


def ledger_cost_rows(rows: list[dict], positions: dict) -> list[dict]:
    """持仓行 → 提示词展示副本：成本/浮盈改**分账账本**口径（2026-09-12 P1-1）。

    背景：桥 account 的 cost_price 是券商**共享账户混合成本**（历史仓位、别的来源
    都算在同一个账户里），照抄给 LLM 会把口径差当成盈亏——glm 曾把实际浮亏 −2.8%
    讲成 +83% 并据此决策。账本成本是本策略自己的加权平均买入价（live_ledger.
    record_buy/record_sell），才是"我这笔仓位赚没赚"的正确基准。

    边界（计划书 P1-1）：
    - **只改展示**：返回新 dict，绝不改入参行——`build_rows` 的 `pnl_pct` 还被波动
      触发基线、`compute_forced_trims`、执行路径 holdings 消费，交易判定一律不动。
    - 账本无该票（或 cost_price ≤0 的脏记录）→ 回退桥值，`cost_src="bridge"`，
      渲染端标 `*`（见 LEDGER_COST_NOTE）。
    - 数量/可卖量仍用桥值；`pnl`（行里若有此键）按「账本成本 × 桥数量」重算，
      与口径说明句一致。

    positions = {code: {"volume","cost_price",...}}（live_ledger 的 agent positions）。
    类型坏值（positions 非 dict、cost_price 非数）一律走回退分支，不许炸穿提示词。
    """
    if not isinstance(positions, dict):
        positions = {}
    out = []
    for r in rows:
        row = dict(r)
        pos = positions.get(r.get("code"))
        try:
            lc = float((pos or {}).get("cost_price") or 0)
        except (TypeError, ValueError, AttributeError):
            lc = 0.0
        # 非有限成本（NaN/Inf）必须走回退：`inf > 0` 为真 → 成本列渲染 ¥inf、
        # 盈亏渲染 +nan%，模型照着 nan% 决策（2026-09-12 审查 LOW）。
        if math.isfinite(lc) and lc > 0:
            price = float(row.get("price") or 0)
            row["cost"] = round(lc, 2)
            if "pnl" in row:
                row["pnl"] = round((price - lc) * float(row.get("volume") or 0), 2)
            row["pnl_pct"] = round((price - lc) / lc * 100, 2)
            row["cost_src"] = "ledger"
        else:
            row["cost_src"] = "bridge"      # 保持桥值原样，仅标注来源
        out.append(row)
    return out


def degraded_summary(mode_name: str, rows: list[dict], positions: dict) -> str:
    """LLM 降级时的对话流数据摘要（未调用 API 时给 UI 兜底展示）。

    口径与提示词持仓表一致（2026-09-12 审查 LOW）：成本/盈亏走分账账本，`*` = 账本
    无该票回退桥值——同一轮里提示词是账本口径、摘要却打桥混合成本，前后自相矛盾。
    """
    lines = [f"（{mode_name} LLM 分析暂不可用，附实时数据；"
             f"成本/盈亏 = 你名下分账账本口径，`*` = 账本无记录回退桥值）"]
    for r in ledger_cost_rows(rows, positions):
        mark = "" if r["cost_src"] == "ledger" else "*"
        lines.append(f"- {r['name']} {r['code']}: 现价 ¥{r['price']} / 成本 ¥{r['cost']}{mark} "
                     f"/ {r['volume']}股 / 盈亏 ¥{r['pnl']:+,.0f}({r['pnl_pct']:+.2f}%)")
    return "\n".join(lines)


def _fresh_price(rec, now, max_age_min: int) -> tuple[float | None, float | None, bool]:
    """单条 L2 采集记录 → (现价, 新鲜度分钟, 是否因过期被剔除)。

    缺价/无时间戳 → (None, None, False)（静默跳过，不计入过期数）；
    有价有时间戳但超时 → (None, age, True)。绝不臆造价格。
    build_pool_quote_block 与 filter_affordable 共用这一处口径——两处各读各的，
    会出现「提示词里给了价、却被资金筛掉了」这类不一致。
    """
    if not isinstance(rec, dict):
        return None, None, False
    try:
        price = float(rec.get("now_price") or 0)
    except (TypeError, ValueError):
        return None, None, False
    if price <= 0:
        return None, None, False
    ts = _parse_cn_ts(str(rec.get("ts") or ""))
    if ts is None:
        return None, None, False
    age = (now - ts).total_seconds() / 60
    if age > max_age_min:
        return None, age, True
    return price, age, False


def budget_filter_note(pct: float) -> str:
    """候选池已按资金量裁剪的提示词说明（09:35 主入口与整点轮共用同一句，防口径漂移）。"""
    return (f"候选池已按你的资金量剔除买不起的标的（单票预算 = 剩余额度×{pct:.0%}；"
            "最小一手 100 股、科创板 200 股都超预算的票不会出现在表里），"
            "表里没有的代码不要报买入。")


def filter_affordable(pool: list, budget: float, max_age_min: int = 45,
                      path: Path | None = None, now=None) -> tuple[list, list]:
    """按该 agent 的单票预算裁候选池：买不起的整行**不推给模型**。

    budget = 单票预算（剩余额度 × 单票比例，且不超虚拟现金），由调用方按 agent 算。
    判据与 compute_order **同构**（不是"最小一手 ≤ 预算"的近似）：先按预算取整到
    可申报量（主板/创业板 100 股整数倍、科创板/北交所抬到起报量），再判
    raw_vol ≤ 0 或 raw_vol×价 > 预算×1.02（限价买按现价+1% 报，见 MIN_LOT_SLACK）。
    近似写法会在主板留出 2% 的漂移窗口（价 ∈ (预算/100, 预算×1.02/100]：提示词保留、
    下单 raw_vol 归零被拒）——2026-09-09 code review 实录，故改为同构。

    取不到实时价 / 行情过期的标的**保留**（fail-open）：没价不等于买不起，
    下单前 compute_order 还会按最小申报量再拦一次；这里宁可少筛，不可误删。
    返回 (保留的候选, 剔除的 [(code, name, 最小可买金额, 最小可买股数)])。
    """
    from ashare_rules import (MIN_LOT_SLACK, min_buy_cost, min_buy_qty,
                              round_buy_qty)

    src = path or (ROOT / "data" / "l2_factors_live.json")
    try:
        factors = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(pool or []), []
    if not isinstance(factors, dict):
        return list(pool or []), []
    now = now or datetime.now().astimezone()
    budget = float(budget or 0)
    kept, dropped = [], []
    for p in pool or []:
        code = str(p.get("code") or "").strip()
        price, _age, _stale = _fresh_price(factors.get(code), now, max_age_min)
        if price is None:
            kept.append(p)          # 没价/过期 → 无法判资金，放行给下单闸门兜
            continue
        raw_vol = round_buy_qty(code, int(budget / price)) if budget > 0 else 0
        if raw_vol <= 0 or raw_vol * price > budget * MIN_LOT_SLACK:
            dropped.append((code, str(p.get("name") or ""), min_buy_cost(code, price),
                            min_buy_qty(code)))
            continue
        kept.append(p)
    return kept, dropped


def build_pool_quote_block(pool: list, max_age_min: int = 45,
                           path: Path | None = None, now=None) -> str:
    """候选池实时行情（桥口径，live_l2_capture 每 5 分钟采集）→ 注入分析提示词。

    背景（2026-09-08 上线）：候选池此前只注入 rank/score/行业，**没有现价**——
    模型无法给新标的定价，只能输出 hold，或把买入意图写成 watch（哨兵只执行卖出），
    实盘成交流水因此 10 卖 1 买。这里把已采集的池内实时价回灌，让"是否买入"
    变成可判断的问题。数据缺失/过期的标的整行剔除，绝不臆造价格。

    max_age_min=45：盘中采集节奏 5 分钟，午休段（11:30 收盘 → 12:00 轮）最长空档
    30 分钟仍可用（块首标注实际新鲜度）；隔夜/盘前一律剔除——盘前要定价请用
    quantdb 日K，别拿昨天的盘中快照当现价。
    """
    if not pool:
        return ""
    src = path or (ROOT / "data" / "l2_factors_live.json")
    try:
        factors = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(factors, dict):
        return ""
    now = now or datetime.now().astimezone()
    lines, stale_n, aged = [], 0, None
    for p in pool:
        code = str(p.get("code") or "").strip()
        rec = factors.get(code) if code else None
        price, age, stale = _fresh_price(rec, now, max_age_min)
        if stale:
            stale_n += 1
            continue
        if price is None:
            continue
        aged = age if aged is None else min(aged, age)
        pre = rec.get("pre_close")
        chg = ""
        try:
            if pre and float(pre) > 0:
                chg = f"（{(price / float(pre) - 1) * 100:+.2f}%）"
        except (TypeError, ValueError):
            chg = ""
        sig = rec.get("signal_score")
        sig_txt = f" · 信号分 {sig:.1f}" if isinstance(sig, (int, float)) else ""
        nm = str(p.get("name") or "")
        lines.append(f"- {nm} {code} 现价 ¥{price:.2f}{chg}{sig_txt}")
    if not lines:
        return ""
    head = ("【候选池实时行情（桥口径，系统采集，"
            + (f"最新 {aged:.0f} 分钟前" if aged is not None else "新鲜度未知") + "）】")
    tail = ("（这是可直接下单的定价依据：买入按现价+1%限价撮合，"
            "不必因『查不到价』而放弃；未列出的标的本轮无实时价，不要凭记忆报价）")
    if stale_n:
        tail += f"（另有 {stale_n} 只池内标的行情已过期，已剔除）"
    return head + "\n" + "\n".join(lines) + "\n" + tail


def load_decision_scorecard(agent: str, min_n: int = 6) -> str:
    """该 agent 的决策远期记分卡（logs/decision_scorecard.json）→ 注入本轮分析。

    口径：T+1 开盘入场、全市场等权基准（见 scripts/decision_track.py）。
    只陈述事实，不替模型下结论——让模型自己看到"我在什么模式下亏钱"。
    样本不足（n<min_n）的周期不展示，防小样本被当规律。"""
    try:
        sc = json.loads((ROOT / "logs" / "decision_scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    blk = ((sc.get("agents") or {}).get(agent) or {})
    if not blk:
        return ""

    def _line(label: str, sub: dict, invert: bool = False) -> str | None:
        parts = []
        for h in (1, 5):
            s = sub.get(f"t{h}") or {}
            if not s.get("n") or s["n"] < min_n:
                continue
            exc = (f" · 超额 {s['avg_excess']:+.1%}"
                   if s.get("avg_excess") is not None else "")
            parts.append(f"T+{h} n={s['n']} 胜率 {s['win_rate']:.0%}{exc}")
        if not parts:
            return None
        tail = "（正数=卖出后下跌，即卖对）" if invert else ""
        return f"- {label}{tail}：" + " · ".join(parts)

    lines = []
    for kind, label, inv in (("bullish", "看多意向", False),
                             ("position", "持仓管理", False),
                             ("sell", "卖出决策", True)):
        sub = blk.get(kind) or {}
        ln = _line(label, sub, inv)
        if ln:
            lines.append(ln)
    # 模式归因：T+5 超额最差/最好的标签各一条（各需 n≥5，避免单笔噪声）
    tags = ((blk.get("bullish") or {}).get("by_tag_t5") or {})
    ranked = [ (t, s) for t, s in tags.items()
               if s.get("n", 0) >= 5 and s.get("avg_excess") is not None ]
    ranked.sort(key=lambda kv: kv[1]["avg_excess"])
    for t, s in (ranked[:1] + ranked[-1:] if len(ranked) > 1 else ranked[:1]):
        lines.append(f"- 模式[{t}]：T+5 n={s['n']} 胜率 {s['win_rate']:.0%} "
                     f"超额 {s['avg_excess']:+.1%}")
    if not lines:
        return ""
    return ("\n【你的决策记分卡（系统自动统计 · T+1 开盘入场口径 · 基准=全市场等权）】\n"
            + "\n".join(lines)
            + "\n（样本 <30 时统计不显著，仅作行为参考；当轮结论仍以现场证据为准）")


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
        # —— 🚫 已证伪清单（前置，最高优先级）：防止重复使用已被数据否掉的认知 ——
        # 对标 deepseek-harness-quant 的「证伪知识库」：开工前先看"哪些路已经堵死"，
        # 否则复盘每轮都会重新提出同类想法，模型也会拿它当买入理由。
        _falsified = [h for h in _v if h.get("status") == "contradicted"]
        _verified = [h for h in _v if h.get("status") != "contradicted"]
        if _falsified:
            _falsified.sort(key=lambda h: abs(h.get("vs_base_pp") or 0), reverse=True)
            lines.append("🚫 已证伪（实测反向，禁止作为买入/看多理由；可作规避、减仓参考）：")
            for h in _falsified[:10]:
                if h.get("win_rate") is None:
                    lines.append(f"  - {h.get('name')}")
                else:
                    lines.append(f"  - {h.get('name')}: 胜率 {h['win_rate']:.0%} "
                                 f"(vs 基准 {h.get('vs_base_pp'):+.1f}pp · n={h['n']})")
            if len(_falsified) > 10:
                lines.append(f"  - ……另有 {len(_falsified) - 10} 条已证伪，见 configs/hypotheses.json")
        for h in _verified:
            if h.get("win_rate") is None:
                lines.append(f"- ✅已验证 {h.get('name')}")
                continue
            if id(h) in _a_show or str(h.get("source")) != "factor_screen":
                lines.append(f"- ✅已验证 {h.get('name')}: "
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


def industry_caution_block(held_codes=None, pool_codes=None, names=None,
                           max_items: int = 10, path=None) -> str:
    """【行业整体劣化】：赛道级软提示（**不硬拦**）→ 注入交易提示词。

    用户口径（2026-09-11）：「这些行业也要谨慎点」——某行业在长期排除清单里的
    占比远高于全市场基线时，个股自己没踩雷，也可能是整条赛道在沉（地产链、
    光伏、酿酒……）。只提示不禁买：行业是统计口径，一刀切会误伤同行业里
    没问题的公司；硬拦仍只认个股判据（symbol_policy + risk_list 闸门）。

    榜单来自 scripts/industry_risk.py 每日落盘的 data/industry_risk.json
    （与清单同源，阈值见 configs/live_symbols.json 的 risk 段）；
    产物缺失/过期 → ""（fail-open，不阻塞提示词构建）。
    """
    try:
        from industry_risk import load_doc

        doc = load_doc(path)
    except Exception:  # noqa: BLE001 提示词少一段不阻塞本轮
        return ""
    items = doc.get("items") or []
    warn = doc.get("warn_codes") or {}
    if not items or not isinstance(warn, dict):
        return ""
    nm = names or {}
    stat = {it["industry"]: it for it in items}

    def _hit(code) -> tuple:
        c6 = str(code or "").split(".")[0]
        ind = warn.get(c6)
        if not ind:
            return "", ""
        it = stat.get(ind) or {}
        return ind, f"{it.get('n')}/{it.get('total')} 只被剔除"

    def _name(code) -> str:
        c6 = str(code or "").split(".")[0]
        return nm.get(str(code)) or nm.get(c6) or ""

    lines = ["- 行业风险榜（剔除率 = 该行业被长期排除清单剔除数 / 行业股票总数）："
             + "、".join(f"{it['industry']} {it['n']}/{it['total']}（{it['rate']:.0%}）"
                         for it in items)]
    hit_lines = []
    for code in pool_codes or []:
        ind, s = _hit(code)
        if ind:
            hit_lines.append(f"- 候选池命中：{code} {_name(code)} 属「{ind}」（该行业 {s}）"
                             "——赛道整体在下沉，买入理由要更强，别只看个股分数")
    for code in held_codes or []:
        ind, s = _hit(code)
        if ind:
            hit_lines.append(f"- 持仓命中：{code} {_name(code)} 属「{ind}」（该行业 {s}）"
                             "——评估是否降低赛道暴露，但不要只凭行业标签卖")
    body = hit_lines[:max_items]
    if len(hit_lines) > len(body):
        body.append(f"（另有 {len(hit_lines) - len(body)} 只命中未列）")
    return "\n".join(
        ["【行业整体劣化（赛道级提示，非硬拦——个股干净也可能被赛道拖着走）】",
         *lines, *body])


def risk_warning_block(held_codes=None, pool_codes=None, names=None,
                       max_items: int = 10, path=None) -> str:
    """【事件风险警示】：持仓/候选池里命中事件风险清单的标的（解禁窗口内 / 近期负面新闻）。

    用户口径（2026-09-08）：「解禁这种就是要告警跑的、负面新闻股票也要避坑掉，
    模型有的话需要毙掉」——持仓命中要评估退出（卖出不受闸门限制），候选命中禁止买入。
    闸门（symbol_policy + risk_list）已硬拦买入，这里让模型**先知道**，
    省一轮"提了买、被毙掉"的无效决策；持仓侧则补上闸门管不到的退出提示。

    2026-09-11 增加【监管关注】软段：问询函/监管函/警示函等只提醒不禁买
    （用户口径「监管的可以提醒，里面有因子，不去拿时[再]黑名单」）——
    这类事件的信息含量大于即期风险，模型当因子自评，不进买入闸门。
    （行业级赛道提示是**独立**一段：见 industry_caution_block。）

    清单缺失 → 返回 ""（fail-open，不阻塞提示词构建）。
    """
    try:
        from risk_list import load_risk, load_watch

        risk = load_risk(path)
        watch = load_watch(path)
    except Exception:  # noqa: BLE001
        return ""
    if not risk and not watch:
        return ""
    nm = names or {}
    lines: list = []

    def _code6(c) -> str:
        return str(c or "").split(".")[0]

    def _name(code) -> str:
        return nm.get(code) or nm.get(_code6(code)) or ""

    for code in held_codes or []:
        it = risk.get(_code6(code))
        if it:
            lines.append(f"- {code} {_name(code)}：{it.get('reason')}"
                         " —— 持仓命中：优先评估减仓/退出，禁止加仓")
    for code in pool_codes or []:
        it = risk.get(_code6(code))
        if it:
            lines.append(f"- {code} {_name(code)}：{it.get('reason')}"
                         " —— 候选命中：禁止买入（闸门硬拦）")
    wlines: list = []
    for code in list(held_codes or []) + list(pool_codes or []):
        it = watch.get(_code6(code))
        if it:
            wlines.append(f"- {code} {_name(code)}：{it.get('reason')}")
    if not lines and not wlines:
        return ""
    out = [""]
    if lines:
        out += ["【事件风险警示（系统风险清单，硬约束）】", *lines[:max_items],
                "（持仓可卖、不可加仓；未持仓一律不许买——闸门会直接毙掉。）"]
    if wlines:
        out += ["【监管关注（只提醒不禁买——当因子自评，不做买入依据）】",
                *wlines[:max_items]]
    return "\n".join(out)


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
