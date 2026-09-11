#!/usr/bin/env python3
"""成交回报跟踪：桥下单「已受理(submitted)」≠「成交(filled)」。

- wait_fill: 下单后短轮询桥当日委托，限价单通常秒成，直接按真实成交价/量记账
- add_pending / reconcile: 超时未成交的单挂 pending（data/live_pending_orders.json），
  由整点分析/分钟哨兵/每分钟净值采样兜底 reconcile——按成交增量补记分账账本，
  撤单/废单/隔日过期清除
- 账本只在真实成交后更新（此前按委托限价记账，成交价更优时账本少算/多算现金）

用法: 执行路径调 wait_fill；调度入口（整点/哨兵/record-only）开头调 reconcile(broker)。
reconcile 自带跨进程互斥（fcntl.flock，data/live_reconcile.lock）：三处入口都由 cron
在整分钟边界触发，撞车时后来者直接跳过（读-改-写无锁并发会把同一笔成交补记两次）。
**写者都要过同一把锁**（2026-09-11 审查 HIGH B）：reconcile 是「读 pending → 查桥
（网络往返）→ 整表写回」，add_pending / record_event 若不加锁，网络窗口里刚挂上的
新单会被整表回写冲掉（成交从此进不了账本）；故这两个写者阻塞等锁，非阻塞抢锁只留给
reconcile（抢占方跳过本轮，不排队积压）。
"""
import fcntl
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

PENDING_FILE = ROOT / "data" / "live_pending_orders.json"
# reconcile 跨进程互斥锁（见模块 docstring）；锁文件本身无内容，只看 flock 状态。
RECONCILE_LOCK_FILE = ROOT / "data" / "live_reconcile.lock"
# 需要人工知晓的委托事件（挂队/未成交/废单/收盘未了结）：alert.sh 读取并去重上报。
# 2026-09-11 之前这些只写 logs/*.jsonl，止损没卖出去也零告警。
EVENTS_FILE = ROOT / "data" / "live_order_events.json"
EVENT_KEEP_H = 24              # 事件保留窗口（alert.sh 每 5 分钟扫一遍，足够）
CLOSE_REMIND_FROM = 14 * 60 + 50   # 收盘前 10 分钟提醒在途单将随日终失效


def _load_dotenv() -> None:
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


# ---------- pending 在途单文件（reconcile 与执行路径共用） ----------

def load_pending() -> list:
    """[{order_id, agent, code, side, volume, price, volume_recorded, ts}]"""
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_pending(entries: list) -> None:
    """原子写，避免整点分析与每分钟采样并发读到半写文件。"""
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PENDING_FILE)


def add_pending(order_id, agent: str, code: str, side: str, volume: int,
                price: float, ts: str, protect: bool = False) -> None:
    """登记在途委托。protect=True 表示这是保护价（跌停价）挂队单：
    排队等买盘是它的正常形态，停滞告警时不可触发重启桥的自愈动作。

    没拿到委托号（桥响应缺 order_id）不能静默 return：无号 = 这笔单跟踪不了、
    成交也不会被 reconcile 补记，必须落事件让人核对当日委托（2026-09-11）。
    """
    if not order_id:
        record_event("untracked_order", code,
                     f"[{agent}] {side} {code} {int(volume)}股 委托未拿到委托号，"
                     f"成交无法记账（限价 ¥{price}）——需人工核对当日委托",
                     side=side)
        return
    entry = {
        "order_id": str(order_id), "agent": agent, "code": code, "side": side,
        "volume": int(volume), "price": float(price or 0), "volume_recorded": 0,
        "ts": ts,
    }
    if protect:
        entry["protect"] = True
    # 持锁读-改-写：reconcile 的整表回写（查桥的网络窗口里）会用旧快照覆盖文件，
    # 不持锁的新单会被静默抹掉 → 成交成账外单，且在途闸门也拦不住重复下单
    with _file_lock():
        pend = load_pending()
        pend.append(entry)
        save_pending(pend)


def inflight(code: str, side: str | None = None) -> list:
    """在途（已下单、尚无终态回报）委托中匹配 code（可选 side）的条目。"""
    return [p for p in load_pending()
            if p.get("code") == code and (side is None or p.get("side") == side)]


def inflight_codes(side: str | None = None) -> set:
    """在途委托涉及的代码集合（批量闸门用：一次读盘，逐条判定不重复读文件）。"""
    return {p.get("code") for p in load_pending()
            if side is None or p.get("side") == side}


# ---------- 委托事件（人工需知晓，alert.sh 上报） ----------

def _parse_ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=CN_TZ)


