#!/usr/bin/env python3
"""分钟级价格哨兵：执行 LLM 盘中分析挂的 watch 条件位（跌破止损 / 到位止盈）。

背景：整点分析每小时才一轮，「跌破 ¥120 就减仓」这类条件位在两次整点之间
没有人盯。本脚本由 cron 每分钟拉起，闭环是：

  live_hourly_analysis 解析 LLM 决策中的 watch 项
    → data/live_watch.json（每 agent 每小时整组刷新，最新分析说了算）
    → 本脚本查桥日K最新价（交易时段日K最后一根=当日实时价）
    → 现价 ≤ stop_loss 减仓 pct 比例 / ≥ take_profit 止盈 pct 比例
    → 与盘中执行同一套卖出闸门（T+1 可卖量、跌停不接、100 股整数倍）
    → record_sell 分账记账 + logs/live_watch_YYYYMMDD.jsonl
    → 触发并执行后该条规则即消费（一次性）；T+1 不可卖 / 跌停卖不出
      / 下单失败则保留规则，下一分钟或次日继续守

用法:
  python scripts/live_price_watch.py             # 交易时段才执行（cron 每分钟）
  python scripts/live_price_watch.py --force     # 忽略时段检查（调试）
  python scripts/live_price_watch.py --dry-run   # 触发只打印，不下单
  python scripts/live_price_watch.py --status    # 查看当前挂着的条件位

cron（本机 JST，北京=JST-1）: * 10-12,14-16 * * 1-5
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_cal import is_trading_day, why_not  # noqa: E402
from live_fills import round_sell_qty  # noqa: E402
from ashare_rules import at_limit_down, after_hours_eligible, board_of, after_hours_window  # noqa: E402

WATCH_FILE = ROOT / "data" / "live_watch.json"
LOG_DIR = ROOT / "logs"
SKIP_STATE_FILE = LOG_DIR / "live_watch_notify_state.json"  # 账户级提醒的跨进程去重（见 _notify_once）
SELL_LIMIT_DOWN = -9.9   # 跌停不接（与 live_hourly_analysis 同口径）
POLL_SLEEP_SEC = 1       # 桥限流：每条规则之间隔 1 秒
SKIP_NOTIFY_HOUR = True  # 同一规则同一小时的重复跳过只提醒一次（防刷屏）


def _load_dotenv() -> None:
    """加载项目 .env（仅补缺省环境变量，不打印任何值）——桥地址/token 在这里。"""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"')


_load_dotenv()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def in_window(now: datetime) -> bool:
    """A股交易时段（北京）：9:30-11:30 / 13:00-15:00 交易日（日历感知，节假日休市不动作）。"""
    if not is_trading_day(now.date()):
        return False
    hm = now.hour * 100 + now.minute
    return 930 <= hm <= 1130 or 1300 <= hm <= 1500


# ---------- watch 规则文件（本模块是 data/live_watch.json 的唯一属主） ----------

def load_watch() -> dict:
    """{"agent名": [{code, stop_loss, take_profit, pct, reason, created_ts}]}"""
    try:
        data = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_watch(rules: dict) -> None:
    """原子写：先写 tmp 再 rename，避免哨兵与整点分析并发读到半写文件。"""
    WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH_FILE.with_name(WATCH_FILE.name + ".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(WATCH_FILE)


def save_watch_rules(agent: str, decisions: list) -> int:
    """整点分析落 watch 条件位：该 agent 的旧规则整组替换（最新分析说了算），
    本轮无 watch 决策则清空该 agent 全部旧规则。返回落盘规则数。"""
    rules = {a: v for a, v in load_watch().items() if a != agent}
    mine = []
    for d in decisions or []:
        if d.get("action") != "watch":
            continue
        code = str(d.get("code") or "").strip()
        if not code or (d.get("stop_loss") is None and d.get("take_profit") is None):
            continue  # 没有价位的条件位没有意义
        mine.append({
            "code": code,
            "stop_loss": d.get("stop_loss"),
            "take_profit": d.get("take_profit"),
            "move_stop": d.get("move_stop"),
            "pct": min(max(float(d.get("pct") or 1.0), 0.0), 1.0),
            "reason": str(d.get("reason") or ""),
            "created_ts": now_cn().isoformat(),
        })
    if mine:
        rules[agent] = mine
    save_watch(rules)
    return len(mine)


# ---------- 行情与执行 ----------

def _last_price(broker, code: str):
    """桥日K 最后两根 → (现价, 昨收)。交易时段日K最后一根的 close 即当日实时价。"""
    try:
        bars = broker.get_klines(code, interval="daily")[-2:]
    except Exception:  # noqa: BLE001
        return None, None
    if len(bars) < 2:
        return None, None
    try:
        price = float(bars[-1].get("close") or 0)
        prev = float(bars[-2].get("close") or 0)
    except (TypeError, ValueError):
        return None, None
    if price <= 0 or prev <= 0:
        return None, None
    return price, prev


def _log_line(rec: dict) -> None:
    """哨兵动作日志 → logs/live_watch_YYYYMMDD.jsonl（按北京日期滚动）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"live_watch_{now_cn():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _notify_once(key: str, msg: str) -> None:
    """账户级提醒的小时级去重。

    哨兵每分钟一个进程，模块内变量留不住；规则级去重（_notify_skip）写在规则对象里，
    而账户快照退化时整轮不动规则文件 → 需要个跨进程的边车文件。"""
    tag = f"{now_cn():%Y%m%d%H}"
    try:
        state = json.loads(SKIP_STATE_FILE.read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if state.get(key) == tag:
        return
    state[key] = tag
    try:
        SKIP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    print(f"  {msg}")


def _degenerate_reason(acct: dict) -> str:
    """账户快照是否退化（桥假活：行情通道正常、交易账号掉线）。返回原因，正常为 ""。

    判据与 live_llm_trade._query_account_with_retry 同口径：资产 <= 0 即不可信。
    2026-09-10 实录：整日 asset=0、positions=[]，哨兵把真实持仓的止损位当"已清仓"
    销毁（3 条真实条件位就是这样没的），而行情侧一切正常、零告警。"""
    asset = (acct.get("asset") or {}).get("asset")
    if asset is None:
        return ""
    try:
        val = float(asset)
    except (TypeError, ValueError):
        return f"资产字段异常（{asset!r}）"
    return f"account/query 返回资产 {val:,.0f}" if val <= 0 else ""


def _ledger_holds(agent: str, code: str) -> bool:
    """分账台账里该 agent 是否仍持有 code。

    读不到台账 / 台账没有该 agent → 保守返回 True：宁可多守一轮条件位，
    也不能因为台账故障把真实持仓的止损位判成"已清仓"作废。"""
    try:
        import live_ledger

        agents = live_ledger.load_ledger().get("agents") or {}
        if agent not in agents:
            return True
        return code in ((agents[agent] or {}).get("positions") or {})
    except Exception:  # noqa: BLE001
        return True


# 方向线索词：『涨到/站上』类 = 到位止盈，『跌破/失守』类 = 破位止损。
# 同步侧（post_review.sync_plan_to_watch）与本文件的运行时护栏共用这一份词表（DRY）。
UP_HINTS = ("涨到", "涨至", "站上", "站稳", "突破", "上破", "冲高", "回升",
            "上沿", "止盈", "锁利", "兑现")
DOWN_HINTS = ("跌破", "失守", "破位", "下破", "下探", "回落到", "回落至",
              "下沿", "止损", "防守", "支撑", "清仓")


def classify_watch_direction(txt: str) -> tuple[str | None, str]:
    """文字里的方向线索 → ("take_profit"|"stop_loss"|None, 命中词)。

    取**最先出现**的方向词：「涨到17.90减50%，避免再次跌破18.10」里先说的算数。"""
    best: tuple[int, str, str] | None = None
    for kind, hints in (("take_profit", UP_HINTS), ("stop_loss", DOWN_HINTS)):
        for kw in hints:
            i = txt.find(kw)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, kind, kw)
    return (best[1], best[2]) if best else (None, "")


