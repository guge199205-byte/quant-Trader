#!/usr/bin/env python3
"""分钟级价格哨兵：执行 LLM 盘中分析挂的 watch 条件位（跌破止损 / 到位止盈）。

背景：整点分析每小时才一轮，「跌破 ¥120 就减仓」这类条件位在两次整点之间
没有人盯。本脚本由 cron 每分钟拉起，闭环是：

  live_hourly_analysis 解析 LLM 决策中的 watch 项
    → data/live_watch.json（每 agent 每小时整组刷新，最新分析说了算）
    → 本脚本查桥日K最新价（交易时段日K最后一根=当日实时价）
    → 现价 ≤ stop_loss 减仓 pct 比例 / ≥ take_profit 止盈 pct 比例
    → 与盘中执行同一套卖出闸门（T+1 可卖量、100 股整数倍）
    → 报**跌停保护价**（成交在盘口买一，跌停封死则挂队+告警；保护价口径见
      ashare_rules.protect_sell_price，2026-09-11 移植 quantmind 真账户实测）
    → 在途闸门：同标的有未确认卖单则不下第二笔；幂等委托号防崩溃重试重复下单
    → record_sell 分账记账 + logs/live_watch_YYYYMMDD.jsonl
    → 触发并下单成功后该条规则**保留并打标**（pending_order_id/pending_volume），
      等这笔委托的归宿：满额成交或持仓清零 → 消费；终态未卖完/废单 → 重新布防
      （换 plan 号，下一轮可再触发）；台账查不到该委托号 → 不重布防 + 转人工事件。
      2026-09-12（001312 实录）之前是「下单即消费」：那笔单随后被柜台判废
      （成交 0/300），条件位已消失，持仓当日再无保护
    → T+1 不可卖 / 下单失败 / 在途未确认 → 规则原样保留，下一分钟或次日继续守
    → 未成交/废单/收盘未了结 → data/live_order_events.json 由 alert.sh 上报

用法:
  python scripts/live_price_watch.py             # 交易时段才执行（cron 每分钟）
  python scripts/live_price_watch.py --force     # 忽略时段检查（调试）
  python scripts/live_price_watch.py --dry-run   # 触发只打印，不下单
  python scripts/live_price_watch.py --status    # 查看当前挂着的条件位

cron（本机 JST，北京=JST-1）: * 10-12,14-16 * * 1-5
"""
import argparse
import copy
import fcntl
import json
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_cal import is_trading_day, why_not  # noqa: E402
from live_fills import (round_sell_qty, inflight, record_event,  # noqa: E402
                        CANCEL_STATUSES)

from ashare_rules import (at_limit_down, after_hours_eligible, board_of,  # noqa: E402
                          after_hours_window, protect_sell_price)

WATCH_FILE = ROOT / "data" / "live_watch.json"
LOG_DIR = ROOT / "logs"
SKIP_STATE_FILE = LOG_DIR / "live_watch_notify_state.json"  # 账户级提醒的跨进程去重（见 _notify_once）
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


def _rule_key(rule: dict) -> tuple:
    """规则身份 = (代码, 创建时间)。同一 agent 可对同一代码挂两条（止损 + 止盈），
    只有 created_ts 能区分；两者都由 save_watch_rules 写入，是稳定键。"""
    return (str(rule.get("code") or ""), str(rule.get("created_ts") or ""))


_LOCK_DEPTH = threading.local()


def _watch_lock_file() -> Path:
    """锁文件路径**按当前 WATCH_FILE 现算**（不存模块常量）：测试 patch 了
    WATCH_FILE 时锁也自动跟着走，不会去 flock 生产 data/live_watch.lock。"""
    return WATCH_FILE.with_name(WATCH_FILE.name + ".lock")


@contextmanager
def _watch_lock():
    """data/live_watch.json 的跨进程互斥（与 live_fills._file_lock 同款 flock）。

    本文件有三个写者：整点分析 save_watch_rules（整组替换）、收盘复盘同步（同一
    函数）、哨兵 flush_watch（差量合并）。三者都是「读当前文件 → 改 → 写回」，
    没有锁时两次读-改-写交叠：后写的把先写的整个覆盖（丢更新 = 真实止损位消失）。
    同线程可重入（嵌套调用不会自锁）。锁文件打不开 → 告警并按无锁继续（宁可偶发
    丢更新，也不能让条件位彻底写不进去）。
    """
    depth = getattr(_LOCK_DEPTH, "depth", 0)
    if depth:                      # 已持锁（同线程嵌套）：直接放行
        _LOCK_DEPTH.depth = depth + 1
        try:
            yield True
        finally:
            _LOCK_DEPTH.depth = depth
        return
    fh, locked = None, False
    try:
        lock_file = _watch_lock_file()
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_file, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX)
        locked = True
    except OSError as exc:
        print(f"⚠️ live_price_watch: 锁文件不可用（{exc}），本次按无锁继续", file=sys.stderr)
        yield True
        return
    finally:
        if not locked and fh is not None:
            fh.close()
    _LOCK_DEPTH.depth = 1
    try:
        yield True
    finally:
        _LOCK_DEPTH.depth = 0
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _locked_update_watch(mutate) -> dict:
    """持锁读-改-写条件位文件：mutate(当前规则表) 返回新表（就地改后返回亦可）。"""
    with _watch_lock():
        cur = load_watch()
        out = mutate(cur)
        out = out if isinstance(out, dict) else cur
        save_watch(out)
        return out


def flush_watch(snapshot: dict, result: dict) -> None:
    """本轮**只施加自己的差量**回写（复刻 quantmind 止损执行器 LOW 12 口径）。

    2026-09-11：run_watch 原来结尾整表回写，用的是轮询开始时的快照。但一轮要跑
    十几秒（每条规则 1s 桥限流 + 逐只行情），期间整点分析 / 收盘复盘可能刚调
    save_watch_rules 给某个 agent 换上新条件位 —— 陈旧快照一写回去，新挂的止损
    就被静默冲掉（持仓裸奔到下一次分析；收盘复盘挂的直接等次日）。

    合并规则（先读回当前文件，再逐 agent 施加本轮差量）：
      - 本轮消费掉的（snapshot 有、result 没有）→ 从当前文件删；
      - 本轮改过的（如 move_stop 上移、方向校正、跳过提醒去重位）→ 用本轮值覆盖；
      - 本轮没碰的（当前文件有、snapshot 没有）→ 原样保留（那是别人的写入）。
    读-改-写在 _watch_lock 内完成（2026-09-12）：差量合并只解决「快照陈旧」，
    两个写者同时在读-改-写仍会互相覆盖。
    """
    def _merge(cur: dict) -> dict:
        out = dict(cur)
        for agent in set(snapshot) | set(result):
            before = {_rule_key(r): r for r in snapshot.get(agent, [])}
            after = {_rule_key(r): r for r in result.get(agent, [])}
            if before == after:
                continue                # 本轮没动过该 agent：整组不碰
            merged = []
            for r in cur.get(agent, []):
                k = _rule_key(r)
                if k in before and k not in after:
                    continue            # 本轮消费掉的 → 删
                merged.append(after[k] if k in before else r)
            have = {_rule_key(r) for r in merged}
            for k, r in after.items():
                if k not in before and k not in have:
                    merged.append(r)    # 本轮新增的（run_watch 不新增，保持自洽）
            if merged:
                out[agent] = merged
            else:
                out.pop(agent, None)
        return out

    _locked_update_watch(_merge)