def record_event(kind: str, code: str, msg: str, *, side: str = "",
                 alert: bool = True, key: str | None = None,
                 now: datetime | None = None) -> str:
    """记一条委托事件；返回事件 id（= 去重键）。

    同一个 (kind, code, 当日) 只保留一条：重复记录覆盖旧值 → 每分钟跑的执行路径
    不会刷屏；alert.sh 按 id 去重，只打扰一次。24 小时前的旧事件顺带清掉。
    """
    now = now or now_cn()
    ev_id = key or f"{kind}:{code}:{now:%Y-%m-%d}"
    cutoff = now - timedelta(hours=EVENT_KEEP_H)
    with _file_lock():          # 读-改-写要排队：与对账/哨兵并发写事件时不许互相覆盖
        try:
            doc = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
            doc = doc if isinstance(doc, dict) else {}
        except (OSError, json.JSONDecodeError):
            doc = {}
        doc = {k: v for k, v in doc.items()
               if isinstance(v, dict) and (_parse_ts(v.get("ts")) or cutoff) >= cutoff}
        doc[ev_id] = {"ts": now.isoformat(), "kind": kind, "code": code, "side": side,
                      "msg": msg, "alert": bool(alert)}
        try:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = EVENTS_FILE.with_name(EVENTS_FILE.name + ".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(EVENTS_FILE)
        except OSError:
            pass  # 事件落盘失败不能反过来打断交易路径
    return ev_id


def fill_recorded(order_id: str, now: datetime | None = None) -> bool:
    """该委托号是否已经进过当日成交流水（= 已记过账，不能再接管重记）。

    两种记账形状都算：reconcile 的 fill_confirm 线（顶层 order_id）与下单路径
    成交后内联写的 fill 段（fill.order_id）。**不算**的是下单/挂 pending 的
    「result」嵌字段——那是委托，不是成交。
    """
    order_id = str(order_id or "")
    if not order_id:
        return True                      # 空号按「不许接管」处理，交给人工
    from live_trade_picks import LOG_DIR

    now = now or now_cn()
    path = LOG_DIR / f"live_trade_{now:%Y%m%d}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False                     # 当天还没有流水文件 = 什么都没记过
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if str(rec.get("order_id") or "") == order_id:
            return True
        if str((rec.get("fill") or {}).get("order_id") or "") == order_id:
            return True
    return False


def ack_line(head: str, result: dict | None) -> str:
    """下单回执的统一文案：受理以**委托号**为准（2026-09-11 审查 MEDIUM）。

    桥没返回委托号时不能印「✅ 已受理」——单可能已在柜台、也可能根本没进去，
    两种情况都必须让人看见（成交跟踪不了 / 需人工核对当日委托）。此前四条下单
    路径各自写死「已受理: {result}」，人看日志以为单在跟踪。
    """
    r = result if isinstance(result, dict) else {}
    oid = str(r.get("order_id") or "")
    if oid:
        return f"✅ {head} 已受理（委托号 {oid}）: {r}"
    return f"⚠️ {head} 未拿到委托号（受理状态未知，勿当已成交）: {r}"


# ---------- 成交查询 ----------

def is_star_market(code: str) -> bool:
    """科创板（688/689 开头）。"""
    from ashare_rules import is_star_market as _is_star

    return _is_star(code)


def round_sell_qty(code: str, raw: int, avail: int) -> int:
    """卖出量合规化——单一口径在 ashare_rules（板块手数/碎股一次性卖出规则）。"""
    from ashare_rules import round_sell_qty as _round

    return _round(code, raw, avail)


def wait_fill(broker, order_id, timeout_s: int = 30, interval: int = 3) -> dict | None:
    """下单后轮询桥当日委托匹配 order_id；返回有成交或有终态的回报，超时返回 None。"""
    if not order_id:
        return None
    # partial_cancelled（桥 TDX 状态 4：部分成交后被撤/失效）也是终态：
    # 不复投回等，已成交部分立刻按增量补记，未成交部分落未卖出事件
    terminal = ("cancelled", "withdrawn", "rejected", "expired",
                "partial_cancelled", "filled")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            for o in broker.get_orders():
                if o.get("order_id") != str(order_id):
                    continue
                if int(o.get("filled_volume") or 0) > 0 \
                        or str(o.get("status") or "") in terminal:
                    return o
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return None


_LOCK_DEPTH = threading.local()


@contextmanager
def _file_lock(blocking: bool = True):
    """跨进程互斥（flock，data/live_reconcile.lock）。yield True=可以继续写，
    yield False=非阻塞模式下被别人占着（调用方本轮跳过）。同进程同线程可重入
    （reconcile 持锁期间调 record_event/save_pending 不会自锁）。

    锁文件打不开（磁盘/权限）→ 告警并按**无锁继续**（yield True）：宁可偶发重复
    记账，也不能让补记彻底停摆。与「被别人抢占」是两回事——后者才该跳过
    （2026-09-11 审查 MEDIUM C：老实现把两种 OSError 混在一个分支里静默跳过，
    锁文件一旦建不出来，对账就每轮空转且零日志）。
    """
    depth = getattr(_LOCK_DEPTH, "depth", 0)
    if depth:                      # 已持锁（同线程嵌套）：直接放行
        _LOCK_DEPTH.depth = depth + 1
        try:
            yield True
        finally:
            _LOCK_DEPTH.depth = depth
        return
    fh = None
    locked = False
    try:
        RECONCILE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = open(RECONCILE_LOCK_FILE, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        locked = True
    except BlockingIOError:            # 非阻塞抢锁失败：别人正在写，正常跳过
        yield False
        return
    except OSError as exc:
        print(f"⚠️ live_fills: 锁文件不可用（{exc}），本次按无锁继续", file=sys.stderr)
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


def _reconcile_lock():
    """reconcile 专用：非阻塞抢锁——别人正在对账就跳过本轮（不排队积压）。"""
    return _file_lock(blocking=False)


def _recorded_baseline(ledger: dict, p: dict, today: str) -> int:
    """该委托「已记账成交量」的基准 = max(pending 的 volume_recorded, 账本幂等标记)。

    为什么不能只看 pending（2026-09-11 审查 MEDIUM）：save_ledger 与 save_pending
    不是一次原子写。中间被 kill（cron 叠跑）、OOM，或 log_line/record_event 落盘
    抛异常（异常穿透出 _reconcile_locked，整轮 pending 回写全丢）→ pending 仍停在
    旧的 volume_recorded → 下一轮把同一笔成交再记一次：现金多记、持仓多扣，账本
    自身看不出异常。标记与持仓变更走同一次 save_ledger 原子落地，最坏只是 pending
    慢一轮更新。

    只认当日标记：A 股委托号每日重排，昨天的同号标记必须失效（否则今天的新单
    会被旧标记吞掉，真成交永远不记账）。
    """
    applied = (ledger.get("applied_fills") or {}).get(p.get("order_id"))
    booked = int(applied.get("filled") or 0) \
        if isinstance(applied, dict) and applied.get("ts") == today else 0
    return max(int(p.get("volume_recorded") or 0), booked)


def _with_applied(cur, order_id: str, filled: int, today: str) -> dict:
    """刷新幂等标记（不可变，返回新表）；顺带清掉隔日条目，免得账本无限膨胀。"""
    out = {k: v for k, v in (cur or {}).items()
           if isinstance(v, dict) and v.get("ts") == today}
    out[str(order_id)] = {"filled": int(filled), "ts": today}
    return out


def reconcile(broker, now: datetime | None = None) -> int:
    """把在途单的成交增量按真实成交价/量记入分账账本。返回补记笔数。

    兜底场景：wait_fill 超时 / 脚本中断 / 部分成交后继续成交。
    终态（撤单/废单/满额成交）移除；隔日桥已查不到的单过期清除。
    未了结的终态（没卖出去）与收盘前仍在途的单会记一条事件 → alert.sh 上报。

    读-改-写全程持跨进程锁：哨兵（每分钟）/整点轮/record-only 采样会在同一
    分钟边界撞车，无锁并发下两边都会基于旧 volume_recorded 补记同一笔成交。
    补记本身幂等：已记账量取 pending 与账本 applied_fills 标记的较大者
    （见 _recorded_baseline），save_ledger 与 save_pending 之间崩溃也不会重复记账。
    """
    with _reconcile_lock() as got:
        if not got:
            return 0
        return _reconcile_locked(broker, now)


def _reconcile_locked(broker, now: datetime | None = None) -> int:
    """reconcile 的实际逻辑（调用方需已持有跨进程锁）。"""
    from live_ledger import load_ledger, record_buy, record_sell, save_ledger
    from live_trade_picks import log_line

    pend = load_pending()
    if not pend:
        return 0
    try:
        orders = {o.get("order_id"): o for o in broker.get_orders()}
    except Exception:  # noqa: BLE001
        return 0
    now = now or now_cn()
    today = now.strftime("%Y-%m-%d")
    hm = now.hour * 60 + now.minute
    fills, kept = 0, []
    for p in pend:
        tag = f"[{p.get('agent')}] {p.get('code')} {p.get('side')}"
        o = orders.get(p.get("order_id"))
        if not o:
            # 桥查不到：隔日过期（当日委托不保留），当日则继续等
            if str(p.get("ts", ""))[:10] < today:
                log_line({"ts": now.isoformat(), "mode": "fill_expire",
                          "agent": p.get("agent"), "code": p.get("code"),
                          "order_id": p.get("order_id"), "side": p.get("side")})
                # 隔夜过期 = 这笔单的归宿没被任何一轮对账观察到。若还有未记账的量，
                # 它可能昨天已成交（进程收盘前掉线、没人看到终态）→ 账外单，账本与
                # 账户永久不一致（2026-09-11：老实现只写日志，成交静默消失）。
                wanted = int(p.get("volume") or 0)
                unrecorded = wanted - int(p.get("volume_recorded") or 0)
                msg = (f"昨日委托 {p.get('order_id')}（{tag}）今日已查不到，"
                       f"{unrecorded}/{wanted} 股未记账——可能昨日已成交，需核对账户与账本"
                       if unrecorded > 0 else
                       f"昨日委托 {p.get('order_id')}（{tag}）过期清理（成交已全部记账）")
                record_event("fill_expire", p.get("code") or "", msg,
                             side=str(p.get("side") or ""), alert=unrecorded > 0, now=now)
            else:
                kept.append(p)
            continue
        status = str(o.get("status") or "")
        filled = int(o.get("filled_volume") or 0)
        wanted = int(p.get("volume") or 0)
        ledger = load_ledger()
        recorded = _recorded_baseline(ledger, p, today)
        if recorded > int(p.get("volume_recorded") or 0):
            p["volume_recorded"] = recorded      # 自愈：把落后的一侧追平（见上）
        delta = filled - recorded
        if delta > 0:
            fprice = float(o.get("filled_price") or p.get("price") or 0)
            prev_applied = ledger.get("applied_fills")
            cost_p = 0.0
            if p.get("side") != "buy":  # 卖出成交带成本基准（已完成 feed 盈亏用）
                cost_p = float((((ledger.get("agents") or {}).get(p["agent"]) or {})
                                .get("positions") or {}).get(p["code"], {}).get("cost_price") or 0)
            if p.get("side") == "buy":
                ledger = record_buy(ledger, p["agent"], p["code"], delta, fprice,
                                    now.isoformat())
            else:
                ledger = record_sell(ledger, p["agent"], p["code"], delta, fprice,
                                     now.isoformat())
            # 幂等标记与持仓变更同一次原子写落地：崩在 save_pending 之前也不会重复记账
            ledger["applied_fills"] = _with_applied(prev_applied, p.get("order_id"),
                                                    filled, today)
            save_ledger(ledger)
            log_line({"ts": now.isoformat(), "mode": "fill_confirm",
                      "agent": p["agent"], "code": p["code"], "side": p.get("side"),
                      "volume": delta, "price": fprice, "cost_price": cost_p,
                      "order_id": p.get("order_id")})
            p["volume_recorded"] = filled
            p["filled_price"] = fprice
            fills += 1
        if status in ("cancelled", "withdrawn", "rejected", "expired",
                      "partial_cancelled") or (
                status == "filled" and filled >= wanted):
            if status in ("cancelled", "withdrawn", "rejected", "expired",
                          "partial_cancelled"):
                if recorded < filled:
                    log_line({"ts": now.isoformat(), "mode": "fill_abort",
                              "agent": p.get("agent"), "code": p.get("code"),
                              "order_id": p.get("order_id"), "status": status})
                if filled < wanted:
                    # 止损/减仓单没卖出去（废单/被撤/失效）——持仓仍在裸奔，必须让人知道
                    record_event("unfilled", p["code"],
                                 f"委托终态 {status}：{tag} 成交 {filled}/{wanted} 股，"
                                 f"剩余 {wanted - filled} 股未卖出（限价 ¥{p.get('price')}）",
                                 side=str(p.get("side") or ""), now=now)
            continue  # 终态移除
        if CLOSE_REMIND_FROM <= hm < 15 * 60 and filled < wanted:
            # 收盘前仍在途：A股当日委托日终自动失效 → 提醒当日大概率卖不掉了
            record_event("close_pending", p["code"],
                         f"收盘前仍有在途委托未成交：{tag} 成交 {filled}/{wanted} 股，"
                         f"当日委托将随日终失效（限价 ¥{p.get('price')}）",
                         side=str(p.get("side") or ""), now=now)
        kept.append(p)
    save_pending(kept)
    return fills
