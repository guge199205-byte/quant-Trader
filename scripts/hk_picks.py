#!/usr/bin/env python3
"""港股候选池：拉取恒指权重股日K → 动量评分 → top 20 → data/hk_picks.json。

- 数据: data/HK_stock/get_daily_price_hk.py 的 fetch_kline（腾讯免费接口，后复权日K），
  顺带刷新 merged.jsonl（各 agent 港股数据同目录，保持新鲜）
- 评分: mom20 45% + mom60 30% + 趋势(现价vs MA20) 15% + 低波动 10%，分位排名合成
- 方向: 全池 mom20 中位数 → bullish / bearish / 震荡（注入港股盘中分析提示词）
- 消费: scripts/live_hourly_analysis_hk.py 读本文件输出注入候选池表

用法:
  python scripts/hk_picks.py                  # 拉新数据 + 评分
  python scripts/hk_picks.py --no-refresh     # 只用本地 merged.jsonl 评分
  python scripts/hk_picks.py --top 10
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "data" / "HK_stock"))

HK_DIR = ROOT / "data" / "HK_stock"
MERGED = HK_DIR / "merged.jsonl"
OUT_FILE = ROOT / "data" / "hk_picks.json"
CN_TZ = ZoneInfo("Asia/Shanghai")

# 评分权重（分位排名合成，各分量 0~1）
W_MOM20, W_MOM60, W_TREND, W_LOWVOL = 0.45, 0.30, 0.15, 0.10


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def hk_enabled_agents() -> list:
    """港股 enabled 模型（configs/hk_config.json）。"""
    try:
        cfg = json.loads((ROOT / "configs" / "hk_config.json").read_text(encoding="utf-8"))
        return [m["name"] for m in cfg.get("models", []) if m.get("enabled")]
    except (OSError, json.JSONDecodeError, KeyError):
        return ["deepseek-v4-flash", "deepseek-v4-pro", "glm-5.3-flash"]


def _ranks(values: list) -> list:
    """值 → 分位排名 0~1（并列取平均名次）。"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 / (n - 1)  # 并列平均
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def load_bars(refresh: bool) -> dict:
    """{code: [{date, open, close, high, low, volume}]}；refresh=True 重拉腾讯并刷 merged.jsonl。"""
    from get_daily_price_hk import DEFAULT_SYMBOLS, fetch_kline, to_merged_line

    bars_map: dict = {}
    if refresh:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        lines = []
        for symbol in DEFAULT_SYMBOLS:
            try:
                bars = fetch_kline(symbol, start, end)
                if not bars:
                    print(f"⚠️  {symbol}: 无数据")
                    continue
                bars_map[symbol] = bars
                lines.append(to_merged_line(symbol, bars))
                print(f"✅ {symbol}: {len(bars)} 根 ({bars[0]['date']} ~ {bars[-1]['date']})")
            except Exception as e:  # noqa: BLE001
                print(f"❌ {symbol}: {e}")
            time.sleep(0.3)  # 限速
        if lines:
            MERGED.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"merged.jsonl 已刷新（{len(lines)} 只）")
    else:
        for line in MERGED.read_text(encoding="utf-8").splitlines():
            doc = json.loads(line)
            ts = doc.get("Time Series (Daily)") or {}
            bars_map[doc["Meta Data"]["2. Symbol"]] = [
                {"date": d, "close": _f(v.get("4. sell price")),
                 "volume": _f(v.get("5. volume"))}
                for d, v in sorted(ts.items())
            ]
    return bars_map


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def score_universe(bars_map: dict) -> list:
    """全池动量评分 → [{code, name, last_close, mom20, mom60, trend, vol20, score}]。"""
    stats = []
    for code, bars in bars_map.items():
        # 腾讯接口的 OHLC 是字符串，统一转 float（0/坏值丢弃）
        closes = [c for c in (_f(b.get("close")) for b in bars) if c > 0]
        if len(closes) < 65:
            continue
        last = closes[-1]
        mom20 = last / closes[-21] - 1 if closes[-21] else 0.0
        mom60 = last / closes[-61] - 1 if closes[-61] else 0.0
        ma20 = sum(closes[-20:]) / 20
        trend = last / ma20 - 1 if ma20 else 0.0
        rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))
                if closes[i - 1]]
        mean = sum(rets) / len(rets) if rets else 0.0
        vol20 = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0.0
        stats.append({"code": code, "last_close": round(last, 3),
                      "mom20": round(mom20 * 100, 2), "mom60": round(mom60 * 100, 2),
                      "trend": round(trend * 100, 2), "vol20": round(vol20 * 100, 2)})
    if not stats:
        return []
    # 分位排名合成总分（低波动取反向：vol 越小分越高）
    r20 = _ranks([s["mom20"] for s in stats])
    r60 = _ranks([s["mom60"] for s in stats])
    rt = _ranks([s["trend"] for s in stats])
    rv = _ranks([-s["vol20"] for s in stats])
    for i, s in enumerate(stats):
        s["score"] = round((W_MOM20 * r20[i] + W_MOM60 * r60[i]
                            + W_TREND * rt[i] + W_LOWVOL * rv[i]) * 100, 1)
    return sorted(stats, key=lambda s: -s["score"])


def market_direction(stats: list) -> str:
    """全池 mom20 中位数 → 大盘方向标签（注入分析提示词）。"""
    if not stats:
        return "震荡"
    mids = sorted(s["mom20"] for s in stats)
    mid = mids[len(mids) // 2]
    if mid > 1.0:
        return "bullish（权重股动量偏多）"
    if mid < -1.0:
        return "bearish（权重股动量偏空）"
    return "震荡（权重股动量中性）"


def main() -> int:
    parser = argparse.ArgumentParser(description="港股候选池（动量评分 top N）")
    parser.add_argument("--top", type=int, default=20, help="候选池数量，默认 20")
    parser.add_argument("--no-refresh", action="store_true", help="不重拉日K，只用本地 merged.jsonl")
    args = parser.parse_args()

    bars_map = load_bars(refresh=not args.no_refresh)
    if not bars_map:
        print("❌ 无日K数据（先跑 data/HK_stock/get_daily_price_hk.py）")
        return 1
    stats = score_universe(bars_map)
    if not stats:
        print("❌ 可评分样本不足（需 ≥65 根日K）")
        return 1

    from tools.stock_names import HK_STOCK_NAMES

    top = stats[:args.top]
    picks = [{**s, "name": HK_STOCK_NAMES.get(s["code"], s["code"])} for s in top]
    doc = {
        "date": now_cn().strftime("%Y-%m-%d"),
        "generated_at": now_cn().isoformat(),
        "market_direction": market_direction(stats),
        "universe_size": len(stats),
        "picks": picks,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n候选池 top {len(picks)}（大盘 {doc['market_direction']}）→ {OUT_FILE.relative_to(ROOT)}")
    for p in picks[:10]:
        print(f"  {p['score']:5.1f}  {p['code']} {p['name']:<8} "
              f"mom20 {p['mom20']:+.1f}%  mom60 {p['mom60']:+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
