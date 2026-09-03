"""盘面状态快照（确定性注入，模型不可争辩的事实层）。

设计（2026-09-03）：提示词是软约束，要让"大盘剧本/时段打法"真正生效，
把状态判定下沉到代码——指数趋势/量能/位置/时段由本模块按桥数据实时
计算，作为【盘面状态（系统判定）】注入每轮分析任务。模型只需把
当前状态映射到 persona 里的剧本动作基调，而不是自己拍脑袋定牛熊。
"""
import threading
import time
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
_cache: dict = {"ts": 0.0, "text": ""}
_lock = threading.Lock()
TTL = 300  # 5 分钟缓存（整点/多 agent 复用同一条，省桥调用）


def _index_stats(broker, code: str) -> dict | None:
    """近 12 根日K → 5日趋势/量能比/20日位置。失败返回 None（不阻断）。"""
    try:
        k = broker.get_klines(code, interval="daily")  # 250 根，取尾 12
    except Exception:  # noqa: BLE001
        return None
    k = (k or [])[-12:]
    if len(k) < 7:
        return None
    closes = [float(x.get("close") or 0) for x in k]
    vols = [float(x.get("volume") or 0) for x in k]
    if not all(closes) or not all(vols):
        return None
    trend = (closes[-1] / closes[-6] - 1) * 100  # 5 日涨跌
    vol_ratio = sum(vols[-5:]) / max(sum(vols[-10:-5]), 1)
    wins = closes[-20:]
    pos = (closes[-1] - min(wins)) / max(max(wins) - min(wins), 1e-9)
    return {"trend": trend, "vol_ratio": vol_ratio, "pos": pos * 100}


def build_market_state(broker=None) -> str:
    """【盘面状态（系统判定）】文本块：时段 + 大盘三态 + 动作基调 + 情绪提示。

    大盘判定：上证/沪深300 任一可用即可；5日涨跌 ±1.5% 为界分强/弱，
    量能比 >1.15 记放量，位置>60 记高位/ <30 记低位。无法取数时明确降级。
    """
    global _cache
    now = datetime.now(BJ)
    # 缓存 5 分钟（同一轮多 agent/多模式共用）
    if _cache["text"] and now.timestamp() - _cache["ts"] < TTL:
        return _cache["text"]
    session = "休市"
    hm = now.hour * 60 + now.minute
    if now.weekday() < 5:
        if 9 * 60 <= hm < 9 * 60 + 30:
            session = "集合竞价"
        elif 9 * 60 + 30 <= hm < 11 * 60 + 30:
            session = "盘中·上午"
        elif 11 * 60 + 30 <= hm < 13 * 60:
            session = "午间休市"
        elif 13 * 60 <= hm < 14 * 60 + 30:
            session = "盘中·下午"
        elif 14 * 60 + 30 <= hm < 14 * 60 + 57:
            session = "尾盘"
        elif 14 * 60 + 57 <= hm < 15 * 60:
            session = "尾盘竞价"
        elif hm < 9 * 60:
            session = "盘前"
        else:
            session = "盘后"
    stats = []
    if broker is not None:
        for code, name in (("000001.SH", "上证"), ("000300.SH", "沪深300")):
            s = _index_stats(broker, code)
            if s:
                stats.append((name, s))
    if stats:
        name, s = stats[0]
        if s["trend"] >= 1.5:
            regime = "大盘偏强"
        elif s["trend"] <= -1.5:
            regime = "大盘偏弱"
        else:
            regime = "大盘震荡"
        vol_tag = "放量" if s["vol_ratio"] >= 1.15 else ("缩量" if s["vol_ratio"] <= 0.85 else "平量")
        pos_tag = ("高位" if s["pos"] >= 60 else "低位" if s["pos"] <= 30 else "中位")
        extra = f"（指数校验：{name} 5日{s['trend']:+.1f}%、{vol_tag}、位置{pos_tag}）"
        if regime == "大盘偏强":
            tune = "进攻基调：主线龙头回踩低吸可给建仓/加仓；止损照执行但可用结构位"
        elif regime == "大盘偏弱":
            tune = "生存基调：减弱势仓留现金、禁止逆势买入、等企稳信号（缩量止跌/长下影）"
        else:
            tune = "防守反击基调：强势板块内轮动低吸、日内兑现、不做隔夜追涨"
    else:
        regime = "大盘状态未知（指数数据不可用）"
        tune = "按震荡处置：仓位中性、机会只做强势板块、控制隔夜"
        extra = ""
    text = (f"【盘面状态（系统判定）】时段={session}；{regime}{extra}；"
            f"动作基调={tune}。情绪提示：涨跌停与情绪极端点（一字/天地板）一律不参与，错过就错过。")
    with _lock:
        _cache = {"ts": now.timestamp(), "text": text}
    return text