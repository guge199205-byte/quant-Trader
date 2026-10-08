#!/usr/bin/env python3
"""分钟特征的**唯一口径**（2026-09-22）。

背景：`mom30m` / `vol5m` / `vol_ratio` 这三个同名同字段，此前有两个实现、两种口径：

  1. `live_l2_capture.fetch_minute_tick` ← TdxAiData 5 分钟K。
     AiData 通道级故障（.so 内部挂死）自 2026-09-02 起恒空：617 轮里 502 轮
     烧在超时上，产物里 `minute_feats` 出现 0 次。
  2. `live_hourly_analysis.self_minute_feats` ← `logs/min_snapshots/` 自采序列。
     它是活的回退（提示词路径在用），但有两处口径缺陷：
       a. `vol5m` 用**极差** `(max(chgs)-min(chgs))*100`，而 1 用的是**标准差**——
          同名字段其实不是同一个量，两个来源喂同一段提示词；
       b. `mom30m` 的锚点只有下界（`t0 - t >= 25min`）没有上界，而它读的是
          **最近两个日期文件** → 每个交易日开盘头半小时，锚点落在**昨天 15:59**，
          把隔夜跳空当成「30 分钟动量」报给模型。

本模块把 1 的公式抽出来做唯一实现，喂给它的数据换成 2 的 20 秒级自采快照
（`logs/min_snapshots/*.jsonl`，字段 ts/px/vol/b5）。做法是**先重采样成 5 分钟
bar，再原样跑公式** —— 口径与 AiData 一致，但数据源不依赖任何外部通道。

三条纪律：
  * 样本不足 / 间隔不合规 → 返回 `{}`（**显式缺失，绝不凑数**）；
  * 绝不把 10 分钟样本当 30 分钟动量（间隔闸在下）；
  * 跨日、跨午休的样本一律不参与（见 `_trailing_run`）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

BJ_TZ = timezone(timedelta(hours=8))   # 北京；不依赖 tzdata（alert 链路要保持轻依赖）
BAR_SEC = 300                 # 5 分钟 bar（与 AiData 的 5m 同口径）
MIN_BARS = 6                  # 少于此根数不算 —— 严禁把半窗当全窗
AIDATA_BARS = 12              # 定窗：AiData 原路径是 get_klines(interval="5m", count=12)
MAX_BAR_GAP_SEC = 450         # 相邻 bar 最大间隔（5min 的 1.5 倍，容采样抖动）
SESSION_AM = (9 * 60 + 30, 11 * 60 + 30)
SESSION_PM = (13 * 60, 15 * 60)
SNAPSHOT_STALE_SEC = 180      # 快照源「还算活着」的年龄上限（唯一出处）


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(raw: Any) -> Optional[datetime]:
    """ISO 串 / datetime → **带时区**的北京时间。

    必须归一：`fromisoformat` 对 `...Z` 给 aware、对裸 `2026-09-22T09:30:00` 给
    naive，两者相减直接抛 `TypeError: can't subtract offset-naive and
    offset-aware datetimes` —— 而本模块的输入来自多个写入点（快照文件、上游
    传入的行），裸串确实在流通。naive 按仓内约定视作北京时间（与 alert_checks._iso_bj 同）。
    """
    if isinstance(raw, datetime):
        d = raw
    else:
        try:
            d = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        return d.replace(tzinfo=BJ_TZ)
    return d.astimezone(BJ_TZ)


def in_session_minutes(m: int) -> bool:
    """自午夜起的分钟数是否落在连续竞价时段（时段口径的唯一定义）。"""
    return (SESSION_AM[0] <= m < SESSION_AM[1]) or (SESSION_PM[0] <= m < SESSION_PM[1])


def in_session(t: datetime) -> bool:
    """A股连续竞价时段（本模块只判时段，休市日历由数据本身体现：非交易日没有快照）。"""
    return in_session_minutes(t.hour * 60 + t.minute)


def session_start_minutes(m: int) -> int:
    """m 所处那一节的开始分钟数（上午 9:30 / 下午 13:00）。

    用来判断「本时段已经走了多久」——开盘头 30 分钟本就攒不够 6 根 bar，
    那种缺失是预期，不是降级。
    """
    return SESSION_PM[0] if m >= SESSION_PM[0] else SESSION_AM[0]


def _bar_key(t: datetime) -> int:
    """5 分钟对齐的 bar 起点（分钟数）。交易所的 5 分钟 bar 按 :00/:05/... 切。"""
    return t.hour * 60 + (t.minute // 5) * 5


def resample(rows: Iterable[dict]) -> List[dict]:
    """20 秒级快照 → 5 分钟 bar 序列（升序）。

    bar 取该时段内**最后一个有效样本**的价与累计量（= 收盘价/收盘累计量口径）。
    只保留单一交易日、且在连续竞价时段内的样本；跨日与午休的 bar 天然被分开，
    由 `_trailing_run` 的间隔闸剔除。
    """
    samples = []
    for r in rows:
        t = _parse_ts(r.get("ts"))
        px = _f(r.get("px"))
        if t is None or px <= 0 or not in_session(t):
            continue
        samples.append((t, px, _f(r.get("vol"))))
    if not samples:
        return []
    samples.sort(key=lambda s: s[0])
    day = samples[-1][0].date()          # 以**最新样本的日期**为界：昨日样本一律不参与
    bars: Dict[int, dict] = {}
    for t, px, vol in samples:
        if t.date() != day:
            continue
        k = _bar_key(t)
        b = bars.get(k)
        if b is None:
            bars[k] = {"start": k, "ts": t, "px": px, "cum_vol": vol}
        else:                             # 同 bar 内后来的样本覆盖（取最后一口）
            b["ts"], b["px"], b["cum_vol"] = t, px, vol
    return [bars[k] for k in sorted(bars)]


def _trailing_run(bars: Sequence[dict]) -> List[dict]:
    """从最新一根往回走，取**逐桶相邻**的连续段。

    两道闸，缺一不可：

    * **桶号**（`start`，分钟数）：相邻两根必须正好差 5 分钟。这是硬判据 ——
      「缺了一整根桶」只有它能看见。
    * **样本时刻差** ≤ `MAX_BAR_GAP_SEC`：挡隔夜（数百分钟）、午休（约 90 分钟），
      以及 bar 不带 `start` 时的退路。

    为什么光有后者不够：bar 的 ts 是**桶内最后一个样本**的时刻。若 09:35 整根桶
    没采到，前一根的 ts 落在 09:34:5x、后一根落在 09:40:0x，差 **310 秒** ≤ 450
    —— 只看时刻就会把缺口缝成「连续」。而窗口下游要算的 `vol5m`/`vol_ratio` 是
    **全窗**统计量，缝进一段 10 分钟的市场时间就变味了（12 根窗最多可跨 ~82 分钟），
    提示词却把它渲染成「5min波动」。
    """
    if not bars:
        return []
    run = [bars[-1]]
    for prev, cur in zip(reversed(bars[:-1]), reversed(bars)):
        a, b = prev.get("start"), cur.get("start")
        if isinstance(a, int) and isinstance(b, int):
            if b - a != BAR_SEC // 60:
                break
        gap = (cur["ts"] - prev["ts"]).total_seconds()
        if not (0 < gap <= MAX_BAR_GAP_SEC):
            break
        run.append(prev)
    run.reverse()
    return run


def compute(closes: Sequence[float], vols: Sequence[float]) -> Dict[str, Any]:
    """AiData 口径的分钟特征。**公式逐字取自 live_l2_capture.py:470-478**，一字未改。

    mom30m = round((closes[-1]/closes[-6]-1)*100, 3)      需 len>=6 and closes[-6]>0
    vol5m  = round(pstdev(closes 收益率)*100, 3)          **总体标准差**，非极差
    vol_ratio = round(vols[-1]/mean(vols), 2)             avg<=0 时该键不出现
    算不出的键**不出现**（不是填 None 占位）。

    口径边界（易被误读，显式写死）：**公式**与 AiData 一致，**窗口**由调用方决定 ——
    AiData 路径是 `count=12`，本模块的桥自算路径见 `AIDATA_BARS` 与 `from_bars`。
    公式本身对窗口长度不敏感（`closes[-6:]` 只看尾部 6 根），但 `vol5m`/`vol_ratio`
    是全窗统计量，窗口变了值就变了 —— 所以「公式一致」≠「数值一致」。
    """
    out: Dict[str, Any] = {}
    closes = list(closes)
    vols = list(vols)
    if len(closes) >= MIN_BARS and closes[-6] > 0:
        # 除数是 closes[-6]（`closes[0]` 只在 len==6 时才等于它），按真实除数设闸。
        out["mom30m"] = round((closes[-1] / closes[-6] - 1) * 100, 3)
        rets = [c2 / c1 - 1 for c1, c2 in zip(closes, closes[1:]) if c1 > 0]
        if rets:
            avg = sum(rets) / len(rets)
            out["vol5m"] = round(
                (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 3)
    # 量序列天然比价序列短一根（首根 bar 的量不可知，见 _per_bar_volumes），
    # 所以门槛跟**bar 数**走而不是跟量序列自己的长度走 —— 旧写法 `len(vols) >= MIN_BARS`
    # 实际要求 7 根 bar，于是 6 根时 vol_ratio 静默消失，而同一份产物里
    # mom30m/vol5m 与「基于 6 根5min」都还在。
    if len(closes) >= MIN_BARS and vols:
        avg = sum(vols) / len(vols)
        if avg > 0:
            out["vol_ratio"] = round(vols[-1] / avg, 2)
    return out


def _per_bar_volumes(run: Sequence[dict]) -> Optional[List[float]]:
    """累计量差分 → 每根 bar 的量，长度为 `len(run) - 1`（对应 run[1:]）。

    第一根 bar 的量不可知（缺它之前的那次累计量），所以量序列天然比价序列短一根；
    `compute` 的两个参数就是这么对齐的。当前未收口的 bar 也在里面（= 它到目前为
    止的量），与 AiData「closes[-1] 是进行中的那根 bar」同语义。

    差分 ≤0 = 数据异常（跨日重置/抓取错位）→ None。**不把异常差分当 0**：
    那会算出一个看起来很合理的假量比。
    """
    vols: List[float] = []
    for prev, cur in zip(run, run[1:]):
        d = cur["cum_vol"] - prev["cum_vol"]
        if d <= 0:
            return None
        vols.append(d)
    return vols or None


def from_bars(bars: Sequence[dict]) -> Dict[str, Any]:
    """已有 5 分钟 bar 序列 → 分钟特征。

    **定窗**：`run[-AIDATA_BARS:]`（= AiData 原路径的 `count=12`）。此前是把
    合规连续段**整段**喂进 compute，而 `_trailing_run` 只在间隔超限时停 —— 交易日
    里那等于「自开盘以来」。于是同名字段的含义随当日已过去的时长漂移（实测同一段
    尾部 6 根 bar：18 根窗 vol5m=2.641、6 根窗 vol5m=4.782，差 1.8 倍），而提示词
    把它渲染成「5min波动」。

    有 bar 但连续段不足 `MIN_BARS` → 返回显式降级标记（**不是 `{}`**）：`{}` 与
    「拿到 6 根但间隔全是 10 分钟」在下游长得一模一样，而后者是数据缺陷、
    前者只是还没开盘。
    """
    if not bars:
        return {}
    run = _trailing_run(bars)
    if len(run) < MIN_BARS:
        return {"ok": False, "reason": "insufficient_history",
                "have": len(run), "need": MIN_BARS}
    window = run[-AIDATA_BARS:]
    vols = _per_bar_volumes(window)       # 短一根；由 compute 侧的闸对齐
    out = compute([b["px"] for b in window], vols or [])
    if not out:
        return {}
    out["ok"] = True
    out["src"] = "self"
    out["bars"] = len(window)
    # 窗口**最后一个样本的时刻**。有了它，产物自己就能回答「这是此刻的吗」——
    # 在此之前下游只能去扫快照目录问「有人还在写吗」，而那是**全局**问题，
    # 答不了「这一只票还在写吗」（见 fresh_or_stale）。
    ts = window[-1].get("ts")
    if ts is not None:
        out["last_ts"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return out


def fresh_or_stale(mf: dict, now: datetime) -> dict:
    """`ok` 结果若样本已经过旧（盘中）→ 换成降级标记；否则原样返回。

    **颗粒度是单票**。全局探针 `latest_sample_ts` 只能证明「有人在采」，证明不了
    「这一只票在采」：2026-09-22 实测 `logs/min_snapshots/2026-09-22.jsonl` 里
    `688591.SH` 最后一条 13:56:42，而全文件最后一条 14:59:42（来自**别的票**）→
    全局探针全程 20 秒新鲜，那只票单停了 63 分钟无人知。

    两条边界：
      * 盘后/盘前**不判龄** —— 那时最近的一段 bar 本来就该是「上一场」，拿年龄
        去卡它等于每个收盘后都报降级（同轮级戳的 idle）。
      * 盘中说不出样本时刻（缺 last_ts / 不可解析）→ 判降级：证明不了是此刻的。
    """
    if not mf or mf.get("ok") is False:
        return mf
    if not in_session(now):
        return mf
    ts = _parse_ts(mf.get("last_ts"))
    if ts is None:
        return {"ok": False, "reason": "stale_source", "age_sec": None}
    age = (now - ts).total_seconds()
    if age < 0 or age > SNAPSHOT_STALE_SEC:      # 负龄 = 样本晚于本机（同 L2 戳）
        return {"ok": False, "reason": "stale_source", "age_sec": int(age)}
    return mf


def from_rows(rows: Iterable[dict]) -> Dict[str, Any]:
    """`logs/min_snapshots/*.jsonl` 的行 → 分钟特征。

    除 5 分钟 bar 口径的三项外，另出两项只有原始样本才有的字段（旧回退在提供、
    提示词在渲染，不能静默丢）：
      * `mom5m` —— 优先用桥的 `b5`(Before5MinNow，五分钟前价，单轮内即可得、
        不受漏轮影响)，缺失时回退上一根 bar 的收盘；
      * `inout` —— 内外盘失衡（最新样本的 inside/outside）。
    """
    rows = list(rows)
    bars = resample(rows)
    out = from_bars(bars)
    last = _last_sample(rows)
    if last is None:
        return {}
    # mom5m 优先用桥的 b5（五分钟前价，单轮内即可得）；缺 b5 才回退上一根 bar，
    # 且回退必须走同一道间隔闸 —— 否则 10 分钟缺口会被当成「5 分钟动量」。
    b5 = _f(last.get("b5"))
    if b5 <= 0:
        run = _trailing_run(bars)
        b5 = run[-2]["px"] if len(run) >= 2 else 0.0
    if b5 > 0:
        out["mom5m"] = round((last["px"] / b5 - 1) * 100, 3)
    ins, outs = _f(last.get("inside")), _f(last.get("outside"))
    if ins + outs > 0:
        out["inout"] = round((ins - outs) / (ins + outs), 3)
    return out


def _last_sample(rows: Iterable[dict]) -> Optional[dict]:
    """最新一条「有效且在连续竞价时段内」的样本（跨日/盘后样本不参与）。"""
    best = None
    for r in rows:
        t = _parse_ts(r.get("ts"))
        px = _f(r.get("px"))
        if t is None or px <= 0 or not in_session(t):
            continue
        if best is None or t > best[0]:
            best = (t, r)
    if best is None:
        return None
    r = dict(best[1])
    r["px"] = _f(r.get("px"))
    return r


def latest_sample_ts(snap_dir, last_n: int = 2) -> Optional[datetime]:
    """最近 n 个日期文件里最新一条**有效**样本的时刻；无 → None。

    这是「快照采集器还活着吗」的唯一判据。产物的键不存在有两种截然不同的成因
    （今天刚开盘没攒够样本 / 采集器死了），只有它能分开——2026-09-02 起三周的
    AiData 静默之所以没人发现，就是缺了这根探针。
    """
    import json

    best: Optional[datetime] = None
    try:
        files = sorted(snap_dir.glob("*.jsonl"))[-last_n:]
    except (OSError, ValueError):  # read_text 的坏字节抛 UnicodeDecodeError（ValueError）
        return None
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, ValueError):  # read_text 的坏字节抛 UnicodeDecodeError（ValueError）
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or _f(r.get("px")) <= 0:
                continue
            t = _parse_ts(r.get("ts"))
            if t is not None and (best is None or t > best):
                best = t
    return best


def load_code_rows(code: str, snap_dir, last_n: int = 2) -> List[dict]:
    """读该票最近 n 个日期文件的快照行（跨日筛选交给 resample 的日期界）。"""
    import json

    rows: List[dict] = []
    try:
        files = sorted(snap_dir.glob("*.jsonl"))[-last_n:]
    except (OSError, ValueError):  # read_text 的坏字节抛 UnicodeDecodeError（ValueError）
        return []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, ValueError):  # read_text 的坏字节抛 UnicodeDecodeError（ValueError）
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("code") == code and _f(r.get("px")) > 0:
                rows.append(r)
    return rows


def self_check() -> None:
    """自检：打印各票当前口径下的分钟特征（只读）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent
    snap_dir = root.parent / "logs" / "min_snapshots"
    codes = set()
    try:
        for f in sorted(snap_dir.glob("*.jsonl"))[-1:]:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    codes.add(__import__("json").loads(line).get("code"))
                except ValueError:
                    continue
    except (OSError, ValueError):  # read_text 的坏字节抛 UnicodeDecodeError（ValueError）
        print("无快照目录")
        return
    for code in sorted(c for c in codes if c):
        print(f"{code}: {from_rows(load_code_rows(code, snap_dir))}")


if __name__ == "__main__":
    self_check()
