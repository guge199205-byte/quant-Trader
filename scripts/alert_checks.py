#!/usr/bin/env python3
"""alert.sh 用的判定函数（**只用标准库**）。

alert.sh 调的是系统 `/usr/bin/python3`（不是项目 venv），不能 import 本项目里
带 venv 依赖的模块，所以判定逻辑单独放这里，而不是继续往 alert.sh 里塞内联
heredoc——内联的东西没法 pytest，出过一次「逻辑对但阈值/窗口写错」就只能靠
线上误报发现。（order_events.py 同为纯标准库，可以被本文件 import。）

每个函数都接收 `now` 参数（便于测试），CLI 里取当前北京时间。

CLI（alert.sh 调用）：
  /usr/bin/python3 scripts/alert_checks.py llm_tier        logs/live_llm_trade_state.json
  /usr/bin/python3 scripts/alert_checks.py news_stale      data/news_brief/state.json [running]
  /usr/bin/python3 scripts/alert_checks.py folder_failures data/news_brief/state.json
  /usr/bin/python3 scripts/alert_checks.py rt_down        logs/rt_status.json
  /usr/bin/python3 scripts/alert_checks.py account_down   logs/rt_status.json
  /usr/bin/python3 scripts/alert_checks.py l2_stale        data/l2_factors_live.json
  /usr/bin/python3 scripts/alert_checks.py rt_stale        logs/rt_status.json
  /usr/bin/python3 scripts/alert_checks.py pending_stuck   data/live_pending_orders.json
  /usr/bin/python3 scripts/alert_checks.py order_events    data/live_order_events.json [logs/live_ledger.json]
  /usr/bin/python3 scripts/alert_checks.py breaker         logs/breaker/<北京日期>.json
  /usr/bin/python3 scripts/alert_checks.py round_stuck     logs/live_analysis_round.json
  /usr/bin/python3 scripts/alert_checks.py disk_water      （无参数：查根分区水位）

有告警 → stdout 一行文本；无告警 → 不输出，退出码 0（alert.sh 按空串判定）。
`rt_down` 例外：固定输出三行（立即报 / 按天报 / 按天去重键），调用方按行取。
`pending_stuck` 也固定一行「常规数|挂队数」；`order_events` 每行「事件id|文案」。
"""
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import order_events  # 委托事件读侧唯一口径（纯标准库，见 order_events.py）

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
# 总闸关闭时 live_llm_trade 降级 dry-run 并写入 state.note 的标记
# （live_llm_trade.EXEC_SWITCH_OFF_NOTE，同字面量；tests/test_alert_checks.py 钉住）：
# 当日不调仓是预期行为，不能天天按「主入口未收尾」误报
EXEC_SWITCH_OFF_NOTE = "exec_switch_off"

FOLDER_FAIL_MIN = 3                   # 抓取桶失败 ≥3 才值得打扰

# 行情源降级（rt_probe 每 5 分钟写 logs/rt_status.json）
RT_SOURCES = ("bridge", "fuyao", "aidata")
RT_DEBOUNCE = {"fuyao": 2}            # 抖动源：连续失败 ≥2 次才报（一天十几次单点抖动）
# aidata 降级（熔断打开）不自愈，要人去删 .env 的 LIVE_QUOTES_DISABLE_AIDATA 或
# 修通道 → 报一次就够，按天去重。**人工关闭态（disabled）与「没调用过」（unproven）
# 都是 ok=True，不会走到这里** —— 见 rt_probe.probe_aidata 的三态说明。
RT_BYDAY = ("aidata",)
# 看板自身的新鲜度（2026-09-22 二轮审查 HIGH-1）。rt_probe 的 cron 是 `*/5 * * * *`
# ——**全天不分交易日**，所以这里不像 l2_stale 那样有「时段」豁免：任何时刻停更
# 都是故障。15 分钟 = 连丢 3 轮。
#
# 为什么必须有这条：`rt_down` 里原先写着「缺该路（老看板/探针停更）不报——停更由
# 别的检查兜」。全仓核验后那句话是**假的**：本文件的 rt_down/account_down 都不看
# mtime，daily_report_agent 只读 account 段内容，没有任何一处检查新鲜度。于是
# 「探针写不出板子」= 六路（桥/Fuyao/腾讯/AI数据/quantdb/账户）的降级告警**永久
# 闭嘴且表面全绿**——2026-09-21 盘满（ENOSPC 打在 write_text 上）就是这个形态。
RT_STALE_MIN = 15.0
# 失败证据「旧」阈值，**镜像** rt_probe.FAIL_EVIDENCE_STALE_SEC（本模块跑在系统
# python3 上，不能 import rt_probe——那条链要 agent_tools）。超期证据仍判 down
# （未复测 ≠ 已恢复），但文案必须带年龄：aidata 是按天去重的，一笔「首次失败在
# 上上周」的证据每天重报一次，而 `first_fail_ts[11:16]` 只留时分 → 30 小时前的
# 故障读起来像今天刚发生的（2026-09-22 二轮审查 MEDIUM-2）。
# tests/test_rt_aidata_probe.py::test_age_threshold_mirrors_the_probe 钉住两边一致。
FAIL_EVIDENCE_STALE_SEC = 6 * 3600.0