def _fix_rule_direction(agent: str, rule: dict, price: float, prev: float) -> None:
    """方向自洽护栏：止损该在下方等下跌打到、止盈该在上方等上涨打到。

    2026-09-10 实录：复盘同步把『涨到17.90减50%』写成 stop_loss，现价 17.50 在阈值
    下方 → `price <= stop_loss` 成立，开盘即反向假触发（09-09 的 18.10/80.00 同款；
    当日三条真实条件位还被空快照销毁，见 run_watch 的桥假活硬闸）。

    标错判定（按优先级）：
      1) reason 文字线索（『涨到/站上/止盈』vs『跌破/失守/止损』）与存档方向不符；
      2) 无文字线索时看昨收：止损高于昨收且现价还在昨收上方（涨着却挂了上方止损）
         = 标错；若现价已跌到昨收下方，则可能是隔夜跳空打穿的真止损 → 不动。
    翻转按触发语义做（该涨到位卖的改记止盈、该跌到位卖的改记止损），日志留痕；
    move_stop 上移而来的止损是浮盈锁利（可高于昨收），豁免。"""
    sl, tp = rule.get("stop_loss"), rule.get("take_profit")
    try:
        sl_f = float(sl) if sl is not None else None
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        return
    hint, kw = classify_watch_direction(str(rule.get("reason") or ""))
    if sl_f is not None:
        why = _mislabel_reason("stop_loss", sl_f, price, prev, hint, kw)
        if why:
            if tp_f is None:
                rule["stop_loss"], rule["take_profit"] = None, sl_f
                why += " → 按止盈执行"
            else:
                rule["stop_loss"] = None
                why += " → 丢弃该侧"
            _notify_skip(rule, f"🔧 [{agent}] {rule['code']}: {why}")
    if tp_f is not None:
        why = _mislabel_reason("take_profit", tp_f, price, prev, hint, kw)
        if why:
            if rule.get("stop_loss") is None:
                rule["stop_loss"], rule["take_profit"] = tp_f, None
                why += " → 按止损执行"
            else:
                rule["take_profit"] = None
                why += " → 丢弃该侧"
            _notify_skip(rule, f"🔧 [{agent}] {rule['code']}: {why}")