def _pct(v, default: float = 1.0) -> float:
    """减仓比例容错解析并夹到 [0,1]；非法值回退默认（LLM 可能给字符串）。

    NaN/Inf 必须挡在这里（2026-09-12 审查）：`min(max(nan,0),1)` 仍是 nan，而
    `nan <= 0` 为假 → 规则带着 nan 挂上；触发时 `int(avail*nan)` 抛 ValueError，
    run_watch 循环没有 try → **整轮哨兵中断且每分钟复现**，该分钟所有条件位都失去
    保护。json.loads 默认接受字面量 `NaN`，手改 live_watch.json 就能造出来。
    """
    try:
        f = min(max(float(v), 0.0), 1.0)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _lvl(v):
    """价位归一化（去重签名用）：数字 → 三位小数，其余 → None。"""
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _rules_from_decisions(decisions: list) -> list:
    """LLM 决策里的 watch 项 → 规则对象。

    去重（2026-09-12，001312 实录 17.05×2 重复止损）：同 code 且 (止损, 止盈,
    减仓比例) 完全相同的重复项只留第一条——两条同价规则会各触发一次，第二笔被
    在途闸门拦下，白跑一轮还把日志刷花。

    比例缺失/为 0（2026-09-12 审查 HIGH-1）：`parse_intraday_decision` 会把缺字段
    压成 pct=0.0，与本条规则的「0 = 不表达卖出量」撞车 → 该挂的止损被静默丢掉。
    靠 pct_given 区分：**没给 → 按全仓挂**（漏挂 = 这个防守位当日无人执行，比按
    全仓挂危险得多）；**明说 0 → 不挂，但落 watch_pct_zero 事件留痕**。

    脏值（pct_bad_raw，NaN/Inf/"0.3股"）同走「按全仓挂」：执行链对脏值停手
    （不该猜比例下单），但保护层偏向离场——条件位漏挂才是当日裸奔。留痕用
    `watch_pct_dirty` 说清是「解析不出」而不是「没给」（2026-09-12 终审）。
    """
    mine, seen = [], set()
    for d in decisions or []:
        if d.get("action") != "watch":
            continue
        code = str(d.get("code") or "").strip()
        if not code or (d.get("stop_loss") is None and d.get("take_profit") is None):
            continue  # 没有价位的条件位没有意义
        raw_pct = d.get("pct")
        # 非法值留痕：手改 live_watch.json / 非解析路径的脏值不许静默变 100% 全减
        if raw_pct in (None, ""):
            pass
        elif isinstance(raw_pct, float) and not math.isfinite(raw_pct):
            # json.loads 接受字面量 NaN/Infinity；_pct 虽已回退全仓，但脏值必须留痕
            print(f"  ⚠️ watch {code}: pct 非有限数（{raw_pct!r}），按 100% 全仓处理")
        elif not isinstance(raw_pct, (int, float)):
            try:
                float(raw_pct)
            except (TypeError, ValueError):
                print(f"  ⚠️ watch {code}: pct 非数字（{raw_pct!r}），按 100% 全仓处理")
        elif raw_pct > 1:
            print(f"  ⚠️ watch {code}: pct={raw_pct} 超出 (0,1]（疑似百分数），按 100% 全仓处理")
        pct = _pct(raw_pct)
        if pct <= 0:
            raw_bad = d.get("pct_bad_raw")
            if d.get("pct_given", True):
                print(f"  🗑️ watch {code}: 决策明说 pct={raw_pct!r}（不表达卖出量），未挂条件位")
                record_event("watch_pct_zero", code,
                             f"[{code}] watch 决策比例 pct={raw_pct!r}（不是「全减」）：未挂条件位，"
                             f"止损 {d.get('stop_loss')} / 止盈 {d.get('take_profit')}",
                             alert=False)
                continue
            if raw_bad:
                print(f"  ⚠️ watch {code}: pct 无法解析（{raw_bad}），保护层按全仓（100%）挂条件位")
                record_event("watch_pct_dirty", code,
                             f"[{code}] watch 决策比例无法解析（{raw_bad}，解析器判脏）："
                             f"按全仓挂条件位（止损 {d.get('stop_loss')} / 止盈 {d.get('take_profit')}）",
                             alert=False)
            else:
                print(f"  ⚠️ watch {code}: 决策未给 pct，按全仓（100%）挂条件位")
                record_event("watch_pct_missing", code,
                             f"[{code}] watch 决策未给减仓比例，按全仓挂条件位"
                             f"（止损 {d.get('stop_loss')} / 止盈 {d.get('take_profit')}）",
                             alert=False)
            pct = 1.0
        sig = (code, _lvl(d.get("stop_loss")), _lvl(d.get("take_profit")), pct)
        if sig in seen:
            continue
        seen.add(sig)
        mine.append({
            "code": code,
            "stop_loss": d.get("stop_loss"),
            "take_profit": d.get("take_profit"),
            "move_stop": d.get("move_stop"),
            "pct": pct,
            "reason": str(d.get("reason") or ""),
            # 逐规则取时：**组内 created_ts 唯一**是 (_rule_key)、(plan 号 _watch_plan_id)
            # 与 flush_watch 差量合并三处的前提——若两条规则拿到同一戳，它们会先塌成
            # 同一条规则、再共用一个 plan 号（批 13d 的失效形态）。新增批量/回放/迁移
            # 写点前必须先保证组内唯一（2026-09-12 review-p03 复核结论：不为此加机制）。
            "created_ts": now_cn().isoformat(),
        })
    return mine


