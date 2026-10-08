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
  4. 校验: 买入走共用闸门 buy_gate（循环熔断 / 单票 ≤ 当日预算比例 / 候选池成员 /
     新开仓单轮+当日双上限）、不追涨停、100 股整数倍、账户现金兜底；
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
import subprocess
import sys
import time
import traceback
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
    agent_virtual_cash,
    find_holder,
    load_ledger,
    position_cost,
    record_sell,
    sane_fill_price,
    save_ledger,
)
from live_fills import (inflight_codes, note_pct_unparsed, reconcile,  # noqa: E402
                        round_sell_qty, sell_limit, settle_place_fill, wait_fill)
# 执行损耗 TCA 口径（三段价字段的唯一出处；纯算术、不抛异常）
import exec_cost  # noqa: E402
from ashare_rules import at_limit_down, board_of, in_continuous_auction  # noqa: E402
from llm_json import extract_json  # noqa: E402
from live_prompt_context import (PCT_DIRTY, PCT_GIVEN, parse_pct,  # noqa: E402
                                 sell_fraction)
from live_quotes import (klines as lq_klines,  # noqa: E402
                         klines_batch as lq_klines_batch,
                         quote as lq_quote)
# 影子代价账：被拦下的买入意图落池，事后按远期超额给每条规则定价。
# 纯观测层（写入失败只留一行 stderr，绝不抛），见 scripts/ghost_ledger.py。
import gate_rules  # noqa: E402
import ghost_ledger  # noqa: E402

# 杠杆硬约束（与 live_hourly_analysis 同口径）
LEVERAGE_MAX = 1.5

CN_TZ = ZoneInfo("Asia/Shanghai")
PICKS_JSON = ROOT / ".." / "projects" / "quantmind" / "data" / "reports" / "stock_picks"
PER_STOCK_PCT = 0.2   # 单票买入 ≤ 剩余额度 20%
MAX_NEW_BUYS = 3      # 新开仓上限：单轮 + 当日累计（风险预算 max_new_buys 覆盖）
# 单票**累计持仓**上限：同票成本 ≤ 该比例×权益（风险预算 per_stock_pos_pct 覆盖；
# 2026-09-18 机构级——per_stock_pct 只管单笔，同一标的跨轮加仓此前不封顶）
MAX_POS_PCT = 0.25
# 取池多拿只数：入口过滤（ST/黑名单/风险）剔除后按原排序补齐，池子不白白变小
POOL_FILTER_OVERFETCH = 10

# 单实例锁 + 当日执行状态（09:35 主入口与 10:05/11:05 补跑共用）
LOCK_FILE = ROOT / "logs" / "live_llm_trade.lock"
STATE_FILE = ROOT / "logs" / "live_llm_trade_state.json"
# 总闸关闭降级 dry-run 时写进 state.note 的标记（alert_checks 同字面量读取，
# 两侧由 tests/test_alert_checks.py 钉住一致；语义见 _apply_exec_switch）
EXEC_SWITCH_OFF_NOTE = "exec_switch_off"
ACCT_QUERY_ATTEMPTS = 3       # 桥账户查询重试次数（断线/假活）
ACCT_QUERY_SLEEP_SEC = 60     # 重试间隔


def _apply_risk_budget() -> None:
    """启动时读取当日风控档位覆盖常量（风险预算 meta-agent 输出）。

    2026-09-08 前本文件硬编码 1.5/20% 且**没有新开仓上限**——预算定档"防守"
    （1.0/10%/1 只）时，09:35 主入口仍按宽松档下单，风险预算只兑现了一半。
    口径与 live_hourly_analysis 共用 risk_budget_agent.load_limits。
    2026-09-18 起档位不可信时 load_limits 回退买入侧防守参数（fail-safe），
    此处无需额外处理。
    """
    global LEVERAGE_MAX, PER_STOCK_PCT, MAX_NEW_BUYS, MAX_POS_PCT
    try:
        from risk_budget_agent import load_limits

        lim = load_limits()
    except Exception as exc:  # noqa: BLE001  load_limits 自身全路径已兜底；此处防模块级损坏
        print(f"⚠️ 风险预算档位加载失败（{str(exc)[:100]}）"
              "——按模块默认档运行（调用方默认；评审 LOW：不静默）")
        return
    LEVERAGE_MAX = lim.get("leverage_max", LEVERAGE_MAX)
    PER_STOCK_PCT = lim.get("per_stock_pct", PER_STOCK_PCT)
    MAX_NEW_BUYS = lim.get("max_new_buys", MAX_NEW_BUYS)
    MAX_POS_PCT = lim.get("per_stock_pos_pct", MAX_POS_PCT)


_apply_risk_budget()


def _quote_guarded_price(broker, code: str, fp: float) -> tuple[float, bool]:
    """用实时行情护栏校验价格：K 线脏数据/桥回报坏价（偏离实时价>40%）时
    回退实时价并 approx 标记；取不到行情原样返回。"""
    try:
        quote = lq_quote(code, prefer="aidata", broker=broker)
        ref = float((quote or {}).get("close") or 0)
    except Exception:  # noqa: BLE001
        return fp, False
    return sane_fill_price(fp, ref)


# ---------- 候选池 ----------

def load_pool_meta(top: int = 20) -> tuple[list, dict, dict]:
    """候选池 + 大盘方向 + 来源元数据（2026-09-12 P1-3）。

    实盘消费路径（09:35 调仓 / 整点轮）用本函数：提示词与 log.jsonl 都要写明
    「用的是哪一天的哪类池、是否滞后」；池整份缺失/最新一期读不出 → 事件
    （alert，按自然日去重）。L2 采集 / 新闻管线只要代码，继续用 load_pool。
    """
    fetch_top = top + POOL_FILTER_OVERFETCH if top > 0 else 0
    sel = subprocess.run(
        [sys.executable, "scripts/select_from_reports.py", "--source", "picks",
         "--top", str(fetch_top), "--min-side", "HOLD", "--json"],
        capture_output=True, text=True, check=False, cwd=ROOT)
    try:
        pool = json.loads(sel.stdout or "[]")
    except json.JSONDecodeError:
        pool = []
    from symbol_policy import filter_pool, load_policy_with_risk

    pool, blocked = filter_pool(pool or [], load_policy_with_risk())
    if blocked:
        print(f"  ⛔ 候选池剔除 {len(blocked)} 只（标的边界：ST/退市/黑名单/事件风险）："
              + "、".join(f"{r.get('code')} {r.get('name') or ''}" for r, _ in blocked))
    if top > 0:
        pool = pool[:top]
    # 来源元数据（picks_source，与 select_from_reports 子进程同一套判据）：
    # 方向也取自**同一个**池文件——原来是另一个 glob 字典序取最后（可与池子不同源）
    from picks_source import latest_picks, note_missing_pool, pool_meta

    info = latest_picks(PICKS_JSON)
    meta = pool_meta(info)
    if meta["missing"] or meta["unreadable"]:
        note_missing_pool(meta["note"])
    direction = {}
    if info and isinstance(info[2], dict):
        d = info[2].get("market_direction")
        if isinstance(d, dict):
            direction = d
    return pool, direction, meta


def load_pool(top: int = 20) -> tuple[list, dict]:
    """候选池：picks.json → 决策用表格行 + 大盘方向。
    返回 (rows, market_direction)；无池子返回 ([], {})。

    池子入口即剔除 ST/*ST/退市整理/操作员黑名单/事件风险标的
    （symbol_policy.filter_pool，2026-09-11 用户口径「agent 选出来的股票、
    跟踪的股票，不需要 ST 的」）。实盘三条取池路径（09:35 决策 / 整点轮 /
    L2 采集）共用本函数，单点生效；下单闸门仍独立拦一次兜底。
    多取 POOL_FILTER_OVERFETCH 只：剔除的名额由后续排名补上，池子不白白变小。

    需要池来源/新鲜度说明的路径用 load_pool_meta（本函数是它的二值包装）。
    """
    pool, direction, _ = load_pool_meta(top)
    return pool, direction