# CLI 里「必须有输入文档」的检查（其余只有 disk_water）。缺参数一律 rc=2，见 main()。
PATH_REQUIRED = frozenset({
    "llm_tier", "news_stale", "folder_failures", "rt_down", "account_down",
    "l2_stale", "l2_minute", "rt_stale", "pending_stuck", "order_events", "breaker",
    "round_stuck", "deferred_stuck",
})

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
# 采集产物「停更」阈值（2026-09-22 实录）。cron 每 5 分钟一轮，单轮最坏
# MAX_CALLS_PER_PASS(80) × CALL_INTERVAL_SEC(1.5) ≈ 2 分钟，故 20 分钟 = 连丢 4 轮，
# 留足慢轮余量。起因：09-21 盘满后每轮都崩在写盘上，**产物因此停更**，而告警面
# 读的是产物自称的 ok（上一次成功留下的戳）→ 整整一天零告警。
L2_STALE_MIN = 20.0

# 分钟因子这条链的降级戳（data/l2_status.json 的 capabilities.minute_k）。
# 与上面的 l2_stale 是**两个不同的故障**：l2_stale 看的是「整个采集停了」（产物
# 文件不更新），这里看的是「采集在跑、L2 因子也在写，但分钟因子单独死了」——
# 2026-09-02→09-22 正是后者：617 轮全跑了，`minute_feats` 出现 0 次，零告警。
# 连续 ≥2 轮才报（单轮可能只是快照稀疏）；状态机在 live_l2_capture.minute_capability，
# 那边的 ok 已经吃掉了 warmup/idle 两种预期态，这里只认 ok=False。
L2_MINUTE_DEBOUNCE = 2

# 挂单停滞（data/live_pending_orders.json）：盘中在途委托 >10 分钟无回报。
# protect=True（止损报跌停保护价、跌停封死挂队）是「等买盘」的正常形态 →
# 只告警、不投重启信号；常规停滞沿用重启桥自愈。
STUCK_SEC = 600
STUCK_MORNING = (9 * 60 + 30, 11 * 60 + 30)
STUCK_AFTERNOON = (13 * 60, 15 * 60)

# 延期单滞留（logs/live_ledger.json 的 deferred + logs/deferred_expired.jsonl）。
# 延期单 = "减仓意图落盘，等桥恢复后重放"。但 replay_deferred.py 里有两条
# **静默消失**路径：
#   ① 24h 到期 → 原先只打一行日志就丢弃
#   ② 被同代码在途卖单无限期挡住（每分钟"保留延期"），最终仍落到 ①
# 两者都意味着「一笔减仓永远没执行，而无人知晓」。这正是 Vibe-Trading 的
# reconcile 设计要防的：歧义必须 classify-and-surface，绝不静默 auto-correct。
# 阈值取 2h：盘中留足重放机会（桥恢复通常是分钟级），超过就是异常。
DEFER_STUCK_SEC = 2 * 3600
DEFER_EXPIRED_JSONL = Path(__file__).resolve().parents[1] / "logs" / "deferred_expired.jsonl"

TRADING_DAYS_JSON = Path(__file__).resolve().parents[1] / "configs" / "trading_days.json"

# 整点轮心跳（logs/live_analysis_round.json，live_hourly_analysis.round_heartbeat 写）。
# 2026-09-18 实录：数据源退避把单个分析实例拖了数小时，后续 cron 轮全部
# 「已有分析实例在跑」静默跳过——当天只剩 1 轮、零告警。running=true 且心跳
# 停滞 ≥阈值即报（心跳按阶段更新，正常单阶段不会超过 ~10 分钟）。
ROUND_STUCK_MIN = 20.0


def round_stuck(doc, now: datetime, stale_min: float = ROUND_STUCK_MIN) -> str | None:
    """整点轮心跳停滞 → 一行告警文案；未在跑 / 心跳新鲜 / 戳不可解析 → None。"""
    if not isinstance(doc, dict) or not doc.get("running"):
        return None
    t = _iso_bj(doc.get("updated_ts"))
    if t is None:
        return None
    mins = (now - t.astimezone(BJ)).total_seconds() / 60.0
    if mins < stale_min:
        return None
    start = str(doc.get("start_ts") or "?")
    return (f"整点轮心跳停滞 {mins:.0f} 分钟（pid {doc.get('pid')}，"
            f"阶段 {doc.get('phase')}，起于 {start[5:16]}）"
            f"——分析实例可能卡死，后续 cron 轮会被锁跳过")


