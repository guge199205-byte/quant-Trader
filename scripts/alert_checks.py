#!/usr/bin/env python3
"""alert.sh 用的判定函数（**只用标准库**）。

alert.sh 调的是系统 `/usr/bin/python3`（不是项目 venv），不能 import 本项目模块，
所以判定逻辑单独放这里，而不是继续往 alert.sh 里塞内联 heredoc——内联的东西
没法 pytest，出过一次「逻辑对但阈值/窗口写错」就只能靠线上误报发现。

每个函数都接收 `now` 参数（便于测试），CLI 里取当前北京时间。

CLI（alert.sh 调用）：
  /usr/bin/python3 scripts/alert_checks.py llm_tier        logs/live_llm_trade_state.json
  /usr/bin/python3 scripts/alert_checks.py news_stale      data/news_brief/latest.json [running]
  /usr/bin/python3 scripts/alert_checks.py folder_failures data/news_brief/state.json
  /usr/bin/python3 scripts/alert_checks.py rt_down        logs/rt_status.json
  /usr/bin/python3 scripts/alert_checks.py account_down   logs/rt_status.json
  /usr/bin/python3 scripts/alert_checks.py l2_stale        data/l2_factors_live.json
  /usr/bin/python3 scripts/alert_checks.py pending_stuck   data/live_pending_orders.json
  /usr/bin/python3 scripts/alert_checks.py order_events    data/live_order_events.json

有告警 → stdout 一行文本；无告警 → 不输出，退出码 0（alert.sh 按空串判定）。
`rt_down` 例外：固定输出两行（立即报 / 按天报），调用方按行取。
`pending_stuck` 也固定一行「常规数|挂队数」；`order_events` 每行「事件id|文案」。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ = timezone(timedelta(hours=8))

# 新闻停更：盘中每小时一期，>75 分钟无新刊判停更
NEWS_STALE_MIN = 75
NEWS_MORNING_START = 10 * 60 + 45     # 早盘前两刊（09:25/09:55）未齐，不判定
NEWS_AFTERNOON_START = 13 * 60 + 20   # 下午首判 13:20：12:55 那期实测 13:08 才落盘，
                                      # 13:00/13:05 检查到的是 11:04 旧刊 → 误报 115/120 分钟
NEWS_MORNING_END = 11 * 60 + 30
NEWS_AFTERNOON_END = 15 * 60

# 09:35 主入口执行状态
LLM_T1_START = 10 * 60                # 10:00 起判「主入口未收尾」
LLM_T2_START = 11 * 60 + 10           # 11:10 起判「两班补跑均未成功」
LLM_END = 15 * 60

FOLDER_FAIL_MIN = 3                   # 抓取桶失败 ≥3 才值得打扰

# 行情源降级（rt_probe 每 5 分钟写 logs/rt_status.json）
RT_SOURCES = ("bridge", "fuyao", "aidata")
RT_DEBOUNCE = {"fuyao": 2}            # 抖动源：连续失败 ≥2 次才报（一天十几次单点抖动）
RT_BYDAY = ("aidata",)                # 静态文件存在性检查：缺失即长期缺失，去抖无效 → 按天

# 账户通道（rt_probe 同文件写 account 段）：掉线不会自愈，需人工重登交易端；
# 只在可行动的窗口内报（北京 08:45 开盘前 ~ 15:35 收盘后），否则半夜也会刷。
ACCT_DEBOUNCE = 2
ACCT_ALERT_START = 8 * 60 + 45
ACCT_ALERT_END = 15 * 60 + 35

# L2 快照新鲜度（live_l2_capture 交易日每 5 分钟写 data/l2_factors_live.json）。
# 它是「候选池实时价注入」和「按资金量裁剪候选池」的唯一数据源：停更则两条链路
# 同时静默失效（模型拿不到池内价 → 只能 hold 或凭评分瞎报），此前零监控。
# 判定窗口避开两个天然空档：开盘首写（09:30 起）与午休 11:30→13:00（90 分钟）。
L2_STALE_MIN = 20
L2_MORNING_START = 9 * 60 + 50        # 开盘 20 分钟后才够样本
L2_MORNING_END = 11 * 60 + 30
L2_AFTERNOON_START = 13 * 60 + 15     # 午休空档期不判，13:15 起判（13:00 首写需落盘时间）
L2_AFTERNOON_END = 15 * 60

# 挂单停滞（data/live_pending_orders.json）：盘中在途委托 >10 分钟无回报。
# protect=True（止损报跌停保护价、跌停封死挂队）是「等买盘」的正常形态 →
# 只告警、不投重启信号；常规停滞沿用重启桥自愈。
STUCK_SEC = 600
STUCK_MORNING = (9 * 60 + 30, 11 * 60 + 30)
STUCK_AFTERNOON = (13 * 60, 15 * 60)

TRADING_DAYS_JSON = Path(__file__).resolve().parents[1] / "configs" / "trading_days.json"


def is_trading_day(now: datetime, path: Path | str | None = None) -> bool:
    """交易日判定（读 configs/trading_days.json，标准库解析，不 import trading_cal）。

    文件缺失/当日不在覆盖区间（跨年未刷新）→ 退化为周一~周五，与 alert.sh 其他段一致。
    """
    try:
        days = json.loads(Path(path or TRADING_DAYS_JSON).read_text(encoding="utf-8"))["days"]
        days = [str(x) for x in days]
    except (OSError, ValueError, TypeError, KeyError):
        return now.weekday() < 5
    today = now.strftime("%Y%m%d")
    if not days or today > max(days):
        return now.weekday() < 5
    return today in set(days)


def l2_stale_line(now: datetime, mtime: float | None,
                  trading_day: bool | None = None) -> str | None:
    """盘中 L2 快照停更说明（"停更 N 分钟" / "文件缺失"）；不该判定或正常时返回 None。

    trading_day=None 时按周一~周五判（调用方一般传 is_trading_day(now)，节假日不误报）。
    """
    if trading_day is None:
        trading_day = now.weekday() < 5
    if not trading_day:
        return None
    m = now.hour * 60 + now.minute
    in_morning = L2_MORNING_START <= m < L2_MORNING_END
    in_afternoon = L2_AFTERNOON_START <= m < L2_AFTERNOON_END
    if not (in_morning or in_afternoon):
        return None
    if mtime is None:
        return "文件缺失"
    age = (now.timestamp() - mtime) / 60
    return f"停更 {int(age)} 分钟" if age > L2_STALE_MIN else None


def rt_down(doc: dict | None) -> tuple[str, str]:
    """行情源降级 → (立即报的源, 按天去重报的源)，均为逗号分隔描述串。

    `fail_streak` 由 rt_probe 写；老格式（无该字段）按 1 次算，不改变原有立即报行为。
    """
    d = doc if isinstance(doc, dict) else {}
    immediate: list[str] = []
    byday: list[str] = []
    for k in RT_SOURCES:
        v = d.get(k)
        if not isinstance(v, dict) or v.get("ok"):
            continue          # 缺该路（老看板/探针停更）不报——停更由别的检查兜
        streak = int(v.get("fail_streak") or 1)
        if streak < RT_DEBOUNCE.get(k, 1):
            continue
        ts = str(v.get("first_fail_ts") or "")[11:16]
        label = f"{k}(连续{streak}次{'自' + ts if ts else ''})"
        (byday if k in RT_BYDAY else immediate).append(label)
    return ",".join(immediate), ",".join(byday)


def account_down(doc: dict | None, now: datetime,
                 debounce: int = ACCT_DEBOUNCE) -> str | None:
    """账户通道（桥 account/query）掉线 → 一行说明，否则 None。

    2026-09-10 实录：行情通道全通（快照/K线正常）而账户通道整日 asset=0、
    positions=[]——交易账号掉线。当日 7 轮盘中分析 + 3 次 09:35 调仓 + 全部哨兵
    条件位哑火，看板只探行情 → 零告警。该故障不自愈（要人工重登交易端），
    故只在可行动的窗口内报（北京 08:45 起、收盘后 15:35 止），并按连续 ≥2 次
    去抖滤掉桥重启窗口的单点抖动；同一天只在首次出现时报（调用方按天去重）。
    """
    v = (doc or {}).get("account")
    if not isinstance(v, dict) or v.get("ok"):
        return None
    streak = int(v.get("fail_streak") or 1)
    if streak < debounce:
        return None
    if not is_trading_day(now):
        return None
    m = now.hour * 60 + now.minute
    if not (ACCT_ALERT_START <= m < ACCT_ALERT_END):
        return None
    ts = str(v.get("first_fail_ts") or "")[11:16]
    err = str(v.get("error") or "").strip()
    return f"连续{streak}次{'自' + ts if ts else ''}" + (f"：{err[:90]}" if err else "")


def news_stale_minutes(now: datetime, mtime: float | None,
                       running: bool = False) -> int | None:
    """盘中新闻停更分钟数；不该判定时返回 None。

    running=True（news_brief 进程正在跑）时不判定——覆盖手动补跑与超时晚落盘。
    """
    if running or now.weekday() >= 5:
        return None
    m = now.hour * 60 + now.minute
    in_morning = NEWS_MORNING_START <= m < NEWS_MORNING_END
    in_afternoon = NEWS_AFTERNOON_START <= m < NEWS_AFTERNOON_END
    if not (in_morning or in_afternoon):
        return None
    if mtime is None:
        return None
    age = (now.timestamp() - mtime) / 60
    return int(age) if age > NEWS_STALE_MIN else None


def llm_trade_tier(now: datetime, doc: dict | None) -> str | None:
    """09:35 主入口当日执行状态 → "t1|原因" / "t2|原因" / None。

    t1 = 主入口未正常收尾（需人工确认是否已下单）；t2 = 两班补跑（10:05/11:05）也未成功。
    `ok is True` 才算完成——`ok: null`（启动了但没走完）与缺文件都算异常。
    """
    if now.weekday() >= 5:
        return None
    m = now.hour * 60 + now.minute
    if not (LLM_T1_START <= m < LLM_END):
        return None
    d = doc if isinstance(doc, dict) else {}
    today = now.date().isoformat()
    same_day = str(d.get("day") or "") == today
    if same_day and d.get("ok") is True:
        return None
    note = str(d.get("note") or "无说明")
    if not same_day:
        why = "当日无执行记录（09:35 未运行或未落状态）"
    elif d.get("orders_attempted"):
        why = f"已下过单但未正常收尾（{note}）"
    else:
        why = f"下单前失败、一单未动（{note}）"
    return ("t2|" if m >= LLM_T2_START else "t1|") + why


def folder_failures(doc: dict | None) -> list[tuple[int, str]]:
    """news_brief state.last_run.folder_failures → [(folder_id, err)]。

    落库形状：`fails.append((fid, str(exc)[:100]))`，JSON 序列化后是二元列表。
    """
    lr = (doc or {}).get("last_run") or {}
    out: list[tuple[int, str]] = []
    for x in lr.get("folder_failures") or []:
        if isinstance(x, (list, tuple)) and len(x) >= 2:
            try:
                out.append((int(x[0]), str(x[1])))
            except (TypeError, ValueError):
                continue
    return out


def folder_failure_line(doc: dict | None) -> str | None:
    """≥FOLDER_FAIL_MIN 桶失败 → 一行告警文案，否则 None。"""
    fails = folder_failures(doc)
    if len(fails) < FOLDER_FAIL_MIN:
        return None
    detail = "、".join(f"桶{i}:{err[:60]}" for i, err in fails)
    return f"{len(fails)} 个新闻抓取桶失败（{detail}）"


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _load_any(path: str):
    """通用 JSON 读取（列表/字典都可能）；失败返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pending_stuck(doc, now: datetime, stuck_sec: int = STUCK_SEC):
    """盘中在途委托停滞计数 → (常规数, 保护价挂队数)；非盘中/无数据返回 None。

    保护价挂队（止损卖出报跌停价、`protect=True`）本来就是「等买盘」，
    重启桥没有任何意义（订单在券商端、买盘不在桥上）→ 只告警不重启。
    常规在途停滞才继续走「重启桥自愈」的老路。
    """
    if not isinstance(doc, list) or not is_trading_day(now):
        return None
    m = now.hour * 60 + now.minute
    if not (STUCK_MORNING[0] <= m < STUCK_MORNING[1]
            or STUCK_AFTERNOON[0] <= m < STUCK_AFTERNOON[1]):
        return None
    normal = protect = 0
    for x in doc:
        if not isinstance(x, dict):
            continue
        try:
            t = datetime.fromisoformat(str(x.get("ts") or ""))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=BJ)
        if (now - t.astimezone(BJ)).total_seconds() <= stuck_sec:
            continue
        if x.get("protect"):
            protect += 1
        else:
            normal += 1
    return normal, protect


