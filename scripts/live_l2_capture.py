#!/usr/bin/env python3
"""BayMax 自有 L2 逐笔因子采集（复刻 quantmind tdx_l2_capture_task，去掉引擎依赖）。

背景：quantmind 的 L2 采集每轮循环先调引擎 load_latest_scores 拿候选池——
该调用常抛 'NoneType' .get / division by zero（09-01 全天 239 次），整轮作废、
因子表冻结；且按铁律不动 quantmind 代码。桥的 L2 数据本身正常（实测
get_exday_data 返回完整 L2 扩展日线，600183 09:34 曾有真实 VPIN）。

本脚本：桥 get_exday_data（L2 扩展日线：4×4 分档量额/净挂撤单/委买卖均价/
成交单数）+ get_market_snapshot（价/内外盘/五档）→ 13 个回测因子（与 quantmind
同公式，所有除法带保护）→ data/l2_factors_live.json（盘中分析提示词消费）。
轮询集合 = 桥实际持仓 + BayMax 候选池 Top20（自有 load_pool，不依赖引擎）。
桥读限流与行情推送共享 → 恒定 ≤1.5s/次节奏，全量 ~2min。

用法:
  python scripts/live_l2_capture.py            # 交易时段单轮（cron */5）
  python scripts/live_l2_capture.py --force    # 忽略时段（测试/补跑）
  python scripts/live_l2_capture.py --status   # 查看最近因子快照
"""
import argparse
import json
import math
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import minute_feats  # noqa: E402  分钟特征的唯一口径（见该模块 docstring）
from trading_cal import is_trading_day, why_not  # noqa: E402

FACTORS_FILE = ROOT / "data" / "l2_factors_live.json"
STATE_FILE = ROOT / "data" / "l2_state.json"
STATUS_FILE = ROOT / "data" / "l2_status.json"
MARKET_FILE = ROOT / "data" / "market_snapshot.json"
SAMPLE_WINDOW = 40           # 滚动窗口样本数（每 pass 每只 1 样本）
CALL_INTERVAL_SEC = 1.5      # 桥调用节奏（≤40/min，限流 60/min 与行情推送共享）
MAX_WATCHLIST = 30           # 轮询上限（持仓 + 候选池）
MAX_CALLS_PER_PASS = 80      # 单轮桥调用预算（安全阀）

# 大盘指数（桥实时快照）+ 持仓板块（get_relation → 板块指数快照）
INDEX_CODES = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("000300.SH", "沪深300")]
MAX_SECTOR_CALLS = 5         # 板块指数快照预算（每轮）

ZONE_BOUNDARIES = [("T3", (600, 630)), ("T4", (630, 660)), ("T5", (660, 690)), ("T6", (780, 810))]
# 时段（分钟数, 开盘后偏移）: T3=10:00-10:30, T4=10:30-11:00, T5=11:00-11:30, T6=13:00-13:30

# 13 个回测推荐因子的 ICIR 权重（quantmind l2_recommended_factors.csv，去 micro_pin）
FACTOR_ICIR = {
    "micro_vpin_vol_ratio": 0.562,
    "micro_vpin_amount_ratio": 0.483,
    "micro_zone_distribution": 0.417,
    "micro_zone_vol_ratio_T4": 0.345,
    "micro_zone_vol_ratio_T6": 0.338,
    "vol_price_divergence": 0.332,
    "micro_zone_vol_ratio_T5": 0.316,
    "micro_open_gap": 0.273,
    "micro_impact_decay_half_life": 0.271,
    "micro_liquidity_daily_pattern": 0.237,
    "micro_zone_vol_ratio_T3": 0.198,
}


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


def in_window(now: datetime) -> bool:
    """A股交易时段（北京）：9:30-11:30 / 13:00-15:00 交易日（日历感知，节假日休市不采集）。"""
    if not is_trading_day(now.date()):
        return False
    hm = now.hour * 100 + now.minute
    return 930 <= hm <= 1130 or 1300 <= hm <= 1500


# ---------- 桥取数（BayMax 自有 TdxBridgeBroker.tdx_call 透传） ----------

def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clip(v: float, lo: float = -math.inf, hi: float = math.inf) -> float:
    return max(lo, min(hi, v))