# 磁盘水位（2026-09-21 实录）：盘满(ENOSPC) 北京 15:43-17:44——rsyslog 266 万行
# 错误刷爆 journal、心跳写失败被误判「主机离线 177 分钟」、alerts.log 丢块、
# 14:45 尾盘决策轮中断。全程**没有一条告警指向磁盘**，全是下游症状——本检查把
# 「磁盘将满」本身变成告警源。阈值 88%：较日常写入量留出数小时清理窗口；
# 推送侧指纹去重，持续高位最多 2 小时打扰一次。
#
# 口径 = used/(used+free)，**分母不是 total**（2026-09-22 用户拍板改）。判据回答的是
# 「/ 上还有多少**块空间**可写」，不是「盘用了百分之几」；non-root 写方的天花板是
# used+free（= total − 保留块），不是 total。算得上来的话，GNU df 的「已用%」就是这个
# 比值（used/(used+avail)）——运维看 df 和看告警是同一个数。
#
# 旧口径的问题**不是全盲，是预警带被压缩**（曾想写成「旧口径到不了阈值」，核对后撤回：
# 事故当时本检查还没落地，且 09-21 那条 ENOSPC 是 rsyslogd 报的、该进程以 syslog 非 root
# 身份跑，撞墙条件是 f_bavail 归零 ⟹ raw 已到 100−保留比例 ≈ 94.9%，旧口径的 88% 早它
# ~6.9 个 raw 点就红了）。真正的理由是带宽：保留比例 r 下，旧口径告警时离墙只剩 (12−r) 个
# raw 点，且在 r≥12% 的文件系统（mke2fs -m 12 起）会**归零/为负**——即那条 88% 除了
# 已经 ENOSPC 之外永远不会先响；新口径的带子 ≈ (12−0.12r)，几乎与 r 无关。
# 本机（2026-09-22 快照，盘在被持续写、数字会漂）r=5.088%：raw=87.26% vs 可写=91.93%，
# 即 65G vs 107G 真实余量。阈值常量因此未动——换分母后只会**更早报**不会更晚
# （free=f_bavail ≤ total−used ⟹ 新 pct ≥ 旧 pct，对任何 statvfs 状态成立），
# fs 无保留块时两口径重合。free 口径本就是可用空间，文案那句「剩余 XGB」与之同源。
#
# 已知盲区（旧口径同样没有，别当回归）：inode 耗尽、根分区以外的挂载点、
# 只读重挂载(EROFS)——本检查只答「/ 的块空间」这一个问题。
DISK_WARN_PCT = 88.0
DISK_CRIT_PCT = 95.0


def disk_water(usage=None, warn_pct: float = DISK_WARN_PCT) -> str | None:
    """根分区**可写空间**占用 ≥ warn_pct → 一行告警；正常/取不到 → None。usage 注入便于测试。"""
    # 取数留在 try 外是**有意**的：statvfs 抛 OSError 属于「这次测量根本没发生」，
    # 让它冒出去 → main 非零退出 → alert.sh 的 acheck 记入「告警检查器自身失败」，
    # 运维看到的是一句「这些检查本次没有在跑（≠ 没问题）」。若在这里吞成 None，
    # 磁盘这一路就变成**无声缺席**（和「一切正常」同形）——那正是要防的形态。
    u = usage if usage is not None else shutil.disk_usage("/")
    try:
        used = float(u.used)
        free = float(u.free)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None                      # 值层面的怪形状（None/非数字串/缺属性）：判不出就不报
    # inf/nan 过得了 float()（`float("inf")`/`Decimal("Infinity")` 都不抛）→ 必须显式挡：
    # 否则 nan 比不过 warn_pct，会一路落到发告警分支，推「水位高 nan%（剩余 infGB）」上 QQ。
    if not (math.isfinite(used) and math.isfinite(free)):
        return None
    writable = used + free
    if writable <= 0:
        return None                      # 取不到有效值（含 total/avail 全 0）：不炸、不报
    pct = used / writable * 100.0
    if pct < warn_pct:
        return None
    free_gb = free / (1 << 30)
    head = "🔴 磁盘将满" if pct >= DISK_CRIT_PCT else "⚠️ 磁盘水位高"
    return (f"{head} {pct:.0f}%（可写空间已用，剩余 {free_gb:.0f}GB）——盘满会连锁：心跳写失败"
            f"误报主机离线、告警/账本写丢、整点轮中断；请先清理（/var/log、docker、"
            f"临时大文件）再动交易相关操作")