def save_watch_rules(agent: str, decisions: list) -> int:
    """整点分析落 watch 条件位：该 agent 的旧规则整组替换（最新分析说了算），
    本轮无 watch 决策则清空该 agent 全部旧规则。返回落盘规则数。

    读-改-写全程持锁（2026-09-12）：旧实现裸 load→save，与哨兵的差量回写交叠时
    会互相覆盖（丢更新 = 刚挂上的止损位消失）。"""
    mine = _rules_from_decisions(decisions)

    def _replace(rules: dict) -> dict:
        out = {a: v for a, v in rules.items() if a != agent}
        if mine:
            out[agent] = mine
        return out

    _locked_update_watch(_replace)
    per_code: dict = {}
    for r in mine:
        per_code[r["code"]] = per_code.get(r["code"], 0) + 1
    for code, n in per_code.items():
        if n > 1:
            print(f"⚠️ [{agent}] {code} 挂了 {n} 条条件位（价位/比例不同）："
                  f"先触发的先成交，重复下单由在途闸门兜底")
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
    """账户级提醒的小时级去重（并上告警面）。

    哨兵每分钟一个进程，模块内变量留不住；规则级去重（_notify_skip）写在规则对象里，
    而账户快照退化时整轮不动规则文件 → 需要个跨进程的边车文件。
    2026-09-12 P2：小时内首次提醒同时 record_event（alert.sh 上报）——2026-09-10
    账户通道掉了整日，只有 print，人看日志才发现。事件 key 带小时 tag，
    与边车文件同频（每小时最多一条），不刷屏。"""
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
    # 告警面：事件落盘失败不许反过来打断哨兵（record_event 内部已吞 OSError）
    record_event(key, "", msg, key=f"{key}:{tag}")


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


def _discard_event(agent: str, rule: dict, why: str, persist: bool = True) -> None:
    """条件位被作废时留痕（alert=False：规则失效本身不是资金风险，但必须可回溯——
    此前只有 print，条件位为什么消失事后无从查）。

    persist=False（dry-run / 执行开关关闭）：规则根本没被改（见 run_watch 结尾），
    事件面也不许写——否则「试运行」在生产事件文件里留下一条从未发生的处置记录。
    """
    if not persist:
        return
    record_event("watch_discard", str(rule.get("code") or ""),
                 f"[{agent}] {rule.get('code')} 条件位作废：{why}",
                 side="sell", alert=False)


def _direction_fix_event(agent: str, rule: dict, level: float, why: str,
                         persist: bool = True) -> None:
    """方向翻转留痕（review-p03 LOW）：翻转会**落盘**改写条件位语义，此前只有 print。

    事件 id 带价位不按日合并：同一 code 的两个不同价位各自可查；两条同款规则（同 code
    同价位）翻的是同一件事，合并成一条。alert=False——不是资金风险，但审计面必须有。
    """
    if not persist:
        return
    code = str(rule.get("code") or "")
    record_event("watch_direction_fix", code, f"[{agent}] {code} {why}",
                 side="sell", alert=False,
                 key=f"watch_direction_fix:{agent}:{code}:{level:.2f}")


def _fix_rule_direction(agent: str, rule: dict, price: float, prev: float,
                        persist: bool = True) -> None:
    """方向自洽护栏：止损该在下方等下跌打到、止盈该在上方等上涨打到。

    2026-09-10 实录：复盘同步把『涨到17.90减50%』写成 stop_loss，现价 17.50 在阈值
    下方 → `price <= stop_loss` 成立，开盘即反向假触发（09-09 的 18.10/80.00 同款；
    当日三条真实条件位还被空快照销毁，见 run_watch 的桥假活硬闸）。

    标错判定（按优先级）：
      1) reason 文字线索（『涨到/站上/止盈』vs『跌破/失守/止损』）与存档方向不符；
      2) 无文字线索时看昨收：止损高于昨收且现价**严格**在昨收上方（涨着却挂了上方止损）
         = 标错；现价 ≤ 昨收（**含恰等于**）→ 不翻——等值是「证据缺席点」而不是
         「仍在昨收上方」的证据，且也可能是隔夜跳空打穿/低开贴线的真止损。此时按
         规则原文执行：宁可提前不到一个百分点先减一档（与「先降一档/接近即减」类
         理由文本不矛盾，下方通常还有更低的真止损兜底），也不替用户解除止损保护
         （失败方向只能是「卖早了」）。边界由 test_tie_price_equals_prev_close_is_not_flipped 钉住。
    翻转按触发语义做（该涨到位卖的改记止盈、该跌到位卖的改记止损），日志留痕；
    move_stop 上移而来的止损是浮盈锁利（可高于昨收），豁免。
    翻转/丢弃都是**语义改写**，persist=True 时同时落事件面（dry-run 不写）。"""
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
                _direction_fix_event(agent, rule, sl_f, why, persist)
            else:
                rule["stop_loss"] = None
                why += " → 丢弃该侧"
                _discard_event(agent, rule, why, persist)
            _notify_skip(rule, f"🔧 [{agent}] {rule['code']}: {why}")
    if tp_f is not None:
        why = _mislabel_reason("take_profit", tp_f, price, prev, hint, kw)
        if why:
            if rule.get("stop_loss") is None:
                rule["stop_loss"], rule["take_profit"] = tp_f, None
                why += " → 按止损执行"
                _direction_fix_event(agent, rule, tp_f, why, persist)
            else:
                rule["take_profit"] = None
                why += " → 丢弃该侧"
                _discard_event(agent, rule, why, persist)
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


def _watch_plan_id(agent: str, rule: dict) -> str:
    """条件位卖出的幂等委托号：同一条件位重试复用同号，桥内按 plan_id 去重。

    崩溃窗口（2026-09-11 移植 quantmind HIGH 2）：下单已被桥受理、本地还没存下
    （进程被杀/断电/共享盘抖动）→ 下一分钟重试。若每次生成新号，桥会照单再下一笔
    （重复卖出）；同号则桥回 status=duplicate，本地按「已下过」收尾。
    条件位被整点分析整组刷新（created_ts 变）时换号——那是新决策，必须能真下单。

    plan_seq（2026-09-12）：终态未成交（废单）**重新布防**时必须再换一次号——
    桥的 plan 去重记在内存里（重启即丢），同号重下会被永久判 duplicate，规则
    再也拿不到真单。plan_seq 是本条规则的重布防代数，只增不减。

    created_ts 取到**微秒**（[:20]，不是只到分钟）：一次分析整组写下多条规则，
    同一分钟内同代码的多条（001312 就有 17.05/16.60/17.05 三条）若共用一个号，
    第一条真下单后，桥的**入口去重**会让兄弟规则永久判 duplicate——回捞到的唯一
    候选又是已记账的那笔（不接管）→ dup_unresolved 每分钟空转，**更深的那道
    全仓止损静默失效**。到微秒后 plan 号与 _rule_key（code, created_ts）一一对应：
    一条规则一个号，重试仍复用同号。"""
    stamp = re.sub(r"\D", "", str(rule.get("created_ts") or ""))[:20]
    if not stamp:
        stamp = f"{now_cn():%Y%m%d}"
    seq = int(_fnum(rule.get("plan_seq"), 0))
    return f"watch-{agent}-{rule.get('code')}-{stamp}" + (f"-r{seq}" if seq else "")


