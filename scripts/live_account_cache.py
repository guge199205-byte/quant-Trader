#!/usr/bin/env python3
"""桥账户快照缓存（2026-09-15）：为「桥断降级监控」提供最近一次真实账户数据。

采集：`TdxBridgeBroker._account_query` 每次**成功**返回时落盘快照（含资产/持仓/
可卖量/时间戳）。
消费：桥断时监控类脚本（杠杆守护 / 净值采样 / 哨兵降级告警）读最近快照 +
AiData 现价，继续出**估算**监控结果。

⚠️ 红线：快照只准监控/告警层用，**下单/委托/账户真值判定的交易链路绝不读**
（陈旧数据当实时用会做出错误交易决策）。消费方必须标注「估算」。

快照文件与容器共享（logs 卷挂载），宿主/容器写入同一份。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "logs" / "broker_account_cache.json"
CN_TZ = ZoneInfo("Asia/Shanghai")


def save_snapshot(data: dict) -> None:
    """broker 查询成功后调用；原子写、失败静默（绝不影响查询本身）。"""
    try:
        payload = {
            "ts": time.time(),
            # 2026-09-20 修复：原来是 time.strftime(...+08:00)——strftime 取机器本地
            # 时间（本机 JST = 北京+1h），却标成 +08:00，ts_cn 比真实北京时间快 1 小时。
            "ts_cn": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "asset": data.get("asset") or {},
            "positions": data.get("positions") or [],
            "channel_used": data.get("channel_used"),
        }
        tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def load_snapshot(max_age_min: float = 240) -> dict | None:
    """读最近快照；超过 max_age_min 分钟视为过旧返回 None（宁缺毋滥）。"""
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age_min = (time.time() - float(d.get("ts") or 0)) / 60
    if age_min > max_age_min:
        return None
    d["age_min"] = round(age_min, 1)
    return d


def snapshot_positions(snap: dict) -> dict:
    """快照 → {code: {"volume": int, "available": int}}。"""
    out = {}
    for p in snap.get("positions") or []:
        code = str(p.get("stock_code") or "")
        if not code:
            continue
        out[code] = {"volume": int(p.get("total_volume") or 0),
                     "available": int(p.get("available_volume") or 0)}
    return out


def ledger_positions(agent: str | None = None) -> dict:
    """降级兜底二源：本地分账台账（不含未分账持仓；可卖量按「非今日买入」近似）。"""
    try:
        ledger = json.loads((ROOT / "logs" / "live_ledger.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    today = time.strftime("%Y-%m-%d")
    out = {}
    agents = ledger.get("agents") or {}
    for name, rec in agents.items():
        if agent and name != agent:
            continue
        for code, pos in (rec.get("positions") or {}).items():
            vol = int(pos.get("volume") or 0)
            if vol <= 0:
                continue
            bought_today = str(pos.get("buy_ts") or "")[:10] == today
            entry = out.setdefault(code, {"volume": 0, "available": 0, "agents": []})
            entry["volume"] += vol
            entry["available"] += 0 if bought_today else vol
            entry["agents"].append(name)
    return out


_NOTIFY_STATE = ROOT / "logs" / "degraded_notify_state.json"


def notify_throttled(key: str, title: str, content: str, gap_min: float = 30) -> None:
    """降级告警去重（同 key 每 gap_min 分钟最多一条——桥断期脚本每分钟跑，防轰炸）。"""
    now_ts = time.time()
    try:
        state = json.loads(_NOTIFY_STATE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    if now_ts - float(state.get(key) or 0) < gap_min * 60:
        return
    from push_notify import notify

    notify(title, content)
    state[key] = now_ts
    try:
        _NOTIFY_STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    snap = load_snapshot()
    if not snap:
        print("无有效快照（缺失或过旧）")
        sys.exit(1)
    print(f"快照 {snap['ts_cn']}（{snap['age_min']} 分钟前）")
    asset = snap.get("asset") or {}
    print(f"资产 ¥{float(asset.get('asset') or 0):,.0f} 现金 ¥{float(asset.get('cash') or 0):,.0f}")
    for code, p in snapshot_positions(snap).items():
        print(f"  {code}: 总 {p['volume']} 可卖 {p['available']}")