def rt_stale_line(now: datetime, mtime: float | None) -> str | None:
    """行情看板（logs/rt_status.json）停更 → 一行告警；新鲜/取不到时刻 → None。

    mtime=None 表示文件**不存在**——对这份看板而言「不存在」不等于「没启用」：
    cron 全天每 5 分钟跑一轮，部署到位后就该一直有人写。缺失 = 探针从一开始就
    没写成功（首部署那一轮除外，那时也确实没有可报的旧结论可冻结）。

    未来 mtime（时钟回拨）不许当成「新鲜」：未来时刻对任何阈值永远新鲜 = 永久
    刷绿，与 rt_probe 里 last_ok_ts 的规则同源。
    """
    if mtime is None:
        return ("⚠️ 行情看板缺失：logs/rt_status.json 不存在，六路行情源（桥/Fuyao/"
                "腾讯/AI数据/quantdb/账户）的降级**无人监视**")
    age = now.timestamp() - mtime
    if 0 <= age < RT_STALE_MIN * 60:
        return None
    if age < 0:
        return (f"⚠️ 行情看板时刻在未来（{-age / 60:.0f} 分钟），不予采信——"
                f"时钟/时区异常，六路降级判定不可用")
    return (f"⚠️ 行情看板停更 {age / 60:.0f} 分钟（阈值 {RT_STALE_MIN:.0f} 分钟）："
            f"rt_probe 写不出 logs/rt_status.json，六路行情源（桥/Fuyao/腾讯/AI数据/"
            f"quantdb/账户）的降级**全部冻结在上一份板上**，看不出来不等于都健康；"
            f"先查磁盘与 logs/rt_probe.log")


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


def _l2_stale_line(doc: dict, now: datetime) -> str | None:
    """产物**停更** → 一行告警；新鲜/取不到时刻口径 → None。

    为什么「停更」必须单独判：读侧此前只看产物自称的 `ok`。而轮次一旦崩在写盘上
    （2026-09-22 盘满：每轮 OSError(28) 直接穿到 sys.exit），**产物根本不会更新**，
    盘上留着的是**上一次成功**的戳（ok=true）→ 读出来一切正常。产物停更与健康
    产物长得一模一样，这就是那次 24 小时零告警的根因。
    `last_cycle_at`（轮次时刻）此前写进了产物但**全仓无人读**。
    """
    if not isinstance((doc.get("capabilities") or {}).get("minute_k"), dict):
        # 连能力戳都没有 = 老产物/还没跑过 → **无话可说**，不是停更。
        # 升级瞬间产物还没被新代码写过，此时报停更就是全量假告警（而第一次误报
        # 就足以让运维学会忽略这块板）。
        return None
    t = _iso_bj(doc.get("last_cycle_at"))
    if t is None:
        # 有戳、却读不出轮次时刻 → 产物在自称健康，却证明不了自己活着。
        return ("L2 采集产物读不到轮次时刻（last_cycle_at 缺失/不可解析）"
                "——采集轮可能没在写，查 logs/live_l2_capture.log")
    mins = (now - t.astimezone(BJ)).total_seconds() / 60.0
    if mins < L2_STALE_MIN:
        return None
    return (f"L2 采集停更 {mins:.0f} 分钟（最后一轮 {t.astimezone(BJ):%m-%d %H:%M}）"
            f"——正常每 5 分钟一轮；采集轮可能每轮都崩在取数/写盘上，"
            f"查 logs/live_l2_capture.log 有无 ⛔ 中止行")


def l2_minute_line(doc: dict | None, now: datetime) -> str | None:
    """分钟因子降级 → 一行说明，否则 None。

    读 `data/l2_status.json`（轮次时刻 + 形状见
    live_l2_capture.minute_capability）。三处刻意的沉默：
      * 没有产物（部署瞬间可能还没写）→ 不报；
      * 非交易时段 → 不报，采集本就不跑，那是设计不是故障；
      * 老产物没有能力戳，但轮次时刻新鲜 → 走到能力判据自然返回 None。
    warmup/idle 已由写侧判成 ok=True，这里不重复判。

    判据顺序：**先判停更，再判产物自称的能力**。停更优先是因为产物自称的 ok 只在
    「它确实在更新」时才有意义。
    """
    if not isinstance(doc, dict):
        return None
    if not is_trading_day(now):
        return None
    m = now.hour * 60 + now.minute
    if not ((L2_MORNING_START <= m < L2_MORNING_END)
            or (L2_AFTERNOON_START <= m < L2_AFTERNOON_END)):
        return None
    stale = _l2_stale_line(doc, now)
    if stale:
        return stale
    cap = (doc.get("capabilities") or {}).get("minute_k")
    if not isinstance(cap, dict) or cap.get("ok"):
        return None
    # 数不出来（坏类型/缺字段）按**已达阈值**处理：戳自称 ok=False 时，唯一可行动
    # 的信息是「有降级」，计数值只用于防抖——裸 int() 抛出去等于这条检查静默失效。
    streak = _as_counter(cap.get("fail_streak"), default=L2_MINUTE_DEBOUNCE)
    if streak < L2_MINUTE_DEBOUNCE:
        return None
    ts = str(cap.get("first_fail_ts") or "")[11:16]
    err = str(cap.get("error") or "").strip()
    n = _streak_txt(cap.get("fail_streak"), streak)
    return (f"{cap.get('state')}"
            + (f" 连续{n}次{'自' + ts if ts else ''}" if ts else f" 连续{n}次")
            + (f"：{err[:90]}" if err else ""))