def _mislabel_reason(kind: str, level: float, price: float, prev: float,
                     hint: str | None, kw: str) -> str:
    """该条件位是否方向标错 → 原因文案；没问题返回 ""。"""
    label = "止损" if kind == "stop_loss" else "止盈"
    if hint and hint != kind:
        return f"{label} ¥{level:.2f} 与理由文字『{kw}』方向不符（方向标错）"
    if hint:
        return ""
    if kind == "stop_loss" and level > prev and price > prev:
        return f"止损 ¥{level:.2f} 高于昨收 ¥{prev:.2f} 且现价仍在昨收上方（方向标错）"
    if kind == "take_profit" and level < prev and price < prev:
        return f"止盈 ¥{level:.2f} 低于昨收 ¥{prev:.2f} 且现价仍在昨收下方（方向标错）"
    return ""


def _execute_sell(broker, agent: str, rule: dict, price: float, prev: float,
                  trig: str, avail, dry_run: bool = False,
                  after_hours: bool = False) -> bool:
    """条件位触发 → 卖出（与盘中执行同一套闸门）。
    返回 True=规则已消费（已执行/作废），False=保留规则下次再守。"""
    code = rule["code"]
    if avail is None:
        # 2026-09-10 实录：桥假活时 account/query 的 positions 为空，这里会把真实持仓的
        # 止损位当"已清仓"销毁。先与分账台账交叉核对：台账仍持有 → 保留条件位。
        if _ledger_holds(agent, code):
            _notify_skip(rule, f"⏭️ [{agent}] {code}: 账户快照无此持仓但台账仍持有，"
                               f"疑似账号掉线——条件位保留，勿当已清仓")
            return False
        print(f"  🗑️ [{agent}] {code}: 已不在持仓中，条件位作废")
        return True
    if avail <= 0:
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 可卖量 0（T+1 当日买入），条件位保留待明日")
        return False
    chg = (price - prev) / prev * 100
    if at_limit_down(code, chg):
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 跌停（{chg:+.2f}%）卖不出，条件位保留")
        return False
    raw_qty = int(avail * min(max(rule.get("pct", 1.0), 0.0), 1.0))
    vol = round_sell_qty(code, raw_qty, avail)
    if vol <= 0:
        print(f"  🗑️ [{agent}] {code}: 可卖量 {avail} 股按 {rule.get('pct', 1.0):.0%}"
              f"无合法可卖量（板块手数口径），条件位作废")
        return True
    # 盘后固定价格交易以收盘价撮合 → 申报价=收盘价；盘中限价卖=现价-1%
    limit = price if after_hours else round(price * 0.99, 2)
    label = "跌破止损" if trig == "stop_loss" else "达到止盈"
    if dry_run:
        # 只报不卖 —— 规则**不消费**（返回 False）。曾返回 True 当"已消费"，
        # 结果是开关关闭期间触发的止损被静默吃掉（2026-09-11 600309 实录）。
        # 调用方 run_watch 已在 dry-run 时提前分流，这里是二次兜底。
        print(f"  🟡 DRY-RUN 卖出 {code} {vol}/{avail} 股 限价 ¥{limit:.2f}（{label}）——"
              f"条件位保留")
        return False
    try:
        result = broker.sell(None, None, code, vol, price=limit)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
        _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                   "code": code, "volume": vol, "price": limit,
                   "trigger": rule.get(trig), "error": str(exc)})
        return False  # 下次重试
    print(f"  ✅ [{agent}] 卖出 {code} {vol} 股 限价 ¥{limit:.2f} 已受理: {result}")
    if vol != raw_qty:
        print(f"  ⚖️ [{agent}] {code}: 意图 {raw_qty} 股 → 手数合规实际 {vol} 股"
              f"（{board_of(code)} 口径）")
    # 成交记账走 pending/reconcile 通道：按真实成交价回填 + fill_confirm 进交易流水
    # （2026-09-04 复盘：此前直接按限价记账，fill 回报缺失、审计/复盘漏掉哨兵成交）
    from live_fills import add_pending

    add_pending(result.get("order_id"), agent, code, "sell", vol, limit,
                now_cn().isoformat())
    _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
               "code": code, "volume": vol, "intent_volume": raw_qty, "price": limit,
               "trigger": rule.get(trig), "pct": rule.get("pct"),
               "reason": rule.get("reason"), "result": result})
    return True