def pool_rows(pool: list) -> list[str]:
    """候选池 → markdown 表格行（select_from_reports --json 字段：
    rank/code/name/industry/score/fusion/remark）。
    side 标签（历史遗留，写侧恒为 HOLD 占位）不再展示——买卖判断由模型按
    分数+大盘+板块+新闻分子综合给出，不看标签。"""
    rows = []
    for p in pool:
        fusion = p.get("fusion")
        fus = f"{fusion:.3f}" if isinstance(fusion, (int, float)) else "—"
        rows.append(
            f"| {p.get('rank', '—')} | {p.get('code', '')} | {p.get('name', '')} "
            f"| {p.get('industry', '')} | {p.get('score', 0):.3f} "
            f"| {fus} | {p.get('remark', '')} |")
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
                quote = lq_quote(code, prefer="aidata", broker=broker)
                price = float((quote or {}).get("close") or 0)
            except Exception:  # noqa: BLE001
                price = 0
        day_chg = None
        try:
            klines = lq_klines(code, interval="daily", count=2,
                               prefer="aidata", broker=broker)
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
                 direction: dict, quota_remaining: float,
                 pool: list | None = None, extra_context: str = "") -> str:
    """09:35 主入口的提示词。

    extra_context：额外上下文块（P1 影子 A/B 的处理臂：盘面状态+新闻分子，
    与整点轮同源同序——插在【今日大盘方向】后、【候选池】前）。空串（默认）
    时输出与旧行为逐字节一致（tests/test_shadow_context_ab 钉住）。
    """
    from live_prompt_context import budget_filter_note

    lines = [
        f"现在是北京时间 {now_cn():%F %T}（开盘后）。你是 {agent} 的 A股实盘调仓决策模型，"
        f"管理 ¥{AGENT_QUOTA:,.0f} 虚拟额度（已用 ¥{agent_used(load_ledger(), agent):,.0f}，"
        f"剩余 ¥{quota_remaining:,.0f}）。",
        "",
        "【你名下的现有持仓】（成本与盈亏% = 你名下分账账本口径；现价/数量/可卖量 = "
        "桥账户实时口径，可卖量 0 = 今日买入 T+1 不可卖）：",
        "",
        "| 代码 | 名称 | 数量 | 成本 | 现价 | 盈亏% | 今日涨跌% | 可卖量 |",
        "|------|------|------|------|------|-------|-----------|--------|",
    ]
    # 展示副本换账本成本口径（2026-09-12 P1-1）：holding_rows 本体仍是桥口径（执行
    # 路径消费它），这里只改喂 LLM 的这一份；账本无该票 → 桥值 + `*` 标注
    from live_prompt_context import LEDGER_COST_NOTE, ledger_cost_rows

    # 账本读不到/结构坏 → 展示层全部回退桥值（提示词不许炸；与整点轮同口径）
    try:
        _led_pos = (load_ledger().get("agents") or {}).get(agent, {}).get("positions") or {}
    except Exception:  # noqa: BLE001
        _led_pos = {}
    for h in ledger_cost_rows(holdings, _led_pos):
        mark = "" if h["cost_src"] == "ledger" else "*"
        lines.append(
            f"| {h['code']} | {h['name']} | {h['volume']} | {h['cost']}{mark} | {h['price']} "
            f"| {h['pnl_pct']:+.2f}% | {h['day_chg']:+.2f}% | {h['avail']} |")
    lines += ["", LEDGER_COST_NOTE, "", "【今日大盘方向】（最新研究产出）：",
              f"{direction.get('direction', '—')}"
              f"{'（总分 ' + str(direction.get('total_score')) + '/11）' if direction.get('total_score') is not None else ''}",
              ""]
    if extra_context:
        lines += [extra_context, ""]
    if pool_rows:
        lines += ["【候选池】（最新研究池：总分/融合分/备注；池内没有方向标签——"
                  "买卖由你按 分数+大盘+板块+新闻分子 综合判断，HOLD/BUY 侧标签一律不作为依据）",
                  "",
                  "| 排名 | 代码 | 名称 | 行业 | 总分 | 融合分 | 备注 |",
                  "|------|------|------|------|------|--------|------|"]
        lines += pool_rows
        # 池内实时价（桥口径）：候选表只有评分没有价 → 模型无法给新标的定价，
        # 只能全 hold（2026-09-08 整点轮同款修复；09:35 是本系统主入口，更不能缺）
        from live_prompt_context import build_pool_quote_block, risk_warning_block

        qb = build_pool_quote_block(pool or [])
        if qb:
            lines += ["", qb]
    # 事件风险警示（解禁窗口内/近期负面新闻）：持仓提示退出，候选提示别选
    # （闸门已硬拦买入，这里省一轮无效决策；2026-09-08 用户口径）
    # + 行业整体劣化（赛道级软提示，不硬拦；2026-09-11 用户口径）
    try:
        from live_prompt_context import industry_caution_block, risk_warning_block

        _nm = {h["code"]: h.get("name") for h in holdings}
        _nm.update({p.get("code"): p.get("name") for p in (pool or [])})
        _hc = [h["code"] for h in holdings]
        _pc = [p.get("code") for p in (pool or [])]
        rb = risk_warning_block(_hc, _pc, _nm)
        if rb:
            lines += [rb]
        ib = industry_caution_block(_hc, _pc, _nm)
        if ib:
            lines += ["", ib]
    except Exception:  # noqa: BLE001 风险清单/行业榜不可用不阻塞提示词
        pass
    lines += [
        "",
        "【决策规则】",
        "1. 逐只现有持仓判断：hold（继续持有）/ sell（减仓或清仓换股）。"
        "如果现有持股趋势/基本面仍优于候选池，可以全部 hold 不换股。",
        "2. 需要买入时从候选池选：优先分数高、行业顺大盘方向的；"
        "允许换仓（同轮先 sell 再 buy），以信号分数+板块主线+新闻分子综合权衡，不必拘泥原有持仓。",
        f"3. sell 的 pct = 卖出可卖量的比例（0~1）；buy 的 pct = 使用剩余额度的比例"
        f"（每票 ≤{PER_STOCK_PCT:.0%}，当日新开仓 ≤{MAX_NEW_BUYS} 只；超出的会被闸门裁掉）。"
        + budget_filter_note(PER_STOCK_PCT),
        "4. T+1：可卖量 0 的持仓不能卖。ST/*ST/退市整理股与黑名单标的**不可买入**（闸门硬拦）。",
        "5. 输出**严格 JSON**（不要 markdown 代码块、不要额外文字），格式：",
        DECISION_SCHEMA,
    ]
    return "\n".join(lines)


def parse_decision(text: str) -> list | None:
    """LLM 输出 → 调仓决策列表（容忍散文夹 JSON / 围栏；解析失败返回 None）。

    2026-09-12 P0-5：抽取统一到 `llm_json.extract_json`。旧实现只认「整段 / ```json
    围栏」，09-11 09:35 的散文输出直接「解析失败，跳过」→ 当天该 agent 0 买 0 卖；
    同一份文本喂盘中的 parse_intraday_decision 就能取出来。pct 解析统一到
    `live_prompt_context.parse_pct`（三态 given/missing/dirty，与盘中同口径）：
    `"30%"` 规范化成 0.3；脏值（`"0.3股"`）不再是「未表达」，带 `pct_bad_raw`
    标记交给执行段跳过留痕（2026-09-12 审查 HIGH-2）。
    """

    def _rows(d: dict) -> list:
        out = []
        for x in (d.get("decisions") if isinstance(d, dict) else None) or []:
            if not isinstance(x, dict):
                continue
            action = str(x.get("action") or "").lower()
            if action not in ("hold", "sell", "buy"):
                continue
            pct, state = parse_pct(x.get("pct"))
            row = {
                "action": action,
                "code": str(x.get("code") or "").strip(),
                "pct": pct,
                "pct_given": state == PCT_GIVEN,
                "reason": str(x.get("reason") or ""),
            }
            if state == PCT_DIRTY:
                row["pct_bad_raw"] = repr(x.get("pct"))[:40]
            out.append(row)
        return out

    obj = extract_json(text, want=lambda d: bool(_rows(d)))
    return _rows(obj) if obj is not None else None


JSON_ONLY_HINT = ("\n\n【纠正】上一次输出无法解析为决策 JSON（没有 JSON / decisions 为空 / "
                  "格式不符）。请**只输出 JSON 对象本身**（不要 markdown 代码块、不要任何"
                  "解释文字），且 decisions 数组必须逐只列出现有持仓的判断，格式同上面的 schema。")


def _merge_usage(a: dict | None, b: dict | None) -> dict | None:
    """两次 LLM 调用的 token 用量合并（解析重试也是真实开销，必须计入）。"""
    if not a:
        return b
    if not b:
        return a
    out = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            out[k] = int(a.get(k) or 0) + int(b.get(k) or 0)
        except (TypeError, ValueError):
            out[k] = a.get(k) or b.get(k)
    return out