def _as_counter(v: Any, default: int = 1) -> int:
    """板子里的计数（纯标准库小工具）。

    看板是跨进程落盘的 JSON，字段类型不受本进程控制：`int("abc")` 抛出去 →
    整条检查 rc≠0 → 该检查静默失效（0.1 秒的坏字段换一条检查永久瞎掉）。
    `Infinity`/`NaN` 是合法 JSON token，`int(inf)` 抛的是 OverflowError；
    `True` 是 int 子类但不是计数。
    """
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def _streak_txt(raw: Any, count: int) -> str:
    """计数的**显示**形式：字段在、但数不出来 → 印 `?`，不把兜底值当事实印出去。

    `_as_counter` 的 default 是给**判据**用的（坏值按已达阈值 → 宁报勿哑），但
    「连续 3 次」和「次数未知」对运维是两条不同的信息：后者通常意味着写侧也坏了
    （写侧每轮自愈重写，所以它一般只坏一轮）；照抄兜底值 = 编一个数字。
    字段**缺席**（老看板）沿用「按 N 次算」的既定语义，只有「在而读不出」才印 `?`。
    """
    if raw is None:
        return str(count)
    if isinstance(raw, bool):
        return "?"
    try:
        return str(int(raw))
    except (TypeError, ValueError, OverflowError):
        return "?"