def _notify_skip(rule: dict, msg: str) -> None:
    """同一规则同一小时只提醒一次跳过原因（T+1/跌停每分钟都会撞上，防刷屏）。"""
    hour_tag = f"{now_cn():%Y%m%d%H}"
    if rule.get("_skip_notified") == hour_tag:
        return
    rule["_skip_notified"] = hour_tag
    print(f"  {msg}")


def in_close_auction(now) -> bool:
    """收盘集合竞价（北京 14:57-15:00）不触发实单：竞价撮合规则不同，
    哨兵限价单语义失效；条件保留至盘后定价窗口或明日盘中。"""
    hm = now.hour * 60 + now.minute
    return 14 * 60 + 57 <= hm < 15 * 60


def run_watch(broker, dry_run: bool = False, after_hours: bool = False, now=None) -> int:
    """轮询全部条件位，返回本轮触发笔数。

    now：本轮基准时刻（北京时区），用于收盘集合竞价护栏；缺省取当前时间。
    """
    from live_fills import reconcile

    now = now or now_cn()

    try:
        reconcile(broker)  # 先补记在途成交，再守条件位
    except Exception:  # noqa: BLE001
        pass
    # 总开关联动：配置关掉自动执行时，哨兵也只打印不真卖（防验证测试误触实盘）
    from live_hourly_analysis import intraday_exec_enabled

    if not dry_run and not intraday_exec_enabled():
        print("  🔒 自动执行开关已关（intraday_exec.json），哨兵只打印不真卖")
        dry_run = True
    rules = load_watch()
    if not rules:
        return 0
    try:
        acct = broker._account_query()
    except Exception as exc:  # noqa: BLE001  桥重启窗口/断线：本轮放弃，下分钟再守
        print(f"  ⚠️ 账户查询失败，本轮哨兵跳过（{str(exc)[:80]}）")
        return 0
    # 桥假活硬闸（2026-09-10）：HTTP 通、行情正常，但账户通道返空（交易账号未登录），
    # 空快照下每条真实规则都会被 _execute_sell 判成"已不在持仓中"销毁。
    deg = _degenerate_reason(acct)
    if deg:
        _notify_once("account_degenerate",
                     f"🚫 账户快照不可信（{deg}），本轮哨兵跳过、条件位全部保留——"
                     f"行情能看但下单/持仓查询哑火，需 RDP 重登 Windows 交易机通达信")
        return 0
    positions = acct.get("positions") or []
    avail_map = {p.get("stock_code"): int(p.get("available_volume") or 0)
                 for p in positions}
    fired = 0
    for agent in list(rules):
        kept = []
        for r in rules[agent]:
            price, prev = _last_price(broker, r["code"])
            if price is None:
                _notify_skip(r, f"⚠️ [{agent}] {r['code']} 行情获取失败，条件位保留")
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            # 方向自洽：先按文字线索/昨收校正标错的止损止盈，再谈触发
            # （move_stop 上移过的止损可以高于昨收，故豁免）
            if not r.get("stop_from_move"):
                _fix_rule_direction(agent, r, price, prev)
            # move_stop：价格触及后止损一次性上移到该价（只上不下，跟踪保护）
            ms = r.get("move_stop")
            if ms:
                try:
                    ms = float(ms)
                except (TypeError, ValueError):
                    ms = None
            if ms and price >= ms and (r.get("stop_loss") or 0) < ms:
                print(f"  🔒 [{agent}] {r['code']} 触及 move_stop ¥{ms:.2f}，"
                      f"止损上移 ¥{r.get('stop_loss') or 0:.2f} → ¥{ms:.2f}")
                r["stop_loss"] = ms
                r["stop_from_move"] = True   # 浮盈锁利位，可高于昨收，别再被当方向标错
                r.pop("move_stop", None)
            if after_hours and not after_hours_eligible(r["code"]):
                kept.append(r)  # 主板等无盘后定价的标的，条件位保留至次日盘中
                continue
            trig = None
            if r.get("stop_loss") and price <= r["stop_loss"]:
                trig = "stop_loss"
            elif r.get("take_profit") and price >= r["take_profit"]:
                trig = "take_profit"
            if not trig:
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            label = "跌破止损" if trig == "stop_loss" else "达到止盈"
            notice = (f"  🎯 [{agent}] {r['code']} 现价 ¥{price:.2f} {label}位 "
                      f"¥{r[trig]:.2f}（减仓 {r.get('pct', 1.0):.0%}）")
            if dry_run:
                # 只报不卖（执行开关未开 / --dry-run）→ 条件位必须保留、不消费。
                # 2026-09-11 实录：600309 的 75.50 止损在开关关闭时于 09:44 触发，
                # dry-run 分支照样按「已消费」上报 → 规则被吃掉、当天再无人守，
                # 持仓裸奔（价格 74.41 → 73.85 无保护）。提醒按规则整点去重防刷屏。
                _notify_skip(r, f"{notice} —— 执行开关未开/试运行，只报不卖、"
                                f"条件位保留: {r.get('reason', '')}")
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            print(f"{notice}: {r.get('reason', '')}")
            if in_close_auction(now):
                print(f"  ⏭️ [{agent}] {r['code']} 收盘集合竞价时段（14:57-15:00），"
                      f"不触发实单，条件保留")
                kept.append(r)
                continue
            if _execute_sell(broker, agent, r, price, prev, trig,
                             avail_map.get(r["code"]), dry_run=dry_run,
                             after_hours=after_hours):
                fired += 1  # 触发并已消费
            else:
                kept.append(r)
            time.sleep(POLL_SLEEP_SEC)
        if kept:
            rules[agent] = kept
        else:
            rules.pop(agent, None)
    save_watch(rules)
    return fired