def decide_with_retry(prompt: str, agent: str) -> tuple[list | None, str, dict | None, str]:
    """调 LLM 拿调仓决策；解析失败追加「只输出 JSON」**重试 1 次**（2026-09-12 P0-5）。

    返回 (decisions, 最终原文, 合并 usage, 失败原因)；成功时原因=""。仍失败 decisions=None，
    由调用方留痕（旧行为是只 print 一行 → UI 看不见、事后无法解释「今天为什么零交易」）。
    失败原因三态（2026-09-12 审查 LOW-2）："api_failed"（调用失败，无输出）/
    "empty_output"（返回空串）/ "parse_failed"（有输出但取不出决策）——三者要用的
    排查方向不同，不能都写成「模型输出解析不了」。
    """
    from live_hourly_analysis import call_llm

    content, usage = "", None
    try:
        content, usage = call_llm(prompt, agent)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ [{agent}] LLM 调用失败: {exc}")
        return None, "", None, "api_failed"
    decisions = parse_decision(content)
    if decisions is not None:
        return decisions, content, usage, ""
    if not content:
        return None, "", usage, "empty_output"
    print(f"  ⚠️ [{agent}] 决策解析失败，追加「只输出 JSON」重试一次")
    try:
        content2, usage2 = call_llm(prompt + JSON_ONLY_HINT, agent)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ [{agent}] 解析重试调用失败: {exc}")
        return None, content, usage, "parse_failed"
    usage = _merge_usage(usage, usage2)
    if content2:
        decisions2 = parse_decision(content2)
        if decisions2 is not None:
            return decisions2, content2, usage, ""
    return None, (content2 or content), usage, "parse_failed"


_FAIL_LABEL = {"api_failed": "LLM 调用失败（无输出）", "empty_output": "LLM 返回空响应",
               "parse_failed": "决策解析失败（含重试）"}


def _log_parse_failure(agent: str, prompt: str, content: str, usage: dict | None,
                       reason: str = "parse_failed") -> None:
    """调仓失败留痕：对话流 kind=reason + 事件（2026-09-12 P0-5）。

    旧行为只有 cron stdout 一行——UI 对话 tab 看不见「这个 agent 今天为什么零交易」，
    与「失败必留痕」的既有口径不一致（先例：append_failure_log / daily_report）。

    reason 分叉文案（2026-09-12 审查 LOW-2）：把「网络/接口失败」写成「模型输出
    解析不了」会把运维排查引到错误方向，三种失败必须区分：
      api_failed（调用失败，无输出）/ empty_output（返回空串）/ parse_failed（有输出但取不出决策）。
    """
    text = (content or "").strip()
    if reason == "api_failed":
        head = ("⚠️ 本轮调仓 LLM 调用失败（网络/桥/接口错误，未取得任何输出）：本轮该 agent"
                "未产生任何买卖（零交易）。排查方向在调用链（见 stdout 的调用错误），不是模型输出格式。")
        raw = "（无输出）"
    elif reason == "empty_output":
        head = ("⚠️ 本轮调仓 LLM 返回空响应：本轮该 agent 未产生任何买卖（零交易）。"
                "空响应多为接口异常而非格式问题，故未做「只输出 JSON」重试。")
        raw = "（空输出）"
    else:
        head = ("⚠️ 本轮调仓决策解析失败（已追加「只输出 JSON」重试 1 次）：LLM 输出没有可用的"
                "决策 JSON（无 JSON / decisions 为空 / 格式不符）。本轮该 agent 未产生任何买卖"
                "（零交易）。")
        raw = text[:500] or "（空输出）"
    body = "\n".join([head, "", "原文前 500 字：", "", raw])
    try:
        from live_hourly_analysis import append_log

        append_log(prompt, body, agent, usage, kind=reason)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ [{agent}] 失败留痕写对话流失败: {exc}")
    try:
        from live_fills import record_event

        record_event(reason, agent,
                     f"[{agent}] 09:35 调仓{_FAIL_LABEL.get(reason, '决策失败')} → 本轮零交易；"
                     f"原文前 120 字：{text[:120] or '（无输出）'!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ [{agent}] 失败留痕事件落盘失败: {exc}")


# ---------- 单实例锁 / 当日状态 / 断线重试 ----------

_LOCK_FH: list = []