def _as_age(v: Any) -> float | None:
    """失败证据的年龄（秒）；数不出来 → None（**不印**，而不是印 0）。"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if f != f else f


def _age_txt(v: dict) -> str:
    """陈旧失败证据的年龄后缀。只在**超期**时出现，避免给刚发生的故障加噪音。"""
    age = _as_age(v.get("fail_age_sec"))
    if age is None or age <= FAIL_EVIDENCE_STALE_SEC:
        return ""
    return f"（证据 {age / 3600:.0f} 小时前，未复测）"


def _key_token(v: dict) -> str:
    """状态词 → 可安全拼进 **文件路径** 的片段。

    alert.sh 拿它拼按天去重键（`/tmp/.baymax_aidata_alerted_<日>_<词>`）。
    板子里的 `state` 是外部数据，不能原样进路径（`../../x` 就是目录穿越），
    只留 [a-z0-9_]。缺 state 的老看板语义上一直是「通道 down」。
    """
    raw = str(v.get("state") or "down").lower()
    return "".join(c for c in raw if c.isascii() and (c.isalnum() or c == "_"))[:24] or "down"


def rt_down(doc: dict | None) -> tuple[str, str, str]:
    """行情源降级 → (立即报的源, 按天去重报的源, 按天去重键)，均为逗号分隔描述串。

    `fail_streak` 由 rt_probe 写；老格式（无该字段）按 1 次算，不改变原有立即报行为。

    第 3 行是给 alert.sh 的**机器可读**去重键（不是从文案里正则抠词）：`degraded`
    （熔断状态文件坏）与 `down`（通道坏）同一天是两件事，共用一个键会让先到的那条
    吃掉另一条——2026-09-21 盘满就是先坏状态文件、真有通道故障也报不出来。
    两种状态也必须在文案里分得开：把状态文件的错说成通道的错是另一种误诊。
    """
    d = doc if isinstance(doc, dict) else {}
    immediate: list[str] = []
    byday: list[str] = []
    key_tokens: set[str] = set()
    for k in RT_SOURCES:
        v = d.get(k)
        if not isinstance(v, dict) or v.get("ok"):
            # 缺该路（老看板/探针停更）在这里不报，但**停更是另一条检查**：
            # alert.sh 2d-3 的 `acheck rt_stale` 盯 logs/rt_status.json 的 mtime。
            # 2026-09-22 审查 HIGH-1 之前这里写着「停更由别的检查兜」而全仓没有
            # 那条检查——板子写不出去（盘满/权限）时六路降级告警会静默冻结。
            continue
        streak = _as_counter(v.get("fail_streak"))
        if streak < RT_DEBOUNCE.get(k, 1):
            continue
        ts = str(v.get("first_fail_ts") or "")[11:16]
        since = f"自{ts}" if ts else ""
        n = _streak_txt(v.get("fail_streak"), streak)
        age_note = _age_txt(v)
        if k in RT_BYDAY:
            tok = _key_token(v)
            key_tokens.add(tok)
            byday.append(f"{k}({tok} 连续{n}次{since}{age_note})")
        else:
            immediate.append(f"{k}(连续{n}次{since}{age_note})")
    return ",".join(immediate), ",".join(byday), ",".join(sorted(key_tokens))


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
    streak = _as_counter(v.get("fail_streak"))
    if streak < debounce:
        return None
    if not is_trading_day(now):
        return None
    m = now.hour * 60 + now.minute
    if not (ACCT_ALERT_START <= m < ACCT_ALERT_END):
        return None
    ts = str(v.get("first_fail_ts") or "")[11:16]
    err = str(v.get("error") or "").strip()
    n = _streak_txt(v.get("fail_streak"), streak)
    return f"连续{n}次{'自' + ts if ts else ''}" + (f"：{err[:90]}" if err else "")


def _iso_bj(s) -> datetime | None:
    """ISO 时间串 → 带时区 datetime；不可解析返回 None（无时区按北京）。"""
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=BJ)


def news_freshness(now: datetime, doc, running: bool = False,
                   trading_day: bool | None = None,
                   state_ok: bool = True) -> str | None:
    """新闻管线/分子新鲜度 → "round|说明" / "chief|说明"；健康或不该判定 → None。

    2026-09-18 重构（当天事故复盘）：旧判据用 latest.json 的 mtime——任何写动作
    都能刷新鲜它，且 pgrep 到进程在跑就豁免（卡死的进程反而挡住告警）。当日实录：
    主机冻结打掉 09:25/09:55 两轮，白天交易 agent 引用 14 小时前的分子，上午
    判定窗口零告警；下午报出后又因文案里分钟数变化刷了 11 条。

    新判据读 state.json（news_brief 原子写、语义明确）：
      ① round：now − last_end > 75 分钟 → 管线停更。last_end 是**跑满五段且无
         桶失败/主编失败才推进**的成功游标（任何环节静默故障都不推进），比
         mtime 可靠；轮次没跑成（主机/cron 故障）也停在这个游标上。
      ② chief：now − max(last_chief_ok, last_empty) > 75 分钟 → 管线在跑但分子
         无新产出（主编持续截断失败）；空窗（无新增新闻）是合法形态，last_empty
         计入，避免静默窗口误报。
    进程豁免的"过期"由调用方保证（alert.sh 只把存活 ≤30 分钟的进程视为在跑，
    卡死进程不再豁免）。trading_day=None 按周一~周五（调用方一般传日历判定）。
    state_ok=False（state.json 缺失/不可读，由 CLI 判定传入）→ 判定不可用本身
    就是告警（2026-09-18 评审 LOW：文件被删/写坏与"首部署无游标"此前同样静默）。
    """
    if running:
        return None
    if trading_day is None:
        trading_day = now.weekday() < 5
    if not trading_day:
        return None
    m = now.hour * 60 + now.minute
    in_morning = NEWS_MORNING_START <= m < NEWS_MORNING_END
    in_afternoon = NEWS_AFTERNOON_START <= m < NEWS_AFTERNOON_END
    if not (in_morning or in_afternoon):
        return None
    if now.tzinfo is None:                  # 裸 datetime（调用方失误）按北京解释
        now = now.replace(tzinfo=BJ)
    if not state_ok:
        return ("round|新闻 state.json 缺失或不可读——停更判定不可用（管线可能"
                "从未运行/文件被清）；查 logs/news_brief.log 与 data/news_brief/")
    d = doc if isinstance(doc, dict) else {}
    last_end = _iso_bj(d.get("last_end"))
    if last_end is None:
        return None          # 无游标（首部署/旧格式）：不判、不臆造
    mins = (now - last_end.astimezone(BJ)).total_seconds() / 60.0
    if mins < -5:
        # 游标时间在将来（时钟回拨/写入方时区写错）：负值会让 mins>阈值 恒 False
        # → 静默无告警。这本身就是数据/环境异常，如实报出来（2026-09-18 评审 LOW）
        return (f"round|新闻 state 游标时间在将来（{last_end.astimezone(BJ):%m-%d %H:%M}"
                f"，早于当前 >5 分钟）——检查主机时钟或写入方时区，停更判定暂不可信")
    if mins > NEWS_STALE_MIN:
        # 归因（2026-09-18 评审 M-1）：游标不推进的直接原因若是**主编无产出**
        # （news_brief 在主编截断/失败轮不推游标、写 last_chief_fail），文案必须
        # 指向主编段——此前只说"查主机/cron"，而主编批量截断才是已知高发形态
        # （09-08 静默 4 小时）。last_chief_fail 晚于 last_end = 停更是主编造成的。
        chief_fail = _iso_bj(d.get("last_chief_fail"))
        if chief_fail is not None and chief_fail > last_end:
            return (f"chief|新闻管线停更 {mins:.0f} 分钟——最近一轮**主编无产出**"
                    f"（截断/解析失败于 {chief_fail.astimezone(BJ):%m-%d %H:%M}，"
                    f"游标停在 {last_end.astimezone(BJ):%m-%d %H:%M}）——查 "
                    "logs/news_brief.log 主编段与 news-chief 对话记录")
        return (f"round|新闻管线停更 {mins:.0f} 分钟（最近一次完整成刊 "
                f"{last_end.astimezone(BJ):%m-%d %H:%M}）——盘中应每小时一期；查 "
                "logs/news_brief.log 与主机/cron 状态，必要时手动跑 news_brief.py；"
                "交易侧当前引用的是过期新闻")
    markers = [t for t in (_iso_bj(d.get("last_chief_ok")), _iso_bj(d.get("last_empty")))
               if t is not None]
    if markers:
        newest = max(markers)
        cmin = (now - newest.astimezone(BJ)).total_seconds() / 60.0
        if cmin > NEWS_STALE_MIN:
            return (f"chief|新闻分子 {cmin:.0f} 分钟无新产出（管线在跑但无成刊，"
                    f"最近产出 {newest.astimezone(BJ):%m-%d %H:%M}）——查 "
                    "logs/news_brief.log 主编段是否批量截断失败")
    return None


def breaker_lines(doc) -> list[str]:
    """当日循环熔断记录 → ["<agent>|<reason>|<ts>", ...]；无熔断/坏文件 → [].

    由 live_breaker.save_trip 写 logs/breaker/{day}.json（{agent: {ts, reason}}）。
    2026-09-18 前熔断只落盘不上告警面——某 agent 全天禁止新开仓而人不知道。
    调用方（alert.sh）按 agent 去重：熔断是当日状态，同一场只打扰一次。
    """
    if not isinstance(doc, dict):
        return []
    rows = []
    for agent, rec in doc.items():
        if not isinstance(rec, dict):
            continue
        rows.append((str(rec.get("ts") or ""), str(agent),
                     str(rec.get("reason") or "")))
    return [f"{a}|{r}|{t}" for t, a, r in sorted(rows)]


def llm_trade_tier(now: datetime, doc: dict | None) -> str | None:
    """09:35 主入口当日执行状态 → "t1|原因" / "t2|原因" / None。

    t1 = 主入口未正常收尾（需人工确认是否已下单）；t2 = 两班补跑（10:05/11:05）也未成功。
    `ok is True` 才算完成——`ok: null`（启动了但没走完）与缺文件都算异常。
    例外：note=EXEC_SWITCH_OFF_NOTE（总闸关闭 → 当日不执行是预期，不告警）。
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
    if same_day and str(d.get("note") or "") == EXEC_SWITCH_OFF_NOTE:
        return None     # 总闸关闭：当天不执行是预期行为（开关一关不该天天报）
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
            except (TypeError, ValueError, OverflowError):   # Infinity 也是合法 JSON token
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