def _load_broker():
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    return TdxBridgeBroker()


def fetch_l2(broker, suffix: str) -> tuple[dict | None, dict]:
    """拉取单只 L2 扩展日线 + 快照。返回 (exday_data, snap)；失败 None 不阻塞。"""
    exday = None
    try:
        exday = broker.tdx_call("get_exday_data", {"stock_code": suffix, "count": 1})
    except Exception:  # noqa: BLE001
        pass
    row = None
    if isinstance(exday, list) and exday:
        row = exday[0]
    elif isinstance(exday, dict):
        rows = exday.get("Value")
        if isinstance(rows, list) and rows:
            row = rows[0]
    snap = {}
    try:
        snap = broker.tdx_call("get_market_snapshot", {"stock_code": suffix})
    except Exception:  # noqa: BLE001
        pass
    return row, snap


def parse_exday_row(row) -> dict | None:
    if not isinstance(row, dict):
        return None
    try:
        return {
            "trade_date": str(row.get("Date") or "").replace("-", "")[:8],
            "cjbs": int(_f(row.get("CJBS"))),
            "b_order": _f(row.get("BOrder")),
            "b_cancel": _f(row.get("BCancel")),
            "s_order": _f(row.get("SOrder")),
            "s_cancel": _f(row.get("SCancel")),
            "buy_avp": _f(row.get("BuyAvp")),
            "sell_avp": _f(row.get("SellAvp")),
            "total_b_order": _f(row.get("TotalBOrder")),
            "total_s_order": _f(row.get("TotalSOrder")),
            "vol_4x4": row.get("Vol") or [],
            "amo_4x4": row.get("Amo") or [],
            "vol_num": row.get("VolNum") or [],
        }
    except (TypeError, ValueError):
        return None


def parse_snapshot(snap_result) -> dict:
    r = snap_result.get("Value") if isinstance(snap_result, dict) else None
    if isinstance(r, list) and r:
        r = r[0] if isinstance(r[0], dict) else r
    if not isinstance(r, dict):
        # 桥对 get_market_snapshot 返回平铺（Buyp/Buyv 在顶层，无 Value 包裹）
        r = snap_result
    if not isinstance(r, dict):
        return {}
    return {
        "now": _f(r.get("Now")),
        "open": _f(r.get("Open")),
        "pre_close": _f(r.get("LastClose")),
        "volume": _f(r.get("Volume")),
        "amount": _f(r.get("Amount")),
        "bid5": [_f(v) for v in (r.get("Buyv") or [])],
        "ask5": [_f(v) for v in (r.get("Sellv") or [])],
        "inside": _f(r.get("Inside")),
        "outside": _f(r.get("Outside")),
    }


def _vol_matrix(data: dict, col: int, key: str = "vol_4x4") -> float:
    """Vol/Amo 4×4 矩阵第 col 列之和（列: 0=买 1=卖 2=主买 3=主卖）。"""
    mat = data.get(key) or []
    return sum(_f(r_[col]) for r_ in mat if isinstance(r_, (list, tuple)) and len(r_) > col)


# ---------- 13 因子计算（quantmind 同公式，除法全带保护） ----------

def _minute_of_day() -> int:
    now = datetime.now()
    return now.hour * 60 + now.minute


def _corr(a: list[float], b: list[float]) -> float:
    """两序列皮尔逊相关（长度不足/方差为零返回 0）。"""
    n = len(a)
    if n < 6:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    den = math.sqrt(va * vb)
    return cov / den if den > 1e-12 else 0.0


def _autocorr(series: list[float], lag: int = 1) -> float:
    if len(series) < 8:
        return 0.0
    return _corr(series[:-lag], series[lag:])