def print_status() -> None:
    rules = load_watch()
    if not rules:
        print("📭 无 watch 条件位（等整点分析挂入）")
        return
    for agent, rs in rules.items():
        for r in rs:
            sl = f"止损 ¥{r['stop_loss']:.2f}" if r.get("stop_loss") else "止损 —"
            tp = f"止盈 ¥{r['take_profit']:.2f}" if r.get("take_profit") else "止盈 —"
            print(f"[{agent}] {r['code']}: {sl} / {tp}"
                  f"（触发卖出 {r.get('pct', 1.0):.0%}）{r.get('reason', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="分钟级价格哨兵：watch 条件位触发卖出")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--dry-run", action="store_true", help="触发只打印，不下单")
    parser.add_argument("--status", action="store_true", help="查看当前挂着的条件位")
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    now = now_cn()
    from live_hourly_analysis import after_hours_exec_enabled

    # 盘后固定价格交易窗口（科创板/创业板 15:05-15:30）：哨兵继续值守，
    # 触发的卖出以收盘价撮合（仅支持盘后定价的板块标的）
    ah = (not args.force) and after_hours_exec_enabled() and after_hours_window(now)
    if not args.force and not in_window(now) and not ah:
        return 0  # 非交易时段静默退出（cron 每分钟跑，不刷屏）
    if not load_watch():
        return 0  # 无条件位，零开销退出（连桥都不碰）

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    broker = TdxBridgeBroker()
    fired = run_watch(broker, dry_run=args.dry_run, after_hours=ah, now=now)
    if fired:
        print(f"[{now:%F %T}] ⚠️ 条件位触发 {fired} 笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