def order_event_lines(doc) -> list[str]:
    """委托事件 → ["<id>|<msg>", ...]（只取需告警的，按时间升序）。

    事件由 live_fills.record_event 写入 data/live_order_events.json：哨兵止损的
    跌停挂队 / 终态未成交 / 开关未开 / 下单失败等——2026-09-11 前这些只写 jsonl，
    止损没卖出去也零告警。调用方（alert.sh）按 id 去重，同一事件只打扰一次。
    """
    if not isinstance(doc, dict):
        return []
    rows = [(str(e.get("ts") or ""), str(k), str(e.get("msg") or ""))
            for k, e in doc.items()
            if isinstance(e, dict) and e.get("alert") and str(e.get("msg") or "").strip()]
    return [f"{k}|{msg}" for _, k, msg in sorted(rows)]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: alert_checks.py <llm_tier|news_stale|folder_failures|rt_down|account_down|"
              "l2_stale|pending_stuck|order_events> <path> [running]", file=sys.stderr)
        return 2
    check, path = argv[0], argv[1]
    now = datetime.now(BJ)
    out = None
    if check == "llm_tier":
        out = llm_trade_tier(now, _load(path))
    elif check == "news_stale":
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        mins = news_stale_minutes(now, mtime,
                                  running=(len(argv) > 2 and argv[2] == "running"))
        out = f"{mins}" if mins is not None else None
    elif check == "folder_failures":
        out = folder_failure_line(_load(path))
    elif check == "rt_down":
        immediate, byday = rt_down(_load(path))
        out = f"{immediate}\n{byday}"      # 固定两行：调用方按行取
    elif check == "account_down":
        out = account_down(_load(path), now)
    elif check == "l2_stale":
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        out = l2_stale_line(now, mtime, trading_day=is_trading_day(now))
    elif check == "pending_stuck":
        r = pending_stuck(_load_any(path), now)
        out = f"{r[0]}|{r[1]}" if r else None      # 固定一行「常规数|挂队数」
    elif check == "order_events":
        lines = order_event_lines(_load_any(path))
        out = "\n".join(lines) if lines else None
    else:
        print(f"unknown check: {check}", file=sys.stderr)
        return 2
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