def compute_l2_factors(data: dict, snap: dict, state: dict) -> dict:
    """13 个回测推荐因子的实时近似（全部基于累积字段，非交易时间也可算静态日值）。

    state: {samples: [[ts, v_buy, v_sell, a_buy, a_sell, price]...],
            zone_baselines: {...}, prev_price}——跨 pass 持久化（data/l2_state.json）。
    """
    v_buy, v_sell = _vol_matrix(data, 2), _vol_matrix(data, 3)                       # 主买/主卖
    a_buy, a_sell = _vol_matrix(data, 2, "amo_4x4"), _vol_matrix(data, 3, "amo_4x4")
    v_total = v_buy + v_sell
    if v_total <= 0:
        v_buy, v_sell = snap.get("outside", 0), snap.get("inside", 0)                # 快照内外盘兜底
        v_total = v_buy + v_sell
        a_buy, a_sell = v_buy, v_sell

    price = snap.get("now") or state.get("prev_price") or 0
    vol_day = snap.get("volume") or v_total

    # 时段基准（zone_vol_ratio_T* 需要开盘以来各时段累积量）
    minute = _minute_of_day()
    baselines = state.setdefault("zone_baselines", {})
    for zone, (start, end) in ZONE_BOUNDARIES:
        if start <= minute < end and zone not in baselines:
            baselines[zone] = v_total

    factors: dict = {}
    # 1/2. VPIN（滚动窗口 Σ|Δ买−Δ卖|/ΣΔ）— vol / amount
    samples = state.setdefault("samples", [])
    samples.append([time.monotonic(), v_buy, v_sell, a_buy, a_sell, price])
    del samples[:-SAMPLE_WINDOW]  # 截断窗口（list 持久化用，不用 deque）
    if len(samples) >= 3:
        d_v, d_buy, d_sell = [], [], []
        d_amt, d_abuy, d_asell = [], [], []
        for (t0, b0, s0, ab0, as0, _), (t1, b1, s1, ab1, as1, _) in zip(samples, samples[1:]):
            d_v.append(max(b1 + s1 - b0 - s0, 0))
            d_amt.append(max(ab1 + as1 - ab0 - as0, 0))
            d_buy.append(max(b1 - b0, 0))
            d_sell.append(max(s1 - s0, 0))
            d_abuy.append(max(ab1 - ab0, 0))
            d_asell.append(max(as1 - as0, 0))
        sv = sum(d_v) + 1e-9
        sa = sum(d_amt) + 1e-9
        factors["micro_vpin_vol_ratio"] = round(sum(abs(b - s) for b, s in zip(d_buy, d_sell)) / sv, 6)
        factors["micro_vpin_amount_ratio"] = round(sum(abs(b - s) for b, s in zip(d_abuy, d_asell)) / sa, 6)
    else:
        factors["micro_vpin_vol_ratio"] = None
        factors["micro_vpin_amount_ratio"] = None

    # 3. zone_distribution: 5 档深度按档位衰减加权的不平衡（买压正）
    bid5, ask5 = snap.get("bid5") or [], snap.get("ask5") or []
    depth_bal = 0.0
    depth_tot = 0.0
    for k, (b, a) in enumerate(zip(bid5, ask5)):
        w = 1.0 / (k + 1)
        depth_bal += w * (b - a)
        depth_tot += w * (b + a)
    factors["micro_zone_distribution"] = round(depth_bal / (depth_tot + 1e-9), 6)

    # 4-7. zone_vol_ratio_T3/T4/T5/T6: 时段成交量 / 当前总成交
    # （baseline 未建立=还没进该时段或盘后 → None，不占位 1.0 误导模型）
    for zone, _ in ZONE_BOUNDARIES:
        base = baselines.get(zone, 0)
        if not base:
            factors[f"micro_zone_vol_ratio_{zone}"] = None
        else:
            factors[f"micro_zone_vol_ratio_{zone}"] = round((v_total - base) / (v_total + 1e-9), 6)

    # 8. vol_price_divergence: 价格变动与量变动的负相关（背离为正）
    if len(samples) >= 10:
        prices = [s[5] for s in samples]
        vols = [s[1] + s[2] for s in samples]
        dp = [b - a for a, b in zip(prices, prices[1:])]
        dv = [b - a for a, b in zip(vols, vols[1:])]
        factors["vol_price_divergence"] = round(-_corr(dp, dv), 6)
    else:
        factors["vol_price_divergence"] = None

    # 9. open_gap: 开盘缺口（快照 Open/LastClose）
    open_p, pre_c = snap.get("open", 0), snap.get("pre_close", 0)
    factors["micro_open_gap"] = round((open_p - pre_c) / (pre_c + 1e-9), 6) if pre_c else None

    # 10/12. impact_decay / flow_revert_speed: 不平衡序列的自相关（1−ρ1, 快回复=高）
    imbalances = [_clip(s[1] - s[2], -1e12, 1e12) for s in samples]
    rho = _autocorr(imbalances, 1) if len(samples) >= 8 else None
    factors["micro_impact_decay_half_life"] = round(_clip(1 - rho, -1, 1), 6) if rho is not None else None
    factors["flow_imbalance_revert_speed"] = round(_clip(1 - rho, 0, 2), 6) if rho is not None else None

    # 11. liquidity_daily_pattern: 近 30min Amihud / 全天 Amihud
    if len(samples) >= 3 and price > 0:
        rets = []
        for (t0, *_a, p0), (t1, *_b, p1) in zip(samples, samples[1:]):
            if p0 > 0 and p1 > 0:
                rets.append(abs(p1 - p0) / p0)
        amt_day = snap.get("amount") or (a_buy + a_sell)
        cur_ret = sum(rets[-15:]) / max(len(rets[-15:]), 1)
        day_ret = sum(rets) / max(len(rets), 1)
        cur_amihud = cur_ret / max(amt_day * 0.25, 1e-9)   # 近 15 分钟 ≈ 全天 25%
        day_amihud = day_ret / max(amt_day, 1e-9)
        factors["micro_liquidity_daily_pattern"] = round(
            _clip(cur_amihud / (day_amihud + 1e-9), 0, 10), 6)
    else:
        factors["micro_liquidity_daily_pattern"] = None

    # 13. zone_rv_ratio_close: 近 30min 波动 / 全天波动
    if len(samples) >= 6:
        prices = [s[5] for s in samples]
        rets = [abs((b - a) / a) for a, b in zip(prices, prices[1:]) if a > 0]
        if rets:
            half = max(len(rets) // 2, 1)
            cur_rv = sum(rets[-half:]) / half
            day_rv = sum(rets) / len(rets)
            factors["micro_zone_rv_ratio_close"] = round(_clip(cur_rv / (day_rv + 1e-9), 0, 10), 6)
        else:
            # 全程无有效价（停牌/无行情，now 缺省记 0）→ rets 空，按样本不足处理。
            # half 有 max(...,1) 兜底但 day_rv 的除法没有——曾抛 ZeroDivisionError
            # （quantmind 线上实录同款：整轮采集中止；本仓单只级 try 兜住但该票整分钟被跳过）
            factors["micro_zone_rv_ratio_close"] = None
    else:
        factors["micro_zone_rv_ratio_close"] = None

    state["prev_price"] = price or state.get("prev_price") or 0
    return {k: (round(_clip(v, -1e6, 1e6), 6) if v is not None else None)
            for k, v in factors.items()}


def build_signal_score(factors: dict) -> float:
    """ICIR 加权原始分（None 因子跳过）。"""
    w_sum = sum(FACTOR_ICIR.values())
    acc = sum(v * w for k, w in FACTOR_ICIR.items()
              if isinstance((v := factors.get(k)), (int, float)))
    return round(acc / w_sum * 100, 2)


# ---------- 状态/产物持久化 ----------

def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    """容错读 JSON 对象：缺失/半截/写坏（盘满曾把状态文件截成 0 字节）一律 {}。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _as_int(v, default: int = 0) -> int:
    """落盘 JSON 里的计数（`_read_json` 的同伴）。

    裸 `int()` 在坏类型上抛出去不是「这一轮失败」，而是**永久停摆**：
    `minute_capability` 抛 → run_pass 死在写盘前 → 产物永远留着那个坏值 →
    下一轮读到同一份坏值再抛（自我维持，无人自愈）。JSON 的 `Infinity`/`NaN`
    是合法 token（`int(inf)` 抛的是 OverflowError），`True` 是 int 子类但不是计数。
    """
    if isinstance(v, bool) or v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def load_state() -> dict:
    return _read_json(STATE_FILE)


def load_factors() -> dict:
    return _read_json(FACTORS_FILE)


# ---------- 大盘/板块上下文（桥快照，不占因子节奏） ----------

def fetch_market_context(broker, holdings_codes: list) -> dict:
    """大盘指数 + 持仓股所属板块（行业优先）+ 板块指数当日涨跌。
    全部经桥透传；单只失败跳过。→ data/market_snapshot.json（盘中分析提示词消费）。"""
    ctx: dict = {"ts": now_cn().isoformat(timespec="seconds"), "indices": {}, "sectors": {},
                 "position_sectors": {}}  # 持仓→板块映射（波动触发器板块联动检测用）
    for code, name in INDEX_CODES:
        try:
            s = parse_snapshot(broker.tdx_call("get_market_snapshot", {"stock_code": code}))
            if s.get("now") and s.get("pre_close"):
                ctx["indices"][code] = {
                    "name": name, "now": s["now"],
                    "chg_pct": round((s["now"] / s["pre_close"] - 1) * 100, 2),
                }
        except Exception:  # noqa: BLE001
            pass
    # 个股所属板块：行业板块优先（get_relation 返回 行业/地区/概念 混合，取前两类）
    sector_by_code: dict = {}
    for code in holdings_codes[:MAX_WATCHLIST]:
        try:
            rel = broker.tdx_call("get_relation", {"stock_code": code})
            rows = rel.get("Value") or (rel if isinstance(rel, list) else [])
            for r in rows:
                btype = str(r.get("BlockType") or "")
                if btype in ("行业", "概念"):
                    sector_by_code.setdefault(code, []).append(
                        {"code": r.get("BlockCode"), "name": r.get("BlockName"), "type": btype})
                    break  # 每只取第一个行业/概念板块
        except Exception:  # noqa: BLE001
            continue
    # 板块指数当日涨跌（预算内）
    seen: set = set()
    for code, rels in sector_by_code.items():
        for rel in rels:
            bcode = rel.get("code")
            if bcode:
                ctx["position_sectors"][code] = bcode  # 持仓→板块（供触发器）
            if not bcode or bcode in seen or len(seen) >= MAX_SECTOR_CALLS:
                continue
            seen.add(bcode)
            try:
                s = parse_snapshot(broker.tdx_call("get_market_snapshot", {"stock_code": bcode}))
                if s.get("now") and s.get("pre_close"):
                    ctx["sectors"][bcode] = {
                        "name": rel.get("name"), "type": rel.get("type"),
                        "now": s["now"],
                        "chg_pct": round((s["now"] / s["pre_close"] - 1) * 100, 2),
                    }
            except Exception:  # noqa: BLE001
                continue
    _atomic_write(MARKET_FILE, ctx)
    return ctx


SNAPSHOT_DIR = ROOT / "logs" / "min_snapshots"
# 快照 3 分钟没更新 = 采集器死了（采样节奏是每分钟 3 轮）。**唯一出处**在
# minute_feats（提示词路径也要用同一个阈值判定，两处各写一个数迟早漂移）。
SNAPSHOT_STALE_SEC = minute_feats.SNAPSHOT_STALE_SEC
MINUTE_WARMUP_MIN = 30       # 本节时段头 30 分钟攒不满 6 根 5 分钟 bar，属预期


def self_minute_feats(code: str) -> dict:
    """持仓股的分钟特征 —— 自采快照（logs/min_snapshots/）自算，**不碰任何网络**。

    2026-09-22 起分钟因子的唯一来源：20 秒快照 → 5 分钟 bar → AiData 公式逐字重演
    （公式与口径见 scripts/minute_feats.py，那里也是唯一实现）。

    为什么不再用 AiData 分钟K：那条链自 2026-09-02 起通道级挂死（617 轮里 502 轮
    烧在超时上，产物 `minute_feats` 出现 0 次），而这里的自采集每轮本来就有快照，
    自算是零额外成本且不受外部通道影响。

    新鲜度按**本票**判（`minute_feats.fresh_or_stale`），不是按「快照目录里有没有
    人在写」：后者是全局问题，别的票还在采就会把单票停采整个盖住。
    """
    try:
        rows = minute_feats.load_code_rows(code, SNAPSHOT_DIR)
    except OSError:
        return {}
    return minute_feats.fresh_or_stale(minute_feats.from_rows(rows), now_cn())


def minute_capability(now: datetime, produced: int, watch: int,
                      last_sample_ts: Optional[datetime],
                      prev: Optional[dict] = None,
                      bars_min: Optional[int] = None,
                      degraded: Optional[dict] = None) -> dict:
    """分钟因子这条链的健康戳（落 `data/l2_status.json` 的 capabilities.minute_k）。

    产物里「键不存在」有两种成因：今天刚开盘还没攒够样本，或采集源死了。三周没人
    发现 AiData 静默，就是因为两者在产物里长得一样 —— 这个戳把它们分开。

    ok=True 覆盖三态：`ok`（在产出）、`warmup`（本节头 30 分钟，本就不够）、
    `idle`（非交易时段/持仓为空）。**warmup 与 idle 必须 ok=True**，否则每天开盘
    和收盘各刷一轮假告警，运维学会忽略这块板 —— 那正是事故的成因。

    `fail_streak` / `first_fail_ts` 语义对齐 scripts/rt_probe.py 的 apply_streaks：
    首败时刻不被后续失败覆盖（= 「自 Y 时刻起持续降级」）。

    ⚠️ `watch <= 0` 把两件事并成 idle（2026-09-22 审查 MEDIUM-4）：**真的没有持仓**
    （平仓/空仓，idle 正确）与**账户通道掉线**（桥假活：asset=0、positions=[]，
    于是 run_pass 算出的持仓集是空的）。后者由 `probe_account` + `alert_checks.
    account_down` 专门负责（2026-09-10 实录：行情全通、账户整日 asset=0、零告警）——
    这里**刻意不重复告警**，同一个根因两条告警只会互相稀释。要在这里分开就得把
    「持仓是已知的」当参数传进来（改签名 + 全套用例），当前不做；排查分钟因子缺席时
    先看 `logs/rt_status.json` 的 account 段。
    """
    prev = prev or {}
    m = now.hour * 60 + now.minute
    age: Optional[float] = None
    if last_sample_ts is not None:
        delta = (now - last_sample_ts).total_seconds()
        age = delta if delta >= 0 else None   # 未来样本（时钟回拨/时区错）不算新鲜
    error = ""
    if not minute_feats.in_session_minutes(m) or watch <= 0:
        state = "idle"
    elif age is None or age > SNAPSHOT_STALE_SEC:
        state = "stale_source"
        if age is not None:
            error = f"快照源停更 {age:.0f} 秒（阈值 {SNAPSHOT_STALE_SEC}s）"
        elif last_sample_ts is not None:
            error = "快照样本时刻晚于本机（时钟/时区异常）"
        else:
            error = f"无快照样本（{SNAPSHOT_DIR} 为空或缺失）"
    elif produced >= watch:
        state = "ok"
    elif produced > 0:
        # **部分产出不算 ok**（旧写法 `produced > 0` = 拿 any 当 all）：少的那几只
        # 票的分钟特征会从产物与提示词里整块消失，而戳是绿的、告警不响。
        state = "partial"
        names = sorted(degraded or {})
        shown = "、".join(names[:3]) + (f" 等 {len(names)} 只" if len(names) > 3 else "")
        error = f"{watch - produced}/{watch} 只持仓没产出行情因子"
        if shown:
            error += f"（{shown}）"
    elif m - minute_feats.session_start_minutes(m) < MINUTE_WARMUP_MIN:
        state = "warmup"
    else:
        state = "no_factors"
        error = "快照新鲜但算不出因子（连续 5 分钟 bar 不足 6 根）"
    ok = state in ("ok", "warmup", "idle")
    # 清零的唯一条件是 `state == "ok"`，**不是 `produced > 0`**：快照源断线后，
    # 断线前攒下的 bar 仍躺在文件里，`from_rows` 照样算得出 mom30m（同日、同
    # 时段、间隔合规）—— 于是 produced>0 与 stale_source 同时成立。按 produced
    # 清零，等于每轮都把计数抹掉，那条按 fail_streak 防抖的告警**永远等不到第 N 轮**。
    if state == "ok":                      # 真产出（快照新鲜且算得出）→ 恢复即清零
        streak, first = 0, None
    elif ok:                               # warmup/idle：不推高也不清零
        streak, first = _as_int(prev.get("fail_streak")), prev.get("first_fail_ts")
    else:
        streak = _as_int(prev.get("fail_streak")) + 1
        first = prev.get("first_fail_ts") or now.isoformat(timespec="seconds")
    return {
        "ok": ok,
        "state": state,
        "source": "self",
        "error": error or None,
        "produced": produced,
        "watch": watch,
        # 本轮产出里**最短**的窗口根数（是下限不是均值）：6 根是噪声地板，
        # 光看 produced/watch 看不出「10 只里 1 只只剩 6 根」。
        "bars_min": bars_min,
        "snapshot_age_sec": None if age is None else int(age),
        "fail_streak": streak,
        "first_fail_ts": first,
        "ts": now.timestamp(),
        "ts_cn": now.astimezone(CN_TZ).isoformat(timespec="seconds"),
    }


# ---------- 单轮采集 ----------

def run_pass(broker, dry_debug: bool = False) -> int:
    """单轮采集：持仓 + 候选池 → 13 因子 → 落盘。返回更新只数。"""
    acct = broker._account_query()
    positions = acct.get("positions") or []
    pos_codes = []
    for p in positions:
        code = str(p.get("stock_code") or "").strip()
        if code and code not in pos_codes:
            pos_codes.append(code)
    codes = list(pos_codes)
    try:
        from live_llm_trade import load_pool

        pool, _ = load_pool(20)
        for p in pool:
            code = str(p.get("code") or "").strip()
            if code and code not in codes:
                codes.append(code)
    except Exception:  # noqa: BLE001
        pass
    # 盘中异动关注（新闻管线产物：micro 事件/主编 watch 的代码）→ 临时入 L2 轮询
    try:
        from news_brief import load_intraday_watch

        iw_codes = load_intraday_watch()
        for c in iw_codes:
            if c not in codes:
                codes.append(c)
    except Exception:  # noqa: BLE001
        pass
    codes = codes[:MAX_WATCHLIST]
    if not codes:
        return 0

    state = load_state()
    factors = load_factors()
    now = now_cn()
    updated = 0
    calls = 0
    mf_produced = 0          # 本轮机产出分钟因子的持仓数（**不含**降级标记）
    mf_watch = 0             # 本轮机应当产出分钟因子的持仓数（分母）
    mf_bars_min = None       # 本轮产出里最短的窗口根数（质量下限）
    mf_degraded = {}         # {仓位代码: 降级原因} —— 「哪几只没数」要说得出来
    pos_set = set(pos_codes)
    for code in codes:
        if calls >= MAX_CALLS_PER_PASS:
            break
        try:
            row, snap_raw = fetch_l2(broker, code)
        except Exception:  # noqa: BLE001
            continue
        calls += 2
        data = parse_exday_row(row)
        snap = parse_snapshot(snap_raw)
        if not data or not snap:
            time.sleep(CALL_INTERVAL_SEC)
            continue
        st = state.setdefault(code, {"samples": [], "zone_baselines": {}, "prev_price": 0})
        try:
            fac = compute_l2_factors(data, snap, st)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ {code} 因子计算失败: {exc}")
            time.sleep(CALL_INTERVAL_SEC)
            continue
        factors[code] = {
            "ts": now.isoformat(timespec="seconds"),
            "name": "",
            "now_price": snap.get("now") or 0,
            # 昨收/开盘：提示词据此算当日涨跌%（候选池此前只给评分不给价，
            # 模型无法定价 → 只能空转或挂 watch，见 2026-09-08 决策记分卡上线复盘）
            "pre_close": snap.get("pre_close") or 0,
            "open": snap.get("open") or 0,
            "volume": snap.get("volume") or 0,
            "factors": fac,
            "signal_score": build_signal_score(fac),
        }
        # 持仓股附加：分钟特征（自采快照自算，零额外桥调用、零外部通道依赖）
        if code in pos_set:
            mf_watch += 1
            mf = self_minute_feats(code)
            if mf:
                # 降级标记也落盘（「试过、不够」比「没有这个键」信息量大），但它
                # **不是产出** —— 否则「10 只里 0 只有数」会被计成 produced=10、报 ok。
                factors[code]["minute_feats"] = mf
                if mf.get("ok"):
                    mf_produced += 1
                    b = mf.get("bars")
                    if isinstance(b, int) and (mf_bars_min is None or b < mf_bars_min):
                        mf_bars_min = b
                else:
                    mf_degraded[code] = str(mf.get("reason") or "unknown")
            else:
                # 连标记都没有：本票一行样本都没读到（快照目录没有他/不是交易时段）
                mf_degraded[code] = "no_samples"
        updated += 1
        time.sleep(CALL_INTERVAL_SEC)  # 桥限流节奏
    # 大盘指数 + 持仓板块（桥快照）
    try:
        fetch_market_context(broker, pos_codes)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 大盘/板块上下文失败: {exc}")
    # 落盘前清理：只保留当前 watchlist（旧池票残留会让下游误当新鲜微观结构）
    from live_hourly_analysis import l2_prune_stale

    factors = l2_prune_stale(factors, codes)
    state = {c: v for c, v in state.items() if c in set(codes)}
    _atomic_write(STATE_FILE, state)
    _atomic_write(FACTORS_FILE, factors)
    # 能力戳：把「产物里没有这个键」和「这条链坏了」分开（旧戳沿用失败计数）。
    # 时钟必须**重新取**：上面那行 now 是轮初的，而一轮最坏约 2 分钟、快照采集器
    # 每 20 秒写一行 —— 拿轮初的 now 去比轮内新写的样本，样本恒「来自未来」，
    # 每轮正常采集都会误报 stale_source（错误文案还会甩锅给时钟/时区）。
    prev_cap = (_read_json(STATUS_FILE).get("capabilities") or {}).get("minute_k") or {}
    cap = minute_capability(now_cn(), mf_produced, mf_watch,
                            minute_feats.latest_sample_ts(SNAPSHOT_DIR), prev_cap,
                            bars_min=mf_bars_min, degraded=mf_degraded)
    if not cap["ok"]:
        print(f"  ⚠️ 分钟因子降级 [{cap['state']}] {cap['error']}"
              f"（连续 {cap['fail_streak']} 轮，自 {cap['first_fail_ts']}）")
    _atomic_write(STATUS_FILE, {
        "last_cycle_at": now.isoformat(timespec="seconds"),
        "watchlist_size": len(codes),
        "updated": updated,
        "codes": codes,
        "capabilities": {"minute_k": cap},
    })
    return updated


def print_status() -> None:
    factors = load_factors()
    if not factors:
        print("📭 无 L2 因子（还没跑过采集）")
        return
    for code, rec in sorted(factors.items()):
        f = rec.get("factors") or {}
        nonnull = {k: v for k, v in f.items() if v not in (None, 0, 0.0)}
        print(f"  {code} {rec.get('ts', '')[:19]} 现价 ¥{rec.get('now_price', 0):.2f} "
              f"score {rec.get('signal_score')} 非空 {len(nonnull)}/{len(f)} "
              f"VPIN {f.get('micro_vpin_vol_ratio')} 背离 {f.get('vol_price_divergence')} "
              f"失衡回复 {f.get('flow_imbalance_revert_speed')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="BayMax 自有 L2 逐笔因子采集（单轮）")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--status", action="store_true", help="查看最近因子快照")
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    now = now_cn()
    if not args.force and not in_window(now):
        return 0  # 非交易时段静默退出（cron 每分钟跑，不刷屏）
    broker = _load_broker()
    try:
        updated = run_pass(broker)
    except Exception as exc:  # noqa: BLE001
        # 一轮崩了必须留痕。2026-09-22 盘满实录：run_pass 在写 STATE_FILE 时抛
        # OSError(28)，异常无人接、一路穿到 sys.exit → 该轮在打印「采集完成」**之前**
        # 就死了。于是日志里 09-21 14:35→09-22 14:45 没有一行带时间戳的输出，只剩
        # 3492 行无主原始输出（含 158 个 traceback），四个半小时无人知。
        # 退出码非 0 是给 cron/看门狗的非人读信号（成功路径照旧返回 0）。
        print(f"[{now:%F %T}] ⛔ 本轮采集中止：{type(exc).__name__}: {exc}")
        return 1
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        size = status.get("watchlist_size", 0)
    except (OSError, json.JSONDecodeError):
        size = 0
    print(f"[{now:%F %T}] L2 采集完成：{updated}/{size} 只更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
