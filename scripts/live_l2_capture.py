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
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

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
    """A股交易时段（北京）：9:30-11:30 / 13:00-15:00 工作日。"""
    if now.weekday() >= 5:
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
        half = max(len(rets) // 2, 1)
        cur_rv = sum(rets[-half:]) / half
        day_rv = sum(rets) / len(rets)
        factors["micro_zone_rv_ratio_close"] = round(_clip(cur_rv / (day_rv + 1e-9), 0, 10), 6)
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


def load_state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_factors() -> dict:
    try:
        d = json.loads(FACTORS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------- 大盘/板块上下文（桥快照，不占因子节奏） ----------

def fetch_market_context(broker, holdings_codes: list) -> dict:
    """大盘指数 + 持仓股所属板块（行业优先）+ 板块指数当日涨跌。
    全部经桥透传；单只失败跳过。→ data/market_snapshot.json（盘中分析提示词消费）。"""
    ctx: dict = {"ts": now_cn().isoformat(timespec="seconds"), "indices": {}, "sectors": {}}
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
    for code in holdings_codes[:5]:
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


_TICK_OK = True  # TdxAiData 分笔权限标记（Token Insufficient 时全局置 False 快速跳过）
_MINUTE_OK = True  # TdxAiData 分钟K限流标记（超时/失败时本轮起跳过）


def _timebox(fn, timeout_s: float = 5.0, *args, **kwargs):
    """线程 + join 硬超时：TdxAiData 重试循环可能无限挂（限流时），必须时间盒。"""
    import threading

    res: dict = {}

    def _run():
        try:
            res["v"] = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            res["e"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError("TdxAiData 调用超时（限流？）")
    if "e" in res:
        raise res["e"]
    return res.get("v")


def fetch_minute_tick(code: str) -> tuple[dict, dict]:
    """TdxAiData 分钟K特征 + 分笔失衡（走 TdxAiData 官方接口，不占桥限流）。
    分笔需额外 token 权限（实测 Token Insufficient），默认跳过——设
    BAYMAX_TICK_ENABLED=1 才尝试；分钟K 5s 硬超时，限流时全局跳过不拖慢采集。"""
    from agent_tools.datasources import tdx_aidata

    global _TICK_OK, _MINUTE_OK
    mf: dict = {}
    tk: dict = {}
    if _MINUTE_OK:
        try:
            bars = _timebox(tdx_aidata.get_klines, 5.0, code, interval="5m", count=12) or []
            closes = [float(b.get("close") or 0) for b in bars if b.get("close")]
            vols = [float(b.get("volume") or 0) for b in bars if b.get("volume")]
            if len(closes) >= 6 and closes[0] > 0:
                mf["mom30m"] = round((closes[-1] / closes[-6] - 1) * 100, 3)
                rets = [c2 / c1 - 1 for c1, c2 in zip(closes, closes[1:]) if c1 > 0]
                if rets:
                    avg = sum(rets) / len(rets)
                    mf["vol5m"] = round((sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 3)
            if len(vols) >= 6:
                avg = sum(vols) / len(vols)
                mf["vol_ratio"] = round(vols[-1] / avg, 2) if avg > 0 else None
        except Exception as exc:  # noqa: BLE001
            _MINUTE_OK = False
            print(f"  ⚠️ TdxAiData 分钟K不可用（限流/超时），本轮起跳过: {exc}")
    if _TICK_OK and os.getenv("BAYMAX_TICK_ENABLED") == "1":
        try:
            for date_fmt in (now_cn().strftime("%Y%m%d"), now_cn().strftime("%Y-%m-%d")):
                t = tdx_aidata.get_tick_data(code, date_fmt, wantnum=100)
                rows = t.get("data") if isinstance(t, dict) else t
                if not isinstance(rows, list) or not rows:
                    continue
                buy = sum(1 for r in rows if str(r.get("BSFlag") or "") in ("B", "1"))
                sell = sum(1 for r in rows if str(r.get("BSFlag") or "") in ("S", "2"))
                if buy + sell > 0:
                    tk = {"buy": buy, "sell": sell, "ratio": round((buy - sell) / (buy + sell), 3)}
                break
        except Exception as exc:  # noqa: BLE001
            if "Token" in str(exc) or "Insufficient" in str(exc) or "13" in str(exc):
                _TICK_OK = False
                print("  ⚠️ TdxAiData 分笔权限不足（Token Insufficient），本轮起跳过分笔")
    return mf, tk


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
    codes = codes[:MAX_WATCHLIST]
    if not codes:
        return 0

    state = load_state()
    factors = load_factors()
    now = now_cn()
    updated = 0
    calls = 0
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
            "volume": snap.get("volume") or 0,
            "factors": fac,
            "signal_score": build_signal_score(fac),
        }
        # 持仓股附加：分钟K特征 + 分笔失衡（TdxAiData，不占桥限流）
        if code in set(pos_codes):
            mf, tk = fetch_minute_tick(code)
            if mf:
                factors[code]["minute_feats"] = mf
            if tk:
                factors[code]["tick_imb"] = tk
        updated += 1
        time.sleep(CALL_INTERVAL_SEC)  # 桥限流节奏
    # 大盘指数 + 持仓板块（桥快照）
    try:
        fetch_market_context(broker, pos_codes)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 大盘/板块上下文失败: {exc}")
    _atomic_write(STATE_FILE, state)
    _atomic_write(FACTORS_FILE, factors)
    _atomic_write(STATUS_FILE, {
        "last_cycle_at": now.isoformat(timespec="seconds"),
        "watchlist_size": len(codes),
        "updated": updated,
        "codes": codes,
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
    updated = run_pass(broker)
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        size = status.get("watchlist_size", 0)
    except (OSError, json.JSONDecodeError):
        size = 0
    print(f"[{now:%F %T}] L2 采集完成：{updated}/{size} 只更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
