#!/usr/bin/env python3
"""成交回报跟踪：桥下单「已受理(submitted)」≠「成交(filled)」。

- wait_fill: 下单后短轮询桥当日委托，限价单通常秒成，直接按真实成交价/量记账
- add_pending / reconcile: 超时未成交的单挂 pending（data/live_pending_orders.json），
  由整点分析/分钟哨兵/每分钟净值采样兜底 reconcile——按成交增量补记分账账本，
  撤单/废单/隔日过期清除
- 账本只在真实成交后更新（此前按委托限价记账，成交价更优时账本少算/多算现金）

用法: 执行路径调 wait_fill；调度入口（整点/哨兵/record-only）开头调 reconcile(broker)。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

PENDING_FILE = ROOT / "data" / "live_pending_orders.json"
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
    排队等买盘是它的正常形态，停滞告警时不可触发重启桥的自愈动作。"""
    if not order_id:
        return
    pend = load_pending()
    entry = {
        "order_id": str(order_id), "agent": agent, "code": code, "side": side,
        "volume": int(volume), "price": float(price or 0), "volume_recorded": 0,
        "ts": ts,
    }
    if protect:
        entry["protect"] = True
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
    try:
        doc = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        doc = doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        doc = {}
    cutoff = now - timedelta(hours=EVENT_KEEP_H)
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
    terminal = ("cancelled", "withdrawn", "rejected", "expired", "filled")
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


def reconcile(broker, now: datetime | None = None) -> int:
    """把在途单的成交增量按真实成交价/量记入分账账本。返回补记笔数。

    兜底场景：wait_fill 超时 / 脚本中断 / 部分成交后继续成交。
    终态（撤单/废单/满额成交）移除；隔日桥已查不到的单过期清除。
    未了结的终态（没卖出去）与收盘前仍在途的单会记一条事件 → alert.sh 上报。
    """
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
        o = orders.get(p.get("order_id"))
        if not o:
            # 桥查不到：隔日过期（当日委托不保留），当日则继续等
            if str(p.get("ts", ""))[:10] < today:
                log_line({"ts": now.isoformat(), "mode": "fill_expire",
                          "agent": p.get("agent"), "code": p.get("code"),
                          "order_id": p.get("order_id"), "side": p.get("side")})
            else:
                kept.append(p)
            continue
        status = str(o.get("status") or "")
        filled = int(o.get("filled_volume") or 0)
        recorded = int(p.get("volume_recorded") or 0)
        wanted = int(p.get("volume") or 0)
        tag = f"[{p.get('agent')}] {p.get('code')} {p.get('side')}"
        delta = filled - recorded
        if delta > 0:
            fprice = float(o.get("filled_price") or p.get("price") or 0)
            ledger = load_ledger()
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
            save_ledger(ledger)
            log_line({"ts": now.isoformat(), "mode": "fill_confirm",
                      "agent": p["agent"], "code": p["code"], "side": p.get("side"),
                      "volume": delta, "price": fprice, "cost_price": cost_p,
                      "order_id": p.get("order_id")})
            p["volume_recorded"] = filled
            p["filled_price"] = fprice
            fills += 1
        if status in ("cancelled", "withdrawn", "rejected", "expired") or (
                status == "filled" and filled >= wanted):
            if status in ("cancelled", "withdrawn", "rejected", "expired"):
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
