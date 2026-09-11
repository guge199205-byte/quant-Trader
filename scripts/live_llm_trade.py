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
import re
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
    record_buy,
    record_sell,
    sane_fill_price,
    save_ledger,
)
from live_fills import (add_pending, inflight_codes, reconcile, wait_fill,  # noqa: E402
                        round_sell_qty)
from ashare_rules import at_limit_down, board_of  # noqa: E402

# 杠杆硬约束（与 live_hourly_analysis 同口径）
LEVERAGE_MAX = 1.5

CN_TZ = ZoneInfo("Asia/Shanghai")
PICKS_JSON = ROOT / ".." / "projects" / "quantmind" / "data" / "reports" / "stock_picks"
PER_STOCK_PCT = 0.2   # 单票买入 ≤ 剩余额度 20%
MAX_NEW_BUYS = 3      # 新开仓上限：单轮 + 当日累计（风险预算 max_new_buys 覆盖）

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
    """
    global LEVERAGE_MAX, PER_STOCK_PCT, MAX_NEW_BUYS
    try:
        from risk_budget_agent import load_limits

        lim = load_limits()
    except Exception:  # noqa: BLE001
        return
    LEVERAGE_MAX = lim.get("leverage_max", LEVERAGE_MAX)
    PER_STOCK_PCT = lim.get("per_stock_pct", PER_STOCK_PCT)
    MAX_NEW_BUYS = lim.get("max_new_buys", MAX_NEW_BUYS)


_apply_risk_budget()


def _quote_guarded_price(broker, code: str, fp: float) -> tuple[float, bool]:
    """用实时行情护栏校验价格：K 线脏数据/桥回报坏价（偏离实时价>40%）时
    回退实时价并 approx 标记；取不到行情原样返回。"""
    try:
        quote = broker.get_quote(code, "")
        ref = float((quote or {}).get("close") or 0)
    except Exception:  # noqa: BLE001
        return fp, False
    return sane_fill_price(fp, ref)


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
                 direction: dict, quota_remaining: float,
                 pool: list | None = None) -> str:
    from live_prompt_context import budget_filter_note

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
    lines += ["", "【今日大盘方向】（最新研究产出）：",
              f"{direction.get('direction', '—')}"
              f"{'（总分 ' + str(direction.get('total_score')) + '/11）' if direction.get('total_score') is not None else ''}",
              ""]
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
    try:
        from live_prompt_context import risk_warning_block

        _nm = {h["code"]: h.get("name") for h in holdings}
        _nm.update({p.get("code"): p.get("name") for p in (pool or [])})
        rb = risk_warning_block([h["code"] for h in holdings],
                                [p.get("code") for p in (pool or [])], _nm)
        if rb:
            lines += [rb]
    except Exception:  # noqa: BLE001 风险清单不可用不阻塞提示词
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
    args = ap.parse_args()

    # 交易日历闸门：法定节假日休市不分析（cron 1-5 覆盖不到法定假日）
    from trading_cal import is_trading_day, why_not

    today = now_cn().date()
    if not is_trading_day(today):
        print(f"⏭️ {now_cn():%F %T} 非交易日（{why_not(today)}），跳过模型自主调仓")
        return 0

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


def _run(args) -> int:
    from agent_tools.brokers.base import BrokerError

    day = now_cn().date().isoformat()

    def mark(**kw) -> None:
        """当日执行状态（仅 --execute 落盘，dry-run 不写）。"""
        if args.execute:
            _write_state(day, **kw)

    mark(started_ts=now_cn().isoformat(), orders_attempted=False, ok=None, note="")

    try:
        broker, acct = _query_account_with_retry()
    except BrokerError as exc:
        # 2026-09-07/09-09 实录：09:25-10:26 桥断线 → 裸崩退出，全天 0 笔交易且零告警
        print(f"❌ 桥账户查询重试 {ACCT_QUERY_ATTEMPTS} 次仍失败：{exc}")
        mark(ok=False, note="bridge_account_query")
        return 1
    asset = float((acct.get("asset") or {}).get("asset") or 0)
    cash = float((acct.get("asset") or {}).get("cash") or 0)
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    holdings = holding_rows(broker, positions)
    print(f"💰 账户资产 ¥{asset:,.0f} 现金 ¥{cash:,.0f} 持仓 {len(holdings)} 只")

    pool, direction = load_pool(args.top)
    if not pool:
        print("❌ 无候选池（picks.json 缺失或为空），终止")
        mark(ok=False, note="no_pool")
        return 1
    print(f"📋 候选池 {len(pool)} 只  大盘: {direction.get('direction', '—')}")

    from live_hourly_analysis import append_log, call_llm, daily_buy_codes

    agents = [a.strip() for a in args.agents.split(",") if a.strip()] or enabled_agents()
    ledger = load_ledger()
    ok = 0
    partial_notes: list[str] = []   # 局部失败（不影响整体收尾判定）
    for agent in agents:
        # 账本 positions 是 {code: {volume, cost_price, ...}} 字典（live_ledger 内部结构）
        mine = set((ledger.get("agents") or {}).get(agent, {}).get("positions", {}))
        # 空账本 = 无持仓。2026-09-08 事故：曾有 `if mine else holdings` 兜底把
        # 共享桥账户全量持仓当"你名下"喂给空账本 agent → pro 卖了 flash 的生益电子
        my_holdings = [h for h in holdings if h["code"] in mine]
        remaining = agent_remaining(ledger, agent)
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
        # 按资金量裁候选（2026-09-09）：单票预算 = 剩余额度×单票比例，且不超虚拟现金。
        # 买不起的（最小 100/200 股一手都超预算）整行不推给模型——池子是研究产物，
        # 与 agent 的资金量无关，不裁的话模型会反复点买不起的票、白白浪费一轮决策。
        from live_prompt_context import filter_affordable

        budget = min(remaining * PER_STOCK_PCT, agent_virtual_cash(ledger, agent))
        pool_for_agent, too_pricey = filter_affordable(pool, budget)
        if too_pricey:
            print(f"  💸 [{agent}] 单票预算 ¥{budget:,.0f}，候选池剔除 {len(too_pricey)} 只买不起的："
                  + "、".join(f"{c}{n}(最小{q}股 ¥{v:,.0f})"
                              for c, n, v, q in too_pricey))
        prompt = build_prompt(agent, my_holdings, pool_rows(pool_for_agent), direction,
                              remaining, pool=pool_for_agent)
        content, usage = "", None
        try:
            content, usage = call_llm(prompt, agent)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [{agent}] LLM 调用失败: {exc}")
        decisions = parse_decision(content)
        if decisions is None:
            print(f"⚠️ [{agent}] 决策解析失败，跳过（LLM 原文前 200 字）："
                  f"{content[:200]!r}")
            continue
        if not decisions:
            print(f"⏭️ [{agent}] 空决策（合法 no-op，本轮不动）")
            continue
        # 决策展示 + 校验
        sells, buys, summary = [], [], [f"（模型自主调仓决策，{now_cn():%F %T}）"]
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
                raw_vol = int(avail * min(max(d["pct"], 0), 1))
                vol = round_sell_qty(code, raw_vol, avail)
                if vol <= 0:
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 比例 {d['pct']} 无合法可卖量，跳过")
                    continue
                if vol != raw_vol:
                    print(f"  ⚖️ [{agent}] 卖出 {code}: 意图 {raw_vol} 股 → 手数合规实际 {vol} 股"
                          f"（{board_of(code)} 口径）")
                if at_limit_down(code, h['day_chg'], h.get('name')):
                    print(f"  ⏭️ [{agent}] 卖出 {code}: 跌停（{h['day_chg']:+.2f}%），不接")
                    continue
                sells.append((code, vol, d["reason"]))
                print(f"  📉 [{agent}] 卖出 {code} {vol}/{avail}股 "
                      f"({d['pct']:.0%}): {d['reason']}")
            elif d["action"] == "buy":
                if code in pending_buy:
                    print(f"  ⏭️ [{agent}] 买入 {code}: 已有在途买单未确认，跳过")
                    continue
                bd = check_buy(code, d["pct"], bool(h), new_buys, opened_today, gate,
                               name=(h or {}).get("name") or nm_by_code.get(code))
                if not bd.ok:
                    print(f"  ⏭️ [{agent}] 买入 {code}: {bd.reason}，跳过")
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
        from agent_tools.datasources import tdx_aidata

        try:
            bars_map = tdx_aidata.get_klines_batch(
                [c for c, _, _ in sells + buys], interval="daily", count=5)
        except Exception:  # noqa: BLE001
            bars_map = {}
        for code, vol, _ in sells:
            if code in pending_sell:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 执行前已有在途卖单未确认，本轮不下单")
                continue
            bars = bars_map.get(code) or broker.get_klines(code, interval="daily")[-5:]
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
                result = broker.sell(None, None, code, vol, price=round(price * 0.99, 2))
                from live_fills import ack_line

                print("  " + ack_line(f"[{agent}] 卖出 {code}", result))
                fill = wait_fill(broker, result.get("order_id", ""))
                if fill and int(fill.get("filled_volume") or 0) > 0:
                    fv = int(fill["filled_volume"])
                    fp = float(fill.get("filled_price") or round(price * 0.99, 2))
                    fp, approx = _quote_guarded_price(broker, code, fp)
                    if approx:
                        print(f"  ⚠️ [{agent}] 卖出 {code}: 桥回报成交价偏离实时价>40%，"
                              f"按实时价 {fp} 记账（approx）")
                    ledger = load_ledger()
                    cost_p = float((((ledger.get("agents") or {}).get(agent) or {})
                                    .get("positions") or {}).get(code, {}).get("cost_price") or 0)
                    ledger = record_sell(ledger, agent, code, fv, fp,
                                         now_cn().isoformat())
                    save_ledger(ledger)
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "sell",
                              "volume": fv, "price": fp, "cost_price": cost_p,
                              "approx": approx,
                              "fill": {"order_id": fill.get("order_id"),
                                       "filled_price": fp, "filled_volume": fv}})
                else:
                    add_pending(result.get("order_id"), agent, code, "sell", vol,
                                round(price * 0.99, 2), now_cn().isoformat())
                    pending_sell.add(code)   # 同轮重复决策同一代码时不再下第二单
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "sell",
                              "volume": vol, "price": round(price * 0.99, 2),
                              "pending": True, "result": result})
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
        for code, pct, _ in buys:
            if code in pending_buy:
                print(f"  ⏭️ [{agent}] 买入 {code}: 执行前已有在途买单未确认，本轮不下单")
                continue
            remaining = agent_remaining(ledger, agent)
            bars = bars_map.get(code) or broker.get_klines(code, interval="daily")[-5:]
            o = compute_order(bars, remaining, pct, code)
            if not o["ok"]:
                print(f"  ⏭️ [{agent}] 买入 {code}: {o['reason']}")
                continue
            _opx, bad_kl = _quote_guarded_price(broker, code, o["price"])
            if bad_kl:
                print(f"  ⏭️ [{agent}] 买入 {code}: K线价 {o['price']} 与实时价偏差>40%"
                      f"（脏数据），跳过防超量下单（2026-09-08 福恩股份事故）")
                continue
            # 分账额度红线：子 agent 买入不能超自己 ¥10 万虚拟子账户的现金
            # （remaining 是额度口径、cash 是桥总账户真实现金，都拦不住已实现
            #   亏损造成的透支——虚拟现金才是子账户真正买得起的钱）
            vcash = agent_virtual_cash(ledger, agent)
            if o["cost"] > vcash:
                print(f"  ⏭️ [{agent}] 买入 {code}: 子账户虚拟现金不足 "
                      f"¥{o['cost']:,.0f} > ¥{vcash:,.0f}（分账额度不透支，跳过）")
                continue
            # 杠杆硬约束：加仓后持仓成本 ≤ 权益×1.5（现金为负时才可能超）
            pos_cost = agent_used(ledger, agent)
            equity = vcash + pos_cost
            if equity > 0 and (pos_cost + o["cost"]) > LEVERAGE_MAX * equity:
                print(f"  ⏭️ [{agent}] 买入 {code}: 加仓后杠杆超 {LEVERAGE_MAX}×权益"
                      f"（{pos_cost + o['cost']:,.0f} > {LEVERAGE_MAX * equity:,.0f}），跳过")
                continue
            if o["cost"] > cash:
                print(f"  ⏭️ [{agent}] 买入 {code}: 账户现金不足 ¥{o['cost']:,.0f} > ¥{cash:,.0f}")
                continue
            cash -= o["cost"]
            try:
                result = broker.buy(None, None, code, o["volume"], price=o["limit_price"])
                from live_fills import ack_line

                print("  " + ack_line(f"[{agent}] 买入 {code} {o['volume']}股 "
                                      f"限价 ¥{o['limit_price']:.2f}", result))
                fill = wait_fill(broker, result.get("order_id", ""))
                if fill and int(fill.get("filled_volume") or 0) > 0:
                    fv = int(fill["filled_volume"])
                    fp = float(fill.get("filled_price") or o["price"])
                    fp, approx = _quote_guarded_price(broker, code, fp)
                    if approx:
                        print(f"  ⚠️ [{agent}] 买入 {code}: 桥回报成交价偏离实时价>40%，"
                              f"按实时价 {fp} 记账（approx）")
                    ledger = record_buy(load_ledger(), agent, code, fv, fp,
                                        now_cn().isoformat())
                    save_ledger(ledger)
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "buy",
                              "volume": fv, "price": fp, "approx": approx,
                              "fill": {"order_id": fill.get("order_id"),
                                       "filled_price": fp, "filled_volume": fv}})
                else:
                    add_pending(result.get("order_id"), agent, code, "buy",
                                o["volume"], o["price"], now_cn().isoformat())
                    pending_buy.add(code)    # 同轮重复决策同一代码时不再下第二单
                    log_line({"ts": now_cn().isoformat(), "mode": "execute",
                              "agent": agent, "code": code, "side": "buy",
                              "volume": o["volume"], "price": o["price"],
                              "pending": True, "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
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