def _acquire_lock() -> bool:
    """单实例锁：09:35 主入口与 10:05/11:05 补跑不得并发（并发 = 重复下单）。

    没有锁就无法区分「另一班正在跑」与「上一班中途崩了」——补跑判据依赖这个区分。
    """
    import fcntl

    try:
        fh = open(LOCK_FILE, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _LOCK_FH.append(fh)  # 保持引用防 GC 释放锁
    return True


def _read_state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_state(day: str, **kw) -> None:
    """当日执行状态原子落盘（仅 --execute 路径调用；dry-run 不写，免得卡住当天补跑）。

    例外：总闸关闭导致的降级（_apply_exec_switch）会写一条 note 标记——那是
    「当日确定不会执行」的如实记录，ok≠True 不阻塞补跑，只为免掉 10:00 的
    「主入口未收尾」误报。

    字段：day / started_ts / orders_attempted / ok / note。
    orders_attempted 一旦为真不会被后续合并写抹掉（除新一轮启动显式重置）。
    跨日则整条作废：旧一天的 orders_attempted/ok 不得带进新一天（否则开关一关、
    _run 没跑，新一天会顶着一份"今天已下过单"的假状态，10:05 补跑直接被跳过）。
    """
    cur = _read_state()
    if cur.get("day") != day:
        cur = {}
    cur.update({"day": day, **kw})
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:  # 状态写不进去不该掀翻调仓
        print(f"  ⚠️ 执行状态写入失败: {exc}")


def _query_account_with_retry(attempts: int = ACCT_QUERY_ATTEMPTS,
                              sleep: float = ACCT_QUERY_SLEEP_SEC):
    """桥账户查询 + 断线重试 → (broker, acct)。全败抛 BrokerError。

    每轮**新建** broker：桥址自动发现（tdx_bridge._post → bridge_discovery）每实例
    只做一次，复用实例等于放弃第 2/3 次发现机会。判据 = 抛异常 **或** 资产<=0
    （桥假活返回空资产不抛异常，2026-09-04 实录）。
    """
    from agent_tools.brokers.base import BrokerError

    last = "未知"
    for i in range(1, attempts + 1):
        broker = TdxBridgeBroker()
        try:
            acct = broker._account_query()
            asset = float((acct.get("asset") or {}).get("asset") or 0)
            if asset > 0:
                return broker, acct
            last = f"桥返回资产 {asset:,.0f}（断线/假活）"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        print(f"  ⚠️ 桥账户查询第 {i}/{attempts} 次失败：{last}")
        if i < attempts:
            time.sleep(sleep)
    raise BrokerError(f"桥账户查询重试 {attempts} 次仍失败：{last}")


def _apply_exec_switch(args) -> bool:
    """自动执行总开关关掉时把 --execute 降级为 dry-run。返回是否降级。

    本脚本由 cron 固定以 `--execute` 调用（无人值守），若「命令行显式传参」一律
    压过 configs/intraday_exec.json，总闸对这条**买入**路径就形同虚设——哨兵/
    整点轮/强平守护/延期重放都受它管，只有它例外（2026-09-11 盘点）。
    人工非要强跑：加 --force（别写进 crontab）。

    降级时落一条当日状态（note=EXEC_SWITCH_OFF_NOTE）：当日「因总闸关闭而未执行」
    是预期行为，alert_checks 据此不再报「主入口未收尾」（否则开关一关就天天误报）。
    状态里 ok≠True、orders_attempted 为假 → 总闸恢复后当日 catch-up 仍可正常补跑。
    例外（2026-09-11 审查 MEDIUM）：当日**已经真下过单/真失败过**时不许覆盖——
    09:35 跑过并失败（ok=False），10:05 补跑时总闸被关，这条 note 会把那次真失败
    洗成「预期行为」，t1/t2 告警全静默。
    """
    if not args.execute or getattr(args, "force", False):
        return False
    from live_hourly_analysis import intraday_exec_enabled

    if intraday_exec_enabled():
        return False
    args.execute = False
    day = now_cn().date().isoformat()
    cur = _read_state()
    if cur.get("day") == day and (cur.get("orders_attempted") or cur.get("ok") is False):
        print("🔒 自动执行总开关已关，本轮降级为 dry-run；当日已有真执行记录"
              "（下过单/失败过）→ 保留原状态不覆盖（那是今天的告警依据）")
        return True
    _write_state(day, ok=None, note=EXEC_SWITCH_OFF_NOTE)
    print("🔒 自动执行总开关已关（configs/intraday_exec.json enabled=false），"
          "本轮降级为 dry-run：只做决策演练，不下单（人工强跑用 --force）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="模型自主调仓（候选池 + 持仓 → LLM 决策 → 桥执行）")
    ap.add_argument("--execute", action="store_true", help="真下单（默认仅 dry-run 决策演练）")
    ap.add_argument("--force", action="store_true",
                    help="人工显式覆盖自动执行总开关（仅手动使用，勿写入 crontab）")
    ap.add_argument("--agents", default="", help="只跑指定 agent（逗号分隔；默认全部 enabled）")
    ap.add_argument("--top", type=int, default=20, help="候选池上限（默认 20）")
    ap.add_argument("--catch-up", action="store_true",
                    help="当日补跑：今天没成功且一单都没动过才执行（须配合 --execute）")
    ap.add_argument("--shadow-context", action="store_true", dest="shadow_context",
                    help="P1 影子 A/B：同快照对照 现状提示词 vs +盘面/新闻分子，"
                         "只记录不下单（建议 cron 北京 09:40）")
    ap.add_argument("--shadow-force", action="store_true", dest="shadow_force",
                    help="影子模式跳过采样窗口（仅手动冒烟；记录带 forced 标记，周报排除）")
    args = ap.parse_args()

    # P1 影子 A/B 参数校验（只记录不下单；与执行类参数互斥，显式拒绝防误用）
    if args.shadow_force and not args.shadow_context:
        print("⏭️ --shadow-force 只在 --shadow-context 下有意义，拒绝"
              "（防止误以为在冒烟影子、实际走了实盘路径）")
        return 2
    if args.shadow_context:
        if args.execute:
            print("⏭️ --shadow-context 只做 A/B 记录、绝不下单，不能与 --execute 同用")
            return 2
        if args.catch_up:
            print("⏭️ --shadow-context 与 --catch-up 是无意义组合，拒绝")
            return 2

    # 交易日历闸门：法定节假日休市不分析（cron 1-5 覆盖不到法定假日）
    from trading_cal import is_trading_day, why_not

    today = now_cn().date()
    if not is_trading_day(today):
        print(f"⏭️ {now_cn():%F %T} 非交易日（{why_not(today)}），跳过模型自主调仓")
        return 0

    if args.shadow_context:
        try:
            return _run_shadow(args)
        except Exception:  # noqa: BLE001 影子异常也不许裸崩（评审 MEDIUM）
            print("❌ 影子 A/B 异常中断：")
            traceback.print_exc()
            return 1

    if args.catch_up:
        if not args.execute:
            print("⏭️ --catch-up 必须配合 --execute，跳过")
            return 0
        from live_hourly_analysis import in_trading_window

        if not in_trading_window(now_cn()):
            print(f"⏭️ {now_cn():%F %T} 不在 A股交易时段，不补跑")
            return 0

    # 自动执行总开关：cron 恒传 --execute，此闸若不加，总闸对 09:35 买入路径无效
    if _apply_exec_switch(args) and args.catch_up:
        print("⏭️ --catch-up 在总闸关闭时跳过（补跑的唯一目的就是真执行）")
        return 0

    if not _acquire_lock():
        print(f"⏭️ {now_cn():%F %T} 已有另一班在跑（{LOCK_FILE.name}），跳过本轮")
        return 0

    if args.catch_up:
        st = _read_state()
        st = st if st.get("day") == today.isoformat() else {}
        if st.get("ok") is True:
            print(f"⏭️ 当日调仓已成功完成（{st.get('note') or 'ok'}），无需补跑")
            return 0
        if st.get("orders_attempted"):
            print("⏭️ 当日已下过单（orders_attempted），补跑可能重复下单 → 跳过，请人工确认")
            return 0

    mode = "🔴 实盘执行" if args.execute else "🟡 DRY-RUN 决策演练（不下单）"
    print(f"{mode}  {now_cn():%F %T}")
    if not args.execute:
        print("ℹ️  LLM 决策会真实调用（看模型判断质量），仅不下单")

    try:
        return _run(args)
    except Exception:  # noqa: BLE001 未预料异常也要落状态、留全栈（2026-09-09 裸崩无痕）
        print("❌ 调仓执行异常中断：")
        traceback.print_exc()
        if args.execute:
            _write_state(today.isoformat(), ok=False,
                         note=f"crashed: {sys.exc_info()[1]}")
        return 1


def _agent_context(agent: str, ledger: dict, holdings: list, pool: list,
                   direction: dict, pool_note: str, extra_context: str = "",
                   announce: bool = True, ghost_source: str = "") -> dict:
    """单 agent 的决策输入快照 + 提示词（09:35 主入口与 P1 影子 A/B 共用）。

    唯一出处：提示词拼装只在这里（+ build_prompt）；主入口与影子各拼一份必然
    漂移（本项目三次"同规则复制多份"教训）。announce=False 供影子预演 A/B
    两版时免重复打印。my_holdings 的裁剪规则见下（2026-09-08 事故）。
    ghost_source：影子账的 source 戳（含 .dry 后缀表示演练轮）；空串 = 不记
    （影子 A/B 预演走的就是空串——模拟轮事件进生产账会把剔除样本翻倍）。
    """
    # 账本 positions 是 {code: {volume, cost_price, ...}} 字典（live_ledger 内部结构）。
    # 空账本 = 无持仓。2026-09-08 事故：曾有 `if mine else holdings` 兜底把共享桥
    # 账户全量持仓当"你名下"喂给空账本 agent → pro 卖了 flash 的生益电子。
    mine = set((ledger.get("agents") or {}).get(agent, {}).get("positions", {}))
    my_holdings = [h for h in holdings if h["code"] in mine]
    remaining = agent_remaining(ledger, agent)
    # 按资金量裁候选（2026-09-09）：单票预算 = 剩余额度×单票比例，且不超虚拟现金。
    # 买不起的（最小 100/200 股一手都超预算）整行不推给模型——池子是研究产物，
    # 与 agent 的资金量无关，不裁的话模型会反复点买不起的票、白白浪费一轮决策。
    from live_prompt_context import filter_affordable

    budget = min(remaining * PER_STOCK_PCT, agent_virtual_cash(ledger, agent))
    pool_for_agent, too_pricey = filter_affordable(pool, budget)
    if too_pricey and announce:
        print(f"  💸 [{agent}] 单票预算 ¥{budget:,.0f}，候选池剔除 {len(too_pricey)} 只买不起的："
              + "、".join(f"{c}{n}(最小{q}股 ¥{v:,.0f})"
                          for c, n, v, q in too_pricey))
    if too_pricey and ghost_source:
        # 影子代价账（kind=unseen）：这批票模型**压根没看见**，不是"被否决的决策"。
        # P2 已确认剔除名单里常有池内高分票（09-17 剔 11 只含 rank 1/2/3/4/6/8）——
        # 它的远期超额回答的是**资金规模**问题，与分数门限无关。
        # 记录条件只看 ghost_source（与打印条件 announce 解耦）：影子 A/B 预演
        # 不记（模拟轮事件进生产账会把剔除样本翻倍），演练轮记但带 .dry 戳。
        # 池快照有自己的时刻（此刻），不借用决策产出的 now：两者本就差一次 LLM 调用。
        _pool_now = now_cn()
        for _c, _n, _v, _q in too_pricey:
            ghost_ledger.veto(agent, _c, gate_rules.BUDGET_UNAFFORDABLE, _pool_now,
                              name=_n, ref_px=(_v / _q) if _q else None,
                              reason=f"单票预算 ¥{budget:,.0f} < 最小 {_q} 股 ¥{_v:,.0f}",
                              extra={"min_cost": _v, "min_qty": _q,
                                     "n_dropped": len(too_pricey)},
                              source=ghost_source)
    prompt = build_prompt(agent, my_holdings, pool_rows(pool_for_agent), direction,
                          remaining, pool=pool_for_agent, extra_context=extra_context)
    if pool_note:
        prompt = f"{pool_note}\n{prompt}"   # 池来源首部注入（P1-3，进对话流/日志）
    return {"holdings": my_holdings, "remaining": remaining, "budget": budget,
            "pool": pool_for_agent, "too_pricey": too_pricey, "prompt": prompt}


# ---------- P1 影子 A/B（2026-09-18） ----------
SHADOW_DIR = ROOT / "logs" / "shadow_context"
SHADOW_LOCK = ROOT / "logs" / "shadow_context.lock"
#: 影子采样窗口（北京，分钟）：09:20 起（新闻 09:25 期前后）到收盘后 5 分钟。
#: 窗外默认不采（盘后 market_state=休市、新闻分子已滞后，采了是脏数据）；
#: --shadow-force 仅手动冒烟，记录带 forced 标记、周报统计排除。
SHADOW_WINDOW = (9 * 60 + 20, 15 * 60 + 5)
_SHADOW_LOCK_FH: list = []


def _acquire_shadow_lock() -> bool:
    """影子实例锁：与主入口锁分开（影子绝不影响、也不会被主入口挡住）。"""
    import fcntl

    try:
        fh = open(SHADOW_LOCK, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _SHADOW_LOCK_FH.append(fh)  # 保持引用防 GC 释放锁
    return True


def _news_age_min() -> float | None:
    """新闻分子年龄（分钟，负值钳 0）；缺失/坏 JSON/非 dict → None。仅供影子记录标注。

    评审 MEDIUM（2026-09-18）：旧版对"语法合法但顶层非 dict"的 latest.json 抛
    AttributeError，而调用链（_run_shadow → main）无兜底——一份坏文件能炸掉整次
    采样（已付的 LLM 费全丢）。
    """
    try:
        d = json.loads((ROOT / "data" / "news_brief" / "latest.json")
                       .read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None
        ts = datetime.fromisoformat(str(d.get("ts")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=CN_TZ)
        return round(max(0.0, (now_cn() - ts).total_seconds() / 60), 1)
    except (OSError, ValueError, TypeError):
        return None


def build_daily_context_blocks(broker) -> tuple[str, dict]:
    """P1 每日上下文块（与整点轮同源）：盘面状态 + 新闻分子 → (文本, meta)。

    盘面块来自 market_state.build_market_state（确定性注入、5 分钟缓存）；
    新闻块来自 live_prompt_context.load_news_brief（>180 分钟自动带滞后标注）。
    实验里"B 臂没加成"必须可见 → meta 如实记录各块有无与新闻年龄。
    """
    parts: list[str] = []
    try:
        from market_state import build_market_state

        state_text = build_market_state(broker) or ""
    except Exception as exc:  # noqa: BLE001 块生成失败不阻塞（如实记录为空）
        print(f"⚠️ 盘面状态块生成失败：{str(exc)[:100]}")
        state_text = ""
    if state_text:
        parts.append(state_text)
    try:
        from live_prompt_context import load_news_brief

        news_text = load_news_brief() or ""
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 新闻分子块生成失败：{str(exc)[:100]}")
        news_text = ""
    if news_text:
        parts.append(news_text)
    return "\n".join(parts), {"market_state": bool(state_text),
                              "news_present": bool(news_text),
                              "news_age_min": _news_age_min()}


def _shadow_arm(decisions, usage, fail: str) -> dict:
    """记录一条调用臂：决策（理由截断 80 字）+ usage + 失败原因。

    pct 三态（given/missing/dirty）必须保留（评审 MEDIUM）：parse_decision 刻意
    区分"未表达比例"与"脏值停手"，记录里塌成同一个 0.0 会让报告低估 effect。
    """
    rows = None
    if decisions is not None:
        rows = [{"action": str(d.get("action")), "code": str(d.get("code")),
                 "pct": d.get("pct"), "pct_given": bool(d.get("pct_given", True)),
                 "pct_bad_raw": str(d.get("pct_bad_raw") or ""),
                 "reason": str(d.get("reason") or "")[:80]}
                for d in decisions if isinstance(d, dict)]
    return {"decisions": rows, "fail": fail or "", "usage": usage}


def _live_lock_held() -> bool:
    """主入口锁是否被占（影子重试档用：主入口没跑完不采，避免抢 LLM/AiData 配额）。

    评审 HIGH-2（2026-09-18）：主入口一轮可远超 5 分钟（09-18 实录持锁到 10:21），
    固定时刻的峰值影子会与真实下单轮并发。cron 改为 5 分钟重试档，由本探测把关。
    """
    import fcntl

    try:
        fh = open(LOCK_FILE, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    finally:
        fh.close()


def _shadow_sampled_today(day_iso: str) -> str:
    """当日已有**正式**（非 forced）影子记录 → 返回其 ts，否则 ""。

    cron 用 5 分钟重试档等主入口锁释放——幂等由这里保证（否则每次重试都追加
    一行，报告的"天数"会被灌水，评审 H-2/MEDIUM-2）。
    """
    try:
        text = (SHADOW_DIR / f"{day_iso}.jsonl").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and not rec.get("forced"):
            return str(rec.get("ts") or "?")
    return ""


def _run_shadow(args) -> int:
    """P1 影子 A/B 主流程：同一输入快照下跑 A1/A2/B 三臂，只记录不下单。

    实验设计（判据见 scripts/shadow_context_report.py）：A1/A2 同提示词重跑 =
    temperature=0.3 的采样噪声基线；B = 现状提示词 + 盘面状态 + 新闻分子。
    noise=diff(A1,A2) 对照 effect=diff(A1,B)，避免把随机波动当信息的影响。
    影子不得改实盘决策面：不下单、不写执行状态/对话流/熔断记录（行情源自身的
    观测落盘——如 sentiment_zt.jsonl——与主入口同源同质，不在此列）。
    """
    from agent_tools.brokers.base import BrokerError

    now = now_cn()
    hm = now.hour * 60 + now.minute
    forced = bool(getattr(args, "shadow_force", False))
    if not forced and not (SHADOW_WINDOW[0] <= hm <= SHADOW_WINDOW[1]):
        print(f"⏭️ {now:%F %T} 不在影子采样窗口（09:20-15:05），跳过"
              "（--shadow-force 仅手动冒烟，记录带 forced 标记）")
        return 0
    if not forced and _live_lock_held():
        print("⏭️ 主入口仍在跑（live_llm_trade.lock 被占），本档跳过等下一次采样"
              "（避免与真实下单轮抢 LLM/AiData 配额）")
        return 0
    if not forced:
        sampled = _shadow_sampled_today(now.date().isoformat())
        if sampled:
            print(f"⏭️ 当日已有影子记录（{sampled}），跳过")
            return 0
    if not _acquire_shadow_lock():
        print("⏭️ 已有影子实例在跑，跳过")
        return 0
    try:
        broker, acct = _query_account_with_retry()
    except BrokerError as exc:
        print(f"❌ 影子模式：桥账户查询失败（{str(exc)[:120]}）")
        return 1
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    holdings = holding_rows(broker, positions)
    pool, direction, pool_info = load_pool_meta(getattr(args, "top", 20))
    if not pool:
        print("❌ 影子模式：无候选池，无法构建输入快照")
        return 1
    from picks_source import prompt_line

    pool_note = prompt_line(pool_info)
    agents = [a.strip() for a in str(getattr(args, "agents", "")).split(",")
              if a.strip()] or enabled_agents()
    ledger = load_ledger()
    blocks, meta = build_daily_context_blocks(broker)
    rec = {"date": now.date().isoformat(), "ts": now.isoformat(),
           "mode": "shadow_context_ab", "forced": forced,
           "blocks_text": blocks, **meta, "agents": {}}
    ok_agents = 0
    for agent in agents:
        # 评审 MEDIUM（2026-09-18）：单 agent 异常不许带走整次采样（9 次 LLM 的钱
        # 与其余 agent 的产出要保住）——异常记进该 agent 记录继续。
        try:
            snap_a = _agent_context(agent, ledger, holdings, pool, direction, pool_note,
                                    announce=False)
            snap_b = _agent_context(agent, ledger, holdings, pool, direction, pool_note,
                                    extra_context=blocks)
            pa, pb = snap_a["prompt"], snap_b["prompt"]
            da1, _c1, ua1, fa1 = decide_with_retry(pa, agent)
            da2, _c2, ua2, fa2 = decide_with_retry(pa, agent)
            db, _c3, ub, fb = decide_with_retry(pb, agent)
            rec["agents"][agent] = {
                "prompt_len_a": len(pa), "prompt_len_b": len(pb),
                "a1": _shadow_arm(da1, ua1, fa1),
                "a2": _shadow_arm(da2, ua2, fa2),
                "b": _shadow_arm(db, ub, fb)}
            ok_agents += 1
            print(f"  🧪 [{agent}] A1/A2/B 三臂完成（prompt {len(pa)}→{len(pb)} 字符，"
                  f"fail: {fa1 or '-'}/{fa2 or '-'}/{fb or '-'}）")
        except Exception as exc:  # noqa: BLE001 单 agent 失败不带走其余
            print(f"  ❌ [{agent}] 影子三臂异常：{str(exc)[:120]}")
            rec["agents"][agent] = {"error": str(exc)[:200]}
    try:
        SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        with open(SHADOW_DIR / f"{rec['date']}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except (OSError, TypeError) as exc:
        print(f"❌ 影子记录写入失败：{exc}")
        return 1
    print(f"✅ 影子 A/B 记录 → {SHADOW_DIR / (rec['date'] + '.jsonl')}"
          f"（{ok_agents}/{len(agents)} agent × 3 臂；未下任何单、未写执行状态/"
          f"对话流/熔断）")
    return 0 if ok_agents else 1


def _run(args) -> int:
    from agent_tools.brokers.base import BrokerError

    day = now_cn().date().isoformat()

    def mark(**kw) -> None:
        """当日执行状态（仅 --execute 落盘，dry-run 不写）。"""
        if args.execute:
            _write_state(day, **kw)

    mark(started_ts=now_cn().isoformat(), orders_attempted=False, ok=None, note="")

    # 影子代价账的 source 戳（与 mark 同一纪律）：演练轮到不了柜台，"被拦下的
    # 意图"在演练轮里没有机会成本，但它仍证明"这条闸今天动过"——不藏也不混，
    # 加上 .dry 后缀，报告里单列一档。执行段再叠一层 .exec（同一规则在决策段
    # 还是执行段拦下的，读法不同：前者是模型意图，后者是钱到位了被拦）。
    ghost_src = "live_llm_trade" if args.execute else "live_llm_trade.dry"
    ghost_src_exec = ghost_src + ".exec"

    try:
        broker, acct = _query_account_with_retry()
    except BrokerError as exc:
        # 2026-09-07/09-09 实录：09:25-10:26 桥断线 → 裸崩退出，全天 0 笔交易且零告警
        print(f"❌ 桥账户查询重试 {ACCT_QUERY_ATTEMPTS} 次仍失败：{exc}")
        from push_notify import notify

        notify("调仓轮取消 ⚠️",
               f"桥账户查询重试 {ACCT_QUERY_ATTEMPTS} 次仍失败：{str(exc)[:120]}\n"
               "本轮调仓未执行 —— Windows 交易端大概率已掉线")
        mark(ok=False, note="bridge_account_query")
        return 1
    asset = float((acct.get("asset") or {}).get("asset") or 0)
    cash = float((acct.get("asset") or {}).get("cash") or 0)
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    holdings = holding_rows(broker, positions)
    print(f"💰 账户资产 ¥{asset:,.0f} 现金 ¥{cash:,.0f} 持仓 {len(holdings)} 只")

    pool, direction, pool_info = load_pool_meta(args.top)
    if not pool:
        print("❌ 无候选池（picks.json 缺失或为空），终止")
        mark(ok=False, note="no_pool")
        return 1
    print(f"📋 候选池 {len(pool)} 只  大盘: {direction.get('direction', '—')}")
    # 池来源/新鲜度随行进提示词（P1-3）：log.jsonl 存的就是提示词，事后对账能看出
    # 某一轮吃的是哪份池；滞后/缺失在这里也必须可见（不能只留在 stderr）
    from picks_source import prompt_line

    pool_note = prompt_line(pool_info)
    print(f"  {pool_note}")

    from live_hourly_analysis import append_log, daily_buy_codes

    agents = [a.strip() for a in args.agents.split(",") if a.strip()] or enabled_agents()
    ledger = load_ledger()
    ok = 0
    partial_notes: list[str] = []   # 局部失败（不影响整体收尾判定）
    for agent in agents:
        # 输入快照 + 提示词：与 P1 影子 A/B（_run_shadow）共用 _agent_context（防漂移）
        snap = _agent_context(agent, ledger, holdings, pool, direction, pool_note,
                              ghost_source=ghost_src)
        my_holdings, prompt = snap["holdings"], snap["prompt"]
        # 循环熔断（第二道防线）：当日已实现亏损/日内权益回撤超限 → 该 agent 当日禁买。
        # 卖出照常放行（降风险不受限）；dry-run 只评估不落盘。
        from live_breaker import check_and_trip

        halt, halt_reason = check_and_trip(agent, persist=bool(args.execute))
        if halt:
            print(f"  🛑 [{agent}] 循环熔断：{halt_reason} → 今日禁止买入（卖出照常）")
        # 买入闸门（纯函数，与整点轮共用同一实现，防两处判定漂移）
        from buy_gate import BuyGate, check_buy
        from symbol_policy import load_policy_with_risk

        gate = BuyGate(pool_codes=frozenset(p["code"] for p in pool),
                       per_stock_pct=PER_STOCK_PCT, max_new_buys=MAX_NEW_BUYS,
                       halted=halt, halt_reason=halt_reason,
                       policy=load_policy_with_risk())  # 含解禁/负面新闻事件风险清单
        # 标的边界用的名称表：候选池 + 本 agent 持仓（ST/退市识别，见 symbol_policy）
        nm_by_code = {p["code"]: p.get("name") for p in pool}
        nm_by_code.update({h["code"]: h.get("name") for h in my_holdings})
        # 决策：LLM + 解析，失败重试 1 次（2026-09-12 P0-5）
        decisions, content, usage, fail_reason = decide_with_retry(prompt, agent)
        if decisions is None:
            print(f"⚠️ [{agent}] 决策失败（{fail_reason}），跳过（LLM 原文前 200 字）："
                  f"{content[:200]!r}")
            _log_parse_failure(agent, prompt, content, usage, fail_reason)
            continue
        if not decisions:
            print(f"⏭️ [{agent}] 空决策（合法 no-op，本轮不动）")
            continue
        # 决策时效打戳 + 过闸（2026-09-18）：打戳在解析后（=产出时刻）；执行端
        # drop_stale_decisions 丢弃 >DECISION_MAX_AGE_MIN 的决策（含卖出——持仓由
        # 条件位/下一轮兜底）。本路径 decide→execute 同轮，闸主要在慢执行时生效。
        from live_hourly_analysis import drop_stale_decisions

        # 本轮决策的时间基准**取一次传到底**（同 9f40630 的教训）：打戳、记分卡
        # 入库、展示行三处共用同一个 now，事后不会出现"同一轮半秒内两个时刻"
        now = now_cn()
        for d in decisions:
            d.setdefault("decided_at", now.isoformat())
        decisions = drop_stale_decisions(decisions, agent,
                                         persist=bool(getattr(args, "execute", False)))
        if not decisions:
            continue
        # 决策远期记分卡入池（P2，2026-09-18）：09:35 是每天第一笔真实调仓，此前
        # **整条路径不落记分卡** → 决策池只看得见整点轮，"模型分数门限"这类问题
        # 连度量都没有样本。带 pool_view = 该 agent 实际看到的池（剔除买不起的之后），
        # 供事后分辨"低分买入"与"资金买不起高分票"（两者方向相反）。
        try:
            from decision_track import ingest_decisions

            ingest_decisions(agent, decisions, now.isoformat(),
                             held_codes={h["code"] for h in my_holdings},
                             source="live_llm_trade",
                             pool_view={"shown": snap["pool"], "dropped": snap["too_pricey"],
                                        "full": pool, "file": pool_info.get("file", "")})
        except Exception as exc:  # noqa: BLE001  统计失败不得影响交易主链路
            print(f"  ⚠️ [{agent}] 决策记分卡入池失败（不影响交易）: {exc}")
        # 决策展示 + 校验
        sells, buys, summary = [], [], [f"（模型自主调仓决策，{now:%F %T}）"]
        # 在途卖单闸门：LLM 决策耗时以分钟计，期间分钟哨兵可能刚止损卖出同一代码。
        # 决策校验前重新读盘（口径统一在 live_fills.inflight_codes）；执行前
        # reconcile 之后还会再刷新一次（见下方 sell 执行段）。
        pending_sell = inflight_codes("sell")
        # 在途买单闸门（2026-09-11，与整点轮同口径）：买单挂 pending 时账本尚未扣现金
        # /加持仓，补跑或后续轮次再决策买入同一代码 → 桥上是第二笔真委托（重复建仓）。
        pending_buy = inflight_codes("buy")
        new_buys = 0                          # 本轮新开仓计数
        opened_today = daily_buy_codes(agent)  # 当日已开仓代码（09:35 可能晚于整点轮）
        for d in decisions:
            code = d["code"]
            if d["action"] == "hold":
                print(f"  🟢 [{agent}] 持有 {code}: {d['reason']}")
                continue
            h = next((x for x in my_holdings if x["code"] == code), None)
            if d["action"] == "sell":
                # 卖出只能动自己名下的持仓（my_holdings 按分账账本裁剪，防跨 agent 卖仓）
                if not h:
                    print(f"  ⚠️ [{agent}] 卖出 {code}: 非持仓，跳过")
                    continue
                if code in pending_sell:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 已有在途卖单未确认，跳过")
                    continue
                avail = h["avail"]
                if avail <= 0:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: T+1 不可卖（可卖量 0），跳过")
                    continue
                # 脏 pct（给了值但解析不出，如 "0.3股"）≠ 没给比例：跳过并留痕，
                # 绝不按清仓执行（2026-09-12 审查 HIGH-2；sell_fraction 兜底返 0）
                if d.get("pct_bad_raw"):
                    print(f"  ⏭️ {note_pct_unparsed(agent, code, d['pct_bad_raw'], 'sell', persist=bool(args.execute))}")
                    continue
                frac = sell_fraction(d)
                raw_vol = int(avail * frac)
                vol = round_sell_qty(code, raw_vol, avail)
                if vol <= 0:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 比例 {frac:.0%} 无合法可卖量，跳过")
                    continue
                if vol != raw_vol:
                    print(f"  ⚖️ [{agent}] 卖出 {code}: 意图 {raw_vol} 股 → 手数合规实际 {vol} 股"
                          f"（{board_of(code)} 口径）")
                if at_limit_down(code, h['day_chg'], h.get('name')):
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 跌停（{h['day_chg']:+.2f}%），不接")
                    continue
                sells.append((code, vol, d["reason"]))
                print(f"  📉 [{agent}] 卖出 {code} {vol}/{avail}股 "
                      f"({frac:.0%}{'' if d.get('pct_given', True) else '，未给比例按清仓'}): "
                      f"{d['reason']}")
            elif d["action"] == "buy":
                nm = (h or {}).get("name") or nm_by_code.get(code)
                if code in pending_buy:
                    print(f"  ⏭️ [{agent}] 买入 {code}: 已有在途买单未确认，跳过")
                    ghost_ledger.veto(agent, code, gate_rules.INFLIGHT_DUP_BUY, now,
                                      reason="已有在途买单未确认", pct=d.get("pct"),
                                      name=nm, source=ghost_src)
                    continue
                if d.get("pct_bad_raw"):
                    print(f"  ⏭️ {note_pct_unparsed(agent, code, d['pct_bad_raw'], 'buy', persist=bool(args.execute))}")
                    ghost_ledger.veto(agent, code, gate_rules.PCT_INVALID, now,
                                      reason=f"比例不可解析：{d['pct_bad_raw']}",
                                      name=nm, source=ghost_src)
                    continue
                if d["pct"] <= 0:
                    why = ("明说 pct=0" if d.get("pct_given", True)
                           else "未表达买入比例（pct）")
                    print(f"  ⏭️ [{agent}] 买入 {code}: {why}，跳过（买入必须有明确比例）")
                    ghost_ledger.veto(agent, code, gate_rules.PCT_ZERO, now, reason=why,
                                      pct=d.get("pct"), name=nm, source=ghost_src)
                    continue
                bd = check_buy(code, d["pct"], bool(h), new_buys, opened_today, gate, name=nm)
                if not bd.ok:
                    print(f"  ⏭️ [{agent}] 买入 {code}: {bd.reason}，跳过")
                    ghost_ledger.veto(agent, code, bd.rule, now, reason=bd.reason,
                                      pct=d.get("pct"), name=nm, source=ghost_src)
                    continue
                new_buys = bd.new_buys
                buys.append((code, bd.pct, d["reason"]))
                print(f"  📈 [{agent}] 买入 {code} 用剩余额度 {bd.pct:.0%}"
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
        if sells or buys:
            # 真正要动单了：此后崩溃一律不许补跑（补跑会重复下单）
            mark(orders_attempted=True)

        # 执行：先卖后买（先补记在途成交，再下新单）
        try:
            reconcile(broker)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 成交回报 reconcile 失败: {exc}")
        # reconcile 后刷新在途集：上一班超时挂队的卖单 / 分钟哨兵的止损单都在里面
        pending_sell = inflight_codes("sell")
        pending_buy = inflight_codes("buy")
        # 2026-09-18 执行时段闸门（与整点轮同口径）：数据源退避可把执行段拖到
        # 收盘后，收盘后的委托会被柜台判废（600176 实录）——非连续竞价时段不下单。
        # 决策已记录、状态照常落盘，只跳过买卖执行。
        # 2026-09-21 审查 HIGH-1：本闸门必须排在用户指令闸门**之前**——此前
        # apply_forced_sells 在闸门之前执行，收盘/午休时段的 QQ 清仓照样发出真单
        # （live_hourly_analysis 的顺序是对的，只有这里反了）。
        if not in_continuous_auction(now_cn()):
            print(f"  ⏭️ [{agent}] 非连续竞价时段（现 {now_cn():%H:%M}），"
                  f"本轮执行段跳过（决策已记录，不下单）")
            sells, buys = [], []
        bars_map = lq_klines_batch([c for c, _, _ in sells + buys],
                                   interval="daily", count=5,
                                   prefer="aidata", broker=broker)
        # 2026-09-14 用户 QQ 指令闸门：强制清仓先于一切；停买冻结买入段
        try:
            from agent_commands import apply_forced_sells, has_halt_buy

            apply_forced_sells(broker, agent, now_cn())
            if has_halt_buy(agent):
                print(f"  ⏸️ [{agent}] 用户指令：暂停买入，本轮只处理卖出/持有")
                buys = []
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ [{agent}] 用户指令闸门异常（继续）: {exc}")
        for code, vol, _ in sells:
            if code in pending_sell:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 执行前已有在途卖单未确认，本轮不下单")
                continue
            bars = bars_map.get(code) or lq_klines(code, interval="daily", count=5,
                                                   prefer="aidata", broker=broker)
            if len(bars) < 2:
                print(f"  ⚠️ [{agent}] 卖出 {code}: 行情不足，跳过")
                continue
            price = float(bars[-1].get("close") or 0)
            if price <= 0:
                continue
            price, bad_kl = _quote_guarded_price(broker, code, price)
            if bad_kl:
                print(f"  ⚠️ [{agent}] 卖出 {code}: K线价与实时价偏差>40%（脏数据），"
                      f"按实时价 {price} 计算限价")
            try:
                limit = sell_limit(price)     # 卖价缓冲的唯一出处（live_fills）
                result = broker.sell(None, None, code, vol, price=limit)
                submit_ts = now_cn().isoformat()
                from live_fills import ack_line

                print("  " + ack_line(f"[{agent}] 卖出 {code}", result))
                fill = wait_fill(broker, result.get("order_id", ""))
                fill_ts = now_cn().isoformat()
                fv = int((fill or {}).get("filled_volume") or 0)
                fp, approx = float((fill or {}).get("filled_price") or limit), False
                if fv > 0:
                    fp, approx = _quote_guarded_price(broker, code, fp)
                    if approx:
                        print(f"  ⚠️ [{agent}] 卖出 {code}: 桥回报成交价偏离实时价>40%，"
                              f"按实时价 {fp} 记账（approx）")
                # 记账 + 余量跟踪统一收口（2026-09-12 P0-4）：部分成交剩余的量挂
                # pending 继续跟踪，不再「记完已成交的就走」→ 余下成交成账外单
                # 成交推送由 settle 内统一发（2026-09-18：提交时刻不推，成功才推）
                rec = settle_place_fill(result.get("order_id"), agent, code, "sell",
                                        vol, limit, fill, fill_price=fp,
                                        source="调仓", ref_px=price,
                                        decided_ts=now.isoformat())
                if rec["filled"] > 0:
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "sell",
                              "volume": rec["filled"], "price": rec["price"],
                              "cost_price": rec["cost_price"],
                              "remaining": rec["remaining"],
                              "approx": approx,
                              "fill": {"order_id": (fill or {}).get("order_id"),
                                       "filled_price": rec["price"],
                                       "filled_volume": rec["filled"]},
                              # 执行损耗 TCA：三段价 + 三个时刻（口径见 exec_cost）
                              **exec_cost.tape_fields(
                                  side="sell", ref_px=price, limit_px=limit,
                                  fill_px=rec["price"], wanted=vol,
                                  filled=rec["filled"], decided_ts=now.isoformat(),
                                  submit_ts=submit_ts, fill_ts=fill_ts,
                                  path="llm_trade")})
                if rec["pending"] or rec["untracked"]:
                    pending_sell.add(code)   # 同轮重复决策同一代码时不再下第二单
                    # （untracked：桥没回委托号 → reconcile 追不了，更要挡重复下单）
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "sell",
                              "volume": vol, "price": limit,
                              "pending": rec["pending"], "untracked": rec["untracked"],
                              "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
                from live_ledger import defer_on_exc

                if defer_on_exc(agent, "sell", code, vol, exc,
                                now_cn().isoformat()):
                    print(f"  ⏳ [{agent}] 卖出 {code} 已登记延期单（桥断链，恢复后自动重放）")
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "volume": vol, "error": str(exc)})
            time.sleep(1)  # 桥限流
        # 卖出回款后账户现金可能变化，重新查一次（只在真要买入时才查）。
        # 此处已在卖出**之后**：查询重试全败**不能**落 ok:false——那会诱导补跑重复卖出。
        # 正确语义 = 跳过该 agent 的买入段、继续其他 agent，收尾记 ok:true + note。
        cash_ok = True
        if buys:
            try:
                broker, acct2 = _query_account_with_retry()
                cash = float((acct2.get("asset") or {}).get("cash") or 0)
            except BrokerError as exc:
                cash_ok = False
                print(f"  ⚠️ [{agent}] 卖出后账户复查失败（{exc}）→ 跳过本 agent 买入段"
                      f"（卖出已成交，不回滚、不重复）")
                partial_notes.append(f"{agent}:acct2_failed")
        if not cash_ok:
            ok += 1
            continue
        ledger = load_ledger()
        round_added: dict = {}   # 本轮已委托成本（同轮重复决策同码时防叠加，评审 M-3）
        for code, pct, _ in buys:
            if code in pending_buy:
                print(f"  ⏭️ [{agent}] 买入 {code}: 执行前已有在途买单未确认，本轮不下单")
                ghost_ledger.veto(agent, code, gate_rules.INFLIGHT_DUP_BUY, now,
                                  reason="执行前已有在途买单未确认", pct=pct,
                                  name=nm_by_code.get(code), source=ghost_src_exec)
                continue
            remaining = agent_remaining(ledger, agent)
            bars = bars_map.get(code) or lq_klines(code, interval="daily", count=5,
                                                   prefer="aidata", broker=broker)
            o = compute_order(bars, remaining, pct, code)
            if not o["ok"]:
                print(f"  ⏭️ [{agent}] 买入 {code}: {o['reason']}")
                ghost_ledger.veto(agent, code, o["rule"], now, reason=o["reason"],
                                  pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec)
                continue
            _opx, bad_kl = _quote_guarded_price(broker, code, o["price"])
            if bad_kl:
                print(f"  ⏭️ [{agent}] 买入 {code}: K线价 {o['price']} 与实时价偏差>40%"
                      f"（脏数据），跳过防超量下单（2026-09-08 福恩股份事故）")
                ghost_ledger.veto(agent, code, gate_rules.DATA_DIRTY_QUOTE, now,
                                  reason=f"K线价 {o['price']} 与实时价偏差>40%", pct=pct,
                                  ref_px=o.get("price"), name=nm_by_code.get(code),
                                  source=ghost_src_exec)
                continue
            # 分账额度红线：子 agent 买入不能超自己 ¥10 万虚拟子账户的现金
            # （remaining 是额度口径、cash 是桥总账户真实现金，都拦不住已实现
            #   亏损造成的透支——虚拟现金才是子账户真正买得起的钱）
            vcash = agent_virtual_cash(ledger, agent)
            if o["cost"] > vcash:
                print(f"  ⏭️ [{agent}] 买入 {code}: 子账户虚拟现金不足 "
                      f"¥{o['cost']:,.0f} > ¥{vcash:,.0f}（分账额度不透支，跳过）")
                ghost_ledger.veto(agent, code, gate_rules.CASH_VCASH, now,
                                  reason=f"虚拟现金 ¥{vcash:,.0f} < 成本 ¥{o['cost']:,.0f}",
                                  pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec,
                                  extra={"cost": round(o["cost"], 2)})
                continue
            # 杠杆硬约束：加仓后持仓成本 ≤ 权益×1.5（现金为负时才可能超）
            pos_cost = agent_used(ledger, agent)
            equity = vcash + pos_cost
            if equity > 0 and (pos_cost + o["cost"]) > LEVERAGE_MAX * equity:
                print(f"  ⏭️ [{agent}] 买入 {code}: 加仓后杠杆超 {LEVERAGE_MAX}×权益"
                      f"（{pos_cost + o['cost']:,.0f} > {LEVERAGE_MAX * equity:,.0f}），跳过")
                ghost_ledger.veto(agent, code, gate_rules.LEV_OVER, now,
                                  reason=f"加仓后 {pos_cost + o['cost']:,.0f} > "
                                         f"{LEVERAGE_MAX}×权益 {LEVERAGE_MAX * equity:,.0f}",
                                  pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec,
                                  extra={"cost": round(o["cost"], 2)})
                continue
            # 单票集中度闸（2026-09-18 机构级）：同票成本 + 本单成本 ≤ MAX_POS_PCT×权益。
            # per_stock_pct 只管"单笔占剩余额度"，同一标的跨轮加仓此前不封顶。
            # round_added：账本快照在循环外，同轮已成交的加仓不计进去会绕闸（评审 M-3）。
            from buy_gate import position_cap_reason

            cap_reason = position_cap_reason(
                position_cost(ledger, agent, code) + round_added.get(code, 0.0),
                o["cost"], equity, MAX_POS_PCT)
            if cap_reason:
                print(f"  ⏭️ [{agent}] 买入 {code}: 单票集中度超限（{cap_reason}），跳过")
                ghost_ledger.veto(agent, code, gate_rules.CAP_POSITION, now,
                                  reason=cap_reason, pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec,
                                  extra={"cost": round(o["cost"], 2)})
                continue
            if o["cost"] > cash:
                print(f"  ⏭️ [{agent}] 买入 {code}: 账户现金不足 ¥{o['cost']:,.0f} > ¥{cash:,.0f}")
                ghost_ledger.veto(agent, code, gate_rules.CASH_INSUFFICIENT, now,
                                  reason=f"账户现金 ¥{cash:,.0f} < 成本 ¥{o['cost']:,.0f}",
                                  pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec,
                                  extra={"cost": round(o["cost"], 2)})
                continue
            cash -= o["cost"]
            round_added[code] = round_added.get(code, 0.0) + o["cost"]  # 见闸门注释
            try:
                result = broker.buy(None, None, code, o["volume"], price=o["limit_price"])
                submit_ts = now_cn().isoformat()
                from live_fills import ack_line

                print("  " + ack_line(f"[{agent}] 买入 {code} {o['volume']}股 "
                                      f"限价 ¥{o['limit_price']:.2f}", result))
                fill = wait_fill(broker, result.get("order_id", ""))
                fill_ts = now_cn().isoformat()
                fv = int((fill or {}).get("filled_volume") or 0)
                fp, approx = float((fill or {}).get("filled_price") or o["price"]), False
                if fv > 0:
                    fp, approx = _quote_guarded_price(broker, code, fp)
                    if approx:
                        print(f"  ⚠️ [{agent}] 买入 {code}: 桥回报成交价偏离实时价>40%，"
                              f"按实时价 {fp} 记账（approx）")
                # 成交推送由 settle 内统一发（2026-09-18：提交时刻不推，成功才推）
                rec = settle_place_fill(result.get("order_id"), agent, code, "buy",
                                        o["volume"], o["limit_price"], fill, fill_price=fp,
                                        reason=str(o.get("reason") or ""), source="调仓",
                                        ref_px=o["price"], decided_ts=now.isoformat())
                if rec["filled"] > 0:
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "buy",
                              "volume": rec["filled"], "price": rec["price"],
                              "remaining": rec["remaining"], "approx": approx,
                              "fill": {"order_id": (fill or {}).get("order_id"),
                                       "filled_price": rec["price"],
                                       "filled_volume": rec["filled"]},
                              # 执行损耗 TCA：三段价 + 三个时刻（口径见 exec_cost）
                              **exec_cost.tape_fields(
                                  side="buy", ref_px=o["price"],
                                  limit_px=o["limit_price"], fill_px=rec["price"],
                                  wanted=o["volume"], filled=rec["filled"],
                                  decided_ts=now.isoformat(), submit_ts=submit_ts,
                                  fill_ts=fill_ts, path="llm_trade")})
                if rec["pending"] or rec["untracked"]:
                    pending_buy.add(code)    # 同轮重复决策同一代码时不再下第二单
                    # （untracked：桥没回委托号 → reconcile 追不了，更要挡重复下单）
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "buy",
                              "volume": o["volume"], "price": o["price"],
                              "pending": rec["pending"], "untracked": rec["untracked"],
                              "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
                # 真金白银的机会损失：桥/柜台拒单也会进影子账（kind=veto），
                # 事后按同期超额定价——断链一小时的代价从此有数，不只是一行 ❌。
                ghost_ledger.veto(agent, code, gate_rules.EXEC_SUBMIT_FAILED, now,
                                  reason=f"下单失败：{type(exc).__name__}: {exc}",
                                  pct=pct, ref_px=o.get("price"),
                                  name=nm_by_code.get(code), source=ghost_src_exec,
                                  extra={"cost": round(o["cost"], 2)})
                from live_ledger import defer_on_exc

                if defer_on_exc(agent, "buy", code, o["volume"], exc,
                                now_cn().isoformat()):
                    print(f"  ⏳ [{agent}] 买入 {code} 已登记延期单（桥断链，恢复后自动重放）")
                log_line({"ts": now_cn().isoformat(), "mode": "execute", "agent": agent,
                          "code": code, "error": str(exc)})
            time.sleep(1)
        ok += 1

    print(f"\n{'✅ 调仓执行完成' if args.execute else '🟡 决策演练完成（--execute 才下单）'}"
          f"：{ok}/{len(agents)} agent 成功")
    if args.execute:
        mark(ok=True, note="; ".join(partial_notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