def deferred_stuck(doc, now: datetime, stuck_sec: int = DEFER_STUCK_SEC,
                   expired_path: Path | str | None = None):
    """延期单滞留 → 固定一行「滞留数|最久小时|今日作废数」；无异常返回 None。

    滞留：盘中且延期单存在 > stuck_sec 仍未重放（桥没恢复，或被在途单挡住）。
    作废：replay_deferred.py 把 24h 到期丢弃的记进 logs/deferred_expired.jsonl ——
          这些是**已经确定没执行的减仓**，任何时刻都要让人看见。
    """
    if not isinstance(doc, dict):
        return None
    stuck = oldest_h = 0
    if is_trading_day(now):
        m = now.hour * 60 + now.minute
        if (STUCK_MORNING[0] <= m < STUCK_MORNING[1]
                or STUCK_AFTERNOON[0] <= m < STUCK_AFTERNOON[1]):
            for d in (doc.get("deferred") or []):
                if not isinstance(d, dict):
                    continue
                t = _iso_bj(d.get("ts"))
                if t is None:
                    continue
                age = (now - t.astimezone(BJ)).total_seconds()
                if age > stuck_sec:
                    stuck += 1
                    oldest_h = max(oldest_h, age / 3600)
    today = now.strftime("%Y-%m-%d")
    expired = 0
    path = Path(expired_path) if expired_path else DEFER_EXPIRED_JSONL
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if str(rec.get("ts") or "")[:10] == today:
                expired += 1
    except OSError:
        pass
    if not stuck and not expired:
        return None
    return f"{stuck}|{round(oldest_h, 1)}|{expired}"