def _same_code(a, b) -> bool:
    """代码同票判定（忽略 .SH/.SZ 后缀差异——两侧来源格式不一致时仍能对上）。"""
    return str(a or "").split(".")[0] == str(b or "").split(".")[0]


def _fnum(v, default: float = 0.0) -> float:
    """桥回报里的数字字段容错解析（字符串/浮点都收，脏值回退默认）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _recover_duplicate(broker, code: str, vol: int, limit: float) -> dict:
    """桥判重复（409 DUPLICATE_PLAN / 内联 duplicate）后，回捞**可接管**的当日委托。

    优选「同代码+同价+同量」的卖单；同代码卖单**只此一笔**时才退化接它（价格可能
    被手数合规调整过）。四处找「能接管的单」会把别的 agent 的成交记到本 agent 头上。

    接管判据（缺一不可，2026-09-11 审查 HIGH A）：
      1. 该委托号没进过当日成交流水（fill_recorded）——接管是从
         volume_recorded=0 开始补记的，已记过账的再挂一次 = 同一笔卖出双记
         （半仓止损先成交记账，剩余仓位的另一条规则再触发就会撞上）；
      2. 不在在途表里（在的话调用方早被在途闸门挡住，这里只做防御性检查）。
    候选**不唯一**时不猜（2026-09-11 审查 MEDIUM）：同代码多笔可接管单并存
    （两次「下单成功但 add_pending 前进程崩」的窗口）时按「最新一笔」接管会把
    别人的成交记进本规则的分账——错账比不接管更难查，宁可返回 {} 落
    dup_unresolved 事件等人工。回捞不到/不可接管返回 {}，由调用方告警并保留条件位。

    终态未成交的候选单独标记返回「_terminal_unfilled」（2026-09-12 P0-3）：
    桥判重复是因为那个 plan 被受理过，但那笔单已被柜台判废（001312 实录
    成交 0/300）。挂它进在途表没有意义（reconcile 下一轮立刻移除），还会让哨兵
    陷入「触发→桥判重复→回捞同一笔废单→重挂→再移除」的每分钟空转；正确动作是
    换 plan 号重新布防（调用方做）。
    """
    try:
        orders = [o for o in broker.get_orders()
                  if str(o.get("side") or "") == "sell" and _same_code(o.get("stock_code"), code)]
    except Exception:  # noqa: BLE001  桥查询失败：交给调用方记事件，不阻断
        return {}
    if not orders:
        return {}
    orders.sort(key=lambda o: str(o.get("time") or ""), reverse=True)   # 新的优先
    exact = [o for o in orders
             if abs(_fnum(o.get("order_price")) - _fnum(limit)) < 0.005
             and int(_fnum(o.get("total_volume"))) == int(vol)]
    if len(exact) > 1:
        return {}
    if exact:
        cand = exact[0]
    elif len(orders) == 1:
        cand = orders[0]          # 同代码卖单只此一笔：只能是它（量价取桥回报真值）
    else:
        return {}                 # 多笔且无一精确匹配：同样不猜
    oid = str(cand.get("order_id") or "")
    if not oid:
        return {}
    status = str(cand.get("status") or "")
    filled = int(_fnum(cand.get("filled_volume")))
    from live_fills import fill_recorded, load_pending

    if any(str(p.get("order_id") or "") == oid for p in load_pending()):
        return {}
    if fill_recorded(oid):
        return {}
    if status in CANCEL_STATUSES and filled <= 0:
        # 终态未成交：接管没意义（下次对账立刻移除），但**不能和「可接管」混为一谈**——
        # 调用方据此换 plan 号重新布防。放在两个「不可接管」判据之后（2026-09-12
        # 审查 LOW）：候选不按 agent/plan 过滤，已记账的单不该在这儿被标成废单。
        return {"_terminal_unfilled": True, "order_id": oid, "status": status,
                "wanted": int(_fnum(cand.get("total_volume")) or vol)}
    return cand


def _execute_sell(broker, agent: str, rule: dict, price: float, prev: float,
                  trig: str, avail, dry_run: bool = False,
                  after_hours: bool = False,
                  trig_level: float | None = None) -> str:
    """条件位触发 → 卖出（与盘中执行同一套闸门）。

    返回（2026-09-12 P0-3 由 bool 改为字符串——旧的两态表达不了「已下单但
    卖出尚未完成」，正是 001312 事故的根：下单即被当成"已消费"）：
      "placed"  已下单并挂上成交跟踪 → 规则**保留**并打标 pending_order_id，
                由 _resolve_tagged 等这笔委托的归宿
      "keep"    本轮没动作/失败/在途未确认 → 规则原样保留，下次再守
      "discard" 规则使命已了且不必再守（已不在持仓 / 无合法可卖量）→ 可移除，
                同时落 watch_discard 事件留痕
    """
    code = rule["code"]
    trig_value = trig_level if trig_level is not None else rule.get(trig)
    if avail is None:
        # 2026-09-10 实录：桥假活时 account/query 的 positions 为空，这里会把真实持仓的
        # 止损位当"已清仓"销毁。先与分账台账交叉核对：台账仍持有 → 保留条件位。
        if _ledger_holds(agent, code):
            _notify_skip(rule, f"⏭️ [{agent}] {code}: 账户快照无此持仓但台账仍持有，"
                               f"疑似账号掉线——条件位保留，勿当已清仓")
            return "keep"
        print(f"  🗑️ [{agent}] {code}: 已不在持仓中，条件位作废")
        _discard_event(agent, rule, "账户无此持仓且分账台账一致（已清仓）")
        return "discard"
    if avail <= 0:
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 可卖量 0（T+1 当日买入），条件位保留待明日")
        return "keep"
    # 在途闸门（2026-09-11 移植 quantmind）：同标的同方向已有未确认委托 → 不再下第二笔。
    # 覆盖「下单成功但本地没存下」之外的第二个窗口：add_pending 写了、条件位还没落盘。
    pend = inflight(code, "sell")
    if pend:
        _notify_skip(rule, f"⏭️ [{agent}] {code}: 已有在途卖单 {pend[0].get('order_id')}"
                           f"（{pend[0].get('volume')} 股）未确认，本轮不再下单")
        return "keep"
    raw_qty = int(avail * _pct(rule.get("pct")))
    vol = round_sell_qty(code, raw_qty, avail)
    if vol <= 0:
        print(f"  🗑️ [{agent}] {code}: 可卖量 {avail} 股按 {rule.get('pct', 1.0):.0%}"
              f"无合法可卖量（板块手数口径），条件位作废")
        _discard_event(agent, rule,
                       f"可卖量 {avail} 股按 {rule.get('pct', 1.0):.0%} 无合法可卖量")
        return "discard"
    chg = (price - prev) / prev * 100 if prev else 0.0
    at_ld = at_limit_down(code, chg)
    # 卖出保护价（2026-09-11 移植 quantmind 真账户实测）：报**跌停价**——挂单价只是
    # 「愿卖的最低」，成交仍按盘口买一（挂 2.21 成交 2.34）。止损语义 = 一定要卖掉：
    # 只要盘口有买盘就必然成交，且报价一定在合法带内（原「现价×0.99」在近跌停时会
    # 报出低于跌停价的价，本仓桥容忍、真柜台可能废单）。
    protect = protect_sell_price(code, prev) if not after_hours else None
    fallback = False
    if after_hours:
        limit = price            # 盘后固定价格交易以收盘价撮合
    elif protect is not None:
        limit = protect
    else:
        limit = round(price * 0.99, 2)   # 昨收缺失/非法：降级回原口径并留痕，不臆造价格
        fallback = True
        print(f"  ⚠️ [{agent}] {code}: 昨收缺失，取不到跌停保护价，"
              f"降级按现价-1% ¥{limit:.2f} 报单")
    label = "跌破止损" if trig == "stop_loss" else "达到止盈"
    if dry_run:
        # 只报不卖 —— 规则**不消费**（返回 keep）。曾返回 True 当"已消费"，
        # 结果是开关关闭期间触发的止损被静默吃掉（2026-09-11 600309 实录）。
        # 调用方 run_watch 已在 dry-run 时提前分流，这里是二次兜底。
        print(f"  🟡 DRY-RUN 卖出 {code} {vol}/{avail} 股 限价 ¥{limit:.2f}（{label}）——"
              f"条件位保留")
        return "keep"
    plan_id = _watch_plan_id(agent, rule)
    try:
        result = broker.sell(None, None, code, vol, price=limit, plan_id=plan_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
        record_event("sell_failed", code,
                     f"[{agent}] {code} {label}卖出下单失败：{str(exc)[:120]}"
                     f"（条件位保留，下一分钟重试；持续失败=持仓当日无保护）")
        _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                   "code": code, "volume": vol, "price": limit,
                   "trigger": trig_value, "error": str(exc)})
        return "keep"  # 下次重试
    if str(result.get("status") or "") == "duplicate":
        # 桥判定「这个 plan 已执行过」（409 DUPLICATE_PLAN 或内联 duplicate）=
        # 上次那笔已在柜台。回捞**未记账**的委托号补挂 pending；条件位一律保留——
        # 卖出真完成（或持仓清零）之前继续守，期间由在途闸门保证不下第二笔，
        # 持仓一旦清零下次触发就会走「已不在持仓中」自动作废。消费掉条件位等于
        # 把没卖出去的持仓从保护里摘出去（2026-09-11 审查 HIGH A）。
        recovered = _recover_duplicate(broker, code, vol, limit)
        oid = str(recovered.get("order_id") or "")
        if recovered.get("_terminal_unfilled"):
            # 唯一在案的委托是**终态未成交**（废单/被撤）：桥判重复只是因为那个
            # plan 号被受理过。挂 pending 会被 reconcile 立刻移除（每分钟空转），
            # 正确动作是把归宿落进终态台账 + 换 plan 号重新布防——下一轮触发时用
            # 新号真下单（同号会被桥永久判 duplicate）。
            from live_fills import save_outcome

            rwanted = int(recovered.get("wanted") or vol)
            save_outcome(oid, str(recovered.get("status") or ""), 0, rwanted)
            rule["plan_seq"] = int(_fnum(rule.get("plan_seq"))) + 1
            print(f"  ♻️ [{agent}] {code}: 桥判重复，但当日唯一在案委托 {oid} 是终态"
                  f"（{recovered.get('status')}，0/{rwanted} 股成交）——"
                  f"换号重新布防，下一轮触发时真下单")
            record_event("refire", code,
                         f"[{agent}] {code} 条件位委托 {oid} 终态"
                         f"{recovered.get('status')}、0/{rwanted} 股成交：已换委托号重新"
                         f"布防（下一轮触发时重新下单）——这笔单从未进过在途表，"
                         f"对账不会为它发事件，故此处必须上告警面（持仓此刻无保护）",
                         side="sell", alert=True, key=f"refire:{code}:{oid}")
            _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                       "code": code, "volume": vol, "price": limit, "plan_id": plan_id,
                       "trigger": trig_value, "terminal_unfilled": True,
                       "order_id": oid, "status": recovered.get("status"),
                       "result": result})
            return "keep"
        if oid:
            # 挂 pending 用**桥回报的真值**（量/价），不用本规则此刻算出的 vol/limit：
            # 回捞到的单可能量价都不同（手数合规调整过/改过仓位的旧单），按我们的数挂
            # 会永远等不到「filled >= wanted」→ 条目压在在途表把该代码的卖出闸门封到收盘
            rvol = int(_fnum(recovered.get("total_volume"), vol) or vol)
            rprice = _fnum(recovered.get("order_price")) or limit
            print(f"  ♻️ [{agent}] {code}: 桥判重复（上次已受理），回捞委托号 "
                  f"{oid}（{rvol} 股 限价 ¥{rprice:.2f}）补挂成交跟踪"
                  f"（条件位保留至卖出完成）")
            from live_fills import add_pending

            add_pending(oid, agent, code, "sell", rvol, rprice,
                        now_cn().isoformat(), protect=at_ld)
            _tag_pending(rule, oid, rvol)
        else:
            # 桥的**两处**去重都回 status=duplicate，但含义完全不同，必须让桥的原话
            # 露出来（2026-09-12）：409 plan 级去重（这个 plan 号已执行过）才是
            # 「单可能已在柜台、成交未记账」；而 `plan_executor._execute_one` 的
            # 当日同向去重（当日已有同代码同方向**成交** →「当日已有同方向成交, 跳过」）
            # 是**这笔根本没进柜台**——条件位留着要等下一个交易日（当日再触发多少次
            # 都一样会被跳过），把它读成「已在柜台」会去查一笔不存在的委托。
            why = str(result.get("message") or "").strip()
            print(f"  ⚠️ [{agent}] {code}: 桥判重复但回捞不到可接管的委托——"
                  f"状态未知（可能已在柜台、也可能没进去），条件位保留，需人工核对"
                  + (f"；桥回执：{why}" if why else ""))
            record_event("dup_unresolved", code,
                         f"[{agent}] {code} 卖出被桥判重复（上次已受理）但当日委托里"
                         f"找不到可接管的单：若已在柜台则成交未记账，需人工核对"
                         + (f"（桥回执：{why}）" if why else ""))
        _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                   "code": code, "volume": vol, "price": limit, "plan_id": plan_id,
                   "trigger": trig_value, "duplicate": True,
                   "recovered_order_id": oid, "result": result})
        return "keep"
    if not str(result.get("order_id") or ""):
        # 桥回 200 但没给委托号：单可能已在柜台，系统却跟踪不了（挂不上 pending、
        # reconcile 补记不了）。**不消费条件位**——下一分钟用同一 plan_id 重试；
        # 若那笔其实已受理，桥判 duplicate → 上面的 _recover_duplicate 回捞委托号，
        # 两条路都收敛（不重复卖、也不丢账）。2026-09-11 之前这里会打印「已受理」
        # 并 return True，成交成了账外单。
        print(f"  ❌ [{agent}] {code}: 桥未返回委托号，成交无法跟踪——"
              f"条件位保留，下一分钟同号重试（若已受理将回捞委托号）")
        record_event("no_order_id", code,
                     f"[{agent}] {code} {label}卖出桥未返回委托号：单可能已在柜台，"
                     f"成交未记账，需人工核对当日委托（限价 ¥{limit}）")
        _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
                   "code": code, "volume": vol, "price": limit, "plan_id": plan_id,
                   "trigger": trig_value, "result": result, "no_order_id": True})
        return "keep"
    if at_ld:
        # 跌停封死不是「跳过」而是挂队（2026-09-11 移植）：无买盘本来就卖不掉，
        # 系统职责是如实告警而非假装能卖；有买盘则按买一价成交。
        record_event("protect_queue", code,
                     f"[{agent}] {code} 已跌停（{chg:+.2f}%），{label}卖出已在跌停价 "
                     f"¥{limit:.2f} 挂队 {vol} 股：有买盘即成交，封死则排队等开板")
    from live_fills import ack_line

    print(f"  {ack_line(f'[{agent}] 卖出 {code} {vol} 股 限价 ¥{limit:.2f}', result)}")
    if vol != raw_qty:
        print(f"  ⚖️ [{agent}] {code}: 意图 {raw_qty} 股 → 手数合规实际 {vol} 股"
              f"（{board_of(code)} 口径）")
    # 成交记账走 pending/reconcile 通道：按真实成交价回填 + fill_confirm 进交易流水
    # （2026-09-04 复盘：此前直接按限价记账，fill 回报缺失、审计/复盘漏掉哨兵成交）
    from live_fills import add_pending

    oid = str(result.get("order_id"))
    add_pending(oid, agent, code, "sell", vol, limit,
                now_cn().isoformat(), protect=at_ld)
    # 下单成功 ≠ 卖出完成。规则**保留并打标**，由 _resolve_tagged 等这笔委托的归宿：
    # 满额成交/持仓清零 → 消费；终态未卖完 → 换号重布防；台账缺该号 → 转人工。
    # （2026-09-12 之前这里 return True = 下单即消费，废单后持仓当日裸奔。）
    _tag_pending(rule, oid, vol)
    _log_line({"ts": now_cn().isoformat(), "mode": f"watch_{trig}", "agent": agent,
               "code": code, "volume": vol, "intent_volume": raw_qty, "price": limit,
               "plan_id": plan_id, "protect": at_ld or None,
               "protect_fallback": fallback or None,
               "trigger": trig_value, "pct": rule.get("pct"),
               "reason": rule.get("reason"), "pending_order_id": oid,
               "result": result})
    return "placed"


def _tag_pending(rule: dict, order_id: str, volume: int) -> None:
    """规则打标：这笔条件位卖出已下单，等它的归宿（见 _resolve_tagged）。"""
    rule["pending_order_id"] = str(order_id)
    rule["pending_volume"] = int(volume)
    rule["fired_ts"] = now_cn().isoformat()


def _notify_skip(rule: dict, msg: str) -> None:
    """同一规则同一小时只提醒一次跳过原因（T+1/跌停每分钟都会撞上，防刷屏）。"""
    hour_tag = f"{now_cn():%Y%m%d%H}"
    if rule.get("_skip_notified") == hour_tag:
        return
    rule["_skip_notified"] = hour_tag
    print(f"  {msg}")


def _stale_outcome(rule: dict, oc: dict) -> bool:
    """台账记录写于本规则下单**之前** → 是隔日同号残留，不是这笔委托的归宿。

    台账窗口 24h 必然跨日，而 A 股委托号每日重排：昨天那个 "T1001" 的终态可能还在
    文件里，今天的 "T1001" 就会读到它（2026-09-12 审查 LOW）。判据用「早于下单
    时刻」而不是「不同日期」——隔夜过期单的台账本来就是**次日**才写的，按日相等
    会把「废单后重新布防」这条主路径一起误杀。

    两边都要能取出日期才比（fired_ts 由本模块写 ISO 时间戳；取不到日期说明字段
    被手改/是占位值）→ 不做判断，交回原有判据，别用假比较制造新的静默分支。
    """
    oc_day = re.search(r"\d{4}-\d{2}-\d{2}", str(oc.get("ts") or ""))
    fired_day = re.search(r"\d{4}-\d{2}-\d{2}", str(rule.get("fired_ts") or ""))
    if not (oc_day and fired_day):
        return False
    return oc_day.group() < fired_day.group()


def _resolve_tagged(agent: str, rule: dict, pend_ids: set) -> str:
    """已下单条件位的生命周期推进（每轮开头、不触发新单）。

    规则一旦打上 pending_order_id，就进入「等这笔委托的归宿」状态。2026-09-12
    之前是「下单即消费」：001312 那笔单随后被柜台判废（成交 0/300），条件位已经
    消失，持仓当日再无保护。判据（按顺序）：

      - 委托仍在在途表 → 等（在途闸门是第二道保险，不在本轮重复下单）；
      - 分账台账已不再持有该票 → 消费（含「全部卖光」的正常收尾）；
      - 终态台账 filled >= pending_volume → 消费（这一笔卖完了）；
      - 终态台账 filled <  pending_volume → 清标记 + 换 plan 号 + refire 事件
        （没卖完/废单 → 重新布防，下一轮起可再次触发）；其中 **expired** 的成交
        量是「我们没观察到」，不是「柜台没成交」→ 事件升级为告警，让账实差异
        有人核对（2026-09-12 审查 MEDIUM）；
      - 终态台账没有该委托号（或只剩隔日同号残留）→ **不重布防**，记事件转人工
        （状态未知不许猜）。
    返回 "keep"（等）/ "consume"（消费规则）/ "rearm"（已重布防，规则保留）。
    """
    oid = str(rule.get("pending_order_id") or "")
    code = rule["code"]
    if oid in pend_ids:
        return "keep"
    if not _ledger_holds(agent, code):
        print(f"  ✅ [{agent}] {code}: 条件位委托 {oid} 已了结且台账无此持仓，消费")
        return "consume"
    from live_fills import load_order_outcome

    oc = load_order_outcome(oid)
    why = "在途表已移除、终态台账无记录"
    if oc is not None and _stale_outcome(rule, oc):
        oc = None
        why = "台账里只有早于本规则下单时刻的隔日同号记录（委托号每日重排）"
    wanted = int(_fnum(rule.get("pending_volume")) or _fnum((oc or {}).get("wanted")))
    if oc is None or wanted <= 0:
        # 状态未知（台账被清/进程崩在 add_pending 前/隔日同号残留）→ 不猜、不重布防：
        # 重布防会再下一笔真单，而这一笔可能已在柜台成交（同号又会被判重复）。
        _notify_skip(rule, f"⚠️ [{agent}] {code}: 条件位委托 {oid} 已离开在途表但查不到"
                           f"终态台账——保持静默待人工核对（不自动重布防）")
        record_event("outcome_unknown", code,
                     f"[{agent}] {code} 条件位委托 {oid} 去向未知（{why}）："
                     f"不自动重布防，需人工核对当日委托与账户",
                     side="sell")
        return "keep"
    filled = int(_fnum(oc.get("filled")))
    if filled >= wanted:
        print(f"  ✅ [{agent}] {code}: 条件位委托 {oid} 成交 {filled}/{wanted} 股，消费")
        # 条件位从这一刻消失，旧实现只留一行 print（001312 事后在整个事件面里查不到
        # 任何「条件位为什么没了」的记录）。落一条非告警事件当审计尾巴；alert=False：
        # 满额成交/清仓清空台账都是设计内的正常收尾，不打扰人。
        record_event("watch_consumed", code,
                     f"[{agent}] {code} 条件位委托 {oid} 已成交 {filled}/{wanted} 股，"
                     f"条件位消费（该防守位到此结束）",
                     side="sell", alert=False)
        return "consume"
    # 没卖完（废单 0 成交 / 部分成交后失效）→ 重新布防。必须换 plan 号：
    # 桥的 plan 去重记在内存里，同号重下会被永久判 duplicate，规则再也拿不到真单。
    expired = str(oc.get("status") or "") == "expired"
    rule.pop("pending_order_id", None)
    rule.pop("pending_volume", None)
    rule.pop("fired_ts", None)
    rule["plan_seq"] = int(_fnum(rule.get("plan_seq"))) + 1
    print(f"  ♻️ [{agent}] {code}: 条件位委托 {oid} 终态 {oc.get('status')}，"
          f"成交 {filled}/{wanted} 股——未卖完，已重新布防（下一轮可再触发）")
    record_event("refire", code,
                 f"[{agent}] {code} 条件位委托 {oid} 终态 {oc.get('status')}："
                 f"成交 {filled}/{wanted} 股，未卖出的部分已重新布防"
                 f"（下一轮触发时重新下单）"
                 + ("。注：隔夜过期的单对账观察不到成交，这个成交数是**已记账量**"
                    "而非柜台实况——若昨日实际已成交，请核对账本，避免重复卖出"
                    if expired else ""),
                 side="sell", alert=expired, key=f"refire:{code}:{oid}")
    return "rearm"


def in_close_auction(now) -> bool:
    """收盘集合竞价（北京 14:57-15:00）不触发实单：竞价撮合规则不同，
    哨兵限价单语义失效；条件保留至盘后定价窗口或明日盘中。"""
    hm = now.hour * 60 + now.minute
    return 14 * 60 + 57 <= hm < 15 * 60


def run_watch(broker, dry_run: bool = False, after_hours: bool = False, now=None) -> dict:
    """轮询全部条件位，返回本轮计数 {"placed": 新下单, "consumed": 消费, "discarded": 作废}。

    now：本轮基准时刻（北京时区），用于收盘集合竞价护栏；缺省取当前时间。
    """
    from live_fills import reconcile, load_pending

    now = now or now_cn()
    counts = {"placed": 0, "consumed": 0, "discarded": 0}

    try:
        reconcile(broker)  # 先补记在途成交，再守条件位
    except Exception:  # noqa: BLE001
        pass
    # 总开关联动：配置关掉自动执行时，哨兵也只打印不真卖（防验证测试误触实盘）
    from live_hourly_analysis import intraday_exec_enabled

    exec_off = (not dry_run) and (not intraday_exec_enabled())
    if exec_off:
        print("  🔒 自动执行开关已关（intraday_exec.json），哨兵只打印不真卖")
        dry_run = True
    rules = load_watch()
    if not rules:
        return counts
    # 快照供结尾差量回写用（本轮可能跑十几秒，期间别人可能改了同一文件）
    snapshot = copy.deepcopy(rules)
    # 在途委托号集合：已下单规则据此判定「这笔委托还在不在」（reconcile 刚跑过，
    # 桥查不到的委托当日会保留在途表，所以「不在表里」才是可靠的终态信号）
    pend_ids = {str(p.get("order_id") or "") for p in load_pending()}
    try:
        acct = broker._account_query()
    except Exception as exc:  # noqa: BLE001  桥重启窗口/断线：本轮放弃，下分钟再守
        print(f"  ⚠️ 账户查询失败，本轮哨兵跳过（{str(exc)[:80]}）")
        return counts
    # 桥假活硬闸（2026-09-10）：HTTP 通、行情正常，但账户通道返空（交易账号未登录），
    # 空快照下每条真实规则都会被 _execute_sell 判成"已不在持仓中"销毁。
    deg = _degenerate_reason(acct)
    if deg:
        _notify_once("account_degenerate",
                     f"🚫 账户快照不可信（{deg}），本轮哨兵跳过、条件位全部保留——"
                     f"行情能看但下单/持仓查询哑火，需 RDP 重登 Windows 交易机通达信")
        return counts
    positions = acct.get("positions") or []
    avail_map = {p.get("stock_code"): int(p.get("available_volume") or 0)
                 for p in positions}
    for agent in list(rules):
        kept = []
        for r in rules[agent]:
            # 已下单的规则：本轮只推进生命周期（等/消费/重布防），不重算方向与价位、
            # 不触发新单（价位改写会让重布防后的规则漂移；在途闸门是第二道保险）
            if r.get("pending_order_id"):
                if dry_run:
                    _notify_skip(r, f"⏭️ [{agent}] {r['code']} 有在途条件位委托 "
                                    f"{r['pending_order_id']}（dry-run 不推进生命周期）")
                    kept.append(r)
                    continue
                act = _resolve_tagged(agent, r, pend_ids)
                if act == "consume":
                    counts["consumed"] += 1
                else:
                    kept.append(r)
                continue
            price, prev = _last_price(broker, r["code"])
            if price is None:
                _notify_skip(r, f"⚠️ [{agent}] {r['code']} 行情获取失败，条件位保留")
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            # 方向自洽：先按文字线索/昨收校正标错的止损止盈，再谈触发
            # （move_stop 上移过的止损可以高于昨收，故豁免）
            # persist：dry-run 下护栏的改写不落盘（见结尾注释），事件面同样不写
            if not r.get("stop_from_move"):
                _fix_rule_direction(agent, r, price, prev, persist=not dry_run)
            # move_stop：价格触及后止损一次性上移到该价（只上不下，跟踪保护）。
            # 触发判定用**本轮开始时**的止损（stop_at_entry）：price == move_stop 时
            # 旧实现「上移到该价位 → 紧接着 price <= 新止损」成立，同轮立刻反向卖出。
            stop_at_entry = _fnum(r.get("stop_loss"))
            ms = r.get("move_stop")
            if ms:
                try:
                    ms = float(ms)
                except (TypeError, ValueError):
                    ms = None
            if ms and price >= ms and (r.get("stop_loss") or 0) < ms:
                print(f"  🔒 [{agent}] {r['code']} 触及 move_stop ¥{ms:.2f}，"
                      f"止损上移 ¥{r.get('stop_loss') or 0:.2f} → ¥{ms:.2f}（下一轮生效）")
                r["stop_loss"] = ms
                r["stop_from_move"] = True   # 浮盈锁利位，可高于昨收，别再被当方向标错
                r.pop("move_stop", None)
            if after_hours and not after_hours_eligible(r["code"]):
                kept.append(r)  # 主板等无盘后定价的标的，条件位保留至次日盘中
                continue
            trig, trig_level = None, None
            if stop_at_entry and price <= stop_at_entry:
                trig, trig_level = "stop_loss", stop_at_entry
            elif r.get("take_profit") and price >= r["take_profit"]:
                trig, trig_level = "take_profit", _fnum(r.get("take_profit"))
            if not trig:
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            label = "跌破止损" if trig == "stop_loss" else "达到止盈"
            notice = (f"  🎯 [{agent}] {r['code']} 现价 ¥{price:.2f} {label}位 "
                      f"¥{trig_level:.2f}（减仓 {r.get('pct', 1.0):.0%}）")
            if dry_run:
                # 只报不卖（执行开关未开 / --dry-run）→ 条件位必须保留、不消费。
                # 2026-09-11 实录：600309 的 75.50 止损在开关关闭时于 09:44 触发，
                # dry-run 分支照样按「已消费」上报 → 规则被吃掉、当天再无人守，
                # 持仓裸奔（价格 74.41 → 73.85 无保护）。提醒按规则整点去重防刷屏。
                _notify_skip(r, f"{notice} —— 执行开关未开/试运行，只报不卖、"
                                f"条件位保留: {r.get('reason', '')}")
                if exec_off:
                    # 开关关着触发的止损 = 持仓当日无保护，必须上告警面（此前只打印）
                    record_event("exec_disabled", r["code"],
                                 f"[{agent}] {r['code']} {label} ¥{price:.2f} 已触发，"
                                 f"但自动执行开关关闭 → 只报不卖（条件位保留）")
                kept.append(r)
                time.sleep(POLL_SLEEP_SEC)
                continue
            print(f"{notice}: {r.get('reason', '')}")
            if in_close_auction(now):
                print(f"  ⏭️ [{agent}] {r['code']} 收盘集合竞价时段（14:57-15:00），"
                      f"不触发实单，条件保留")
                kept.append(r)
                continue
            act = _execute_sell(broker, agent, r, price, prev, trig,
                                avail_map.get(r["code"]), dry_run=dry_run,
                                after_hours=after_hours, trig_level=trig_level)
            if act == "discard":
                counts["discarded"] += 1
            else:
                if act == "placed":
                    counts["placed"] += 1
                    # 已下单：规则**必须继续留在 kept 里**（_execute_sell 已打标
                    # pending_order_id）——漏掉它，结尾 flush_watch 会把这条规则
                    # 当「本轮消费掉」从文件里删掉，等于回到「下单即消费」的老路
                kept.append(r)
            time.sleep(POLL_SLEEP_SEC)
        if kept:
            rules[agent] = kept
        else:
            rules.pop(agent, None)
    # dry-run（含执行开关关闭的自动降级）**不回写条件位文件**：本轮没有消费/丢弃，
    # 文件原样即「规则保留」；而 _fix_rule_direction / move_stop 的在内存改写若落盘，
    # 就成了「试运行改了状态」（且护栏依据的是当时那条行情，桥抖动时不该被固化）。
    # 告警面（exec_disabled / account_degenerate 事件）是既定的有意例外，不在此列。
    if not dry_run:
        flush_watch(snapshot, rules)
    return counts


def print_status() -> None:
    rules = load_watch()
    if not rules:
        print("📭 无 watch 条件位（等整点分析挂入）")
        return
    for agent, rs in rules.items():
        for r in rs:
            sl = f"止损 ¥{r['stop_loss']:.2f}" if r.get("stop_loss") else "止损 —"
            tp = f"止盈 ¥{r['take_profit']:.2f}" if r.get("take_profit") else "止盈 —"
            pend = ""
            if r.get("pending_order_id"):
                pend = (f" ⏳ 在途委托 {r['pending_order_id']}"
                        f"（{r.get('pending_volume')} 股，等归宿）")
            print(f"[{agent}] {r['code']}: {sl} / {tp}"
                  f"（触发卖出 {r.get('pct', 1.0):.0%}）{r.get('reason', '')}{pend}")


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
    counts = run_watch(broker, dry_run=args.dry_run, after_hours=ah, now=now)
    if sum(counts.values()):
        print(f"[{now:%F %T}] ⚠️ 条件位处理 {sum(counts.values())} 笔"
              f"（新下单 {counts['placed']} / 已了结 {counts['consumed']} /"
              f" 作废 {counts['discarded']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