def order_event_lines(doc, *, ledger=None, now=None) -> list[str]:
    """委托事件 → ["<id>|<msg>", ...]（只取仍需告警的，按时间升序）。

    事件由 live_fills.record_event 写入 data/live_order_events.json：哨兵止损的
    跌停挂队 / 终态未成交 / 开关未开 / 下单失败等——2026-09-11 前这些只写 jsonl，
    止损没卖出去也零告警（所以本检查故意不设交易日历限制）。

    判定语义的唯一出处 = order_events.py：24h 保留窗口（与写侧同源）、人工 ack
    的 resolved_* 字段、卖域 + 账本已无持仓的自动消解。`ledger` 传账本文档
    （alert.sh 传 logs/live_ledger.json）；账本缺档时保守全报。本函数只读文件。
    调用方（alert.sh）按 id 去重，同一事件每天最多打扰一次。
    """
    return order_events.order_event_lines(doc, ledger=ledger, now=now)


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: alert_checks.py <llm_tier|news_stale|folder_failures|rt_down|account_down|"
              "l2_stale|l2_minute|pending_stuck|order_events|deferred_stuck|round_stuck|breaker|"
              "disk_water> <path> [running|账本文档]",
              file=sys.stderr)
        return 2
    check = argv[0]
    path = argv[1] if len(argv) > 1 else ""
    # **缺输入文件参数 = 检查没跑，不能算「没问题」**。
    # 旧实现 path 缺省为 ""、_load("") 返回空文档 → 输出空、rc=0，与「真无故障」
    # 完全同形：alert.sh 里一个路径被改坏，那条检查就永久静默失效而告警面全绿
    # （2026-09-22 起 alert.sh 的 acheck 只能看见 rc≠0，看不见这一种）。
    # 缺参一律 rc=2 → 由 acheck 汇总成「检查器自身失败」告警。
    if check in PATH_REQUIRED and not path:
        print(f"{check}: 缺少输入文件参数（不按「无故障」处理）", file=sys.stderr)
        return 2
    now = datetime.now(BJ)
    out = None
    if check == "llm_tier":
        out = llm_trade_tier(now, _load(path))
    elif check == "news_stale":
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            state_ok = isinstance(doc, dict)
        except (OSError, ValueError):
            doc, state_ok = {}, False
        out = news_freshness(now, doc,
                             running=(len(argv) > 2 and argv[2] == "running"),
                             trading_day=is_trading_day(now), state_ok=state_ok)
    elif check == "folder_failures":
        out = folder_failure_line(_load(path))
    elif check == "rt_down":
        immediate, byday, day_key = rt_down(_load(path))
        out = f"{immediate}\n{byday}\n{day_key}"      # 固定三行：调用方按行取
    elif check == "account_down":
        out = account_down(_load(path), now)
    elif check == "l2_minute":
        out = l2_minute_line(_load(path), now)
    elif check == "l2_stale":
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        out = l2_stale_line(now, mtime, trading_day=is_trading_day(now))
    elif check == "rt_stale":
        # 缺文件与 mtime 读不出是**同一件事**（没人在写）→ 都要报，不能静默
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        out = rt_stale_line(now, mtime)
    elif check == "pending_stuck":
        r = pending_stuck(_load_any(path), now)
        out = f"{r[0]}|{r[1]}" if r else None      # 固定一行「常规数|挂队数」
    elif check == "order_events":
        # argv[2]=账本文档（可选）：卖域事件在账本已无该标的持仓时自动消解
        ledger = _load_any(argv[2]) if len(argv) > 2 else None
        lines = order_event_lines(_load_any(path), ledger=ledger, now=now)
        out = "\n".join(lines) if lines else None
    elif check == "breaker":
        lines = breaker_lines(_load_any(path))
        out = "\n".join(lines) if lines else None
    elif check == "round_stuck":
        out = round_stuck(_load_any(path), now)
    elif check == "disk_water":
        out = disk_water()
    elif check == "deferred_stuck":
        out = deferred_stuck(_load(path), now)
    else:
        print(f"unknown check: {check}", file=sys.stderr)
        return 2
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
