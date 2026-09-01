#!/usr/bin/env python3
"""美股候选池（us_picks）：quantus 本地日线动量打分 → data/us_picks.json。

- 全池: quantus 最新交易日成交量达标的标的（流动性过滤，按成交额取前 300）
- 评分: mom20 45% + mom60 30% + 趋势(现价vs MA20) 15% + 低波动 10%（分位排名，同 hk_picks）
- 大盘方向: 全池 median mom20（>1% 偏多 / <-1% 偏空 / 震荡）
- 池隔日作废（date != 今日美东日期 → 空）——us_picks 必须盘前跑

用法:
  python scripts/us_picks.py            # 生成（盘前 cron）
  python scripts/us_picks.py --top 20   # 取前 N
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT_FILE = ROOT / "data" / "us_picks.json"
NY_TZ = ZoneInfo("America/New_York")
W_MOM20, W_MOM60, W_TREND, W_LOWVOL = 0.45, 0.30, 0.15, 0.10
MIN_VOLUME = 2_000_000     # 流动性过滤：最新交易日成交 ≥ 200 万股
UNIVERSE_CAP = 300         # 按成交额取前 300 只打分
TOP_N = 20


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_universe_bars(days: int = 70) -> dict:
    """quantus 全部标的近 days 日线（duckdb 单查询）→ {symbol: [{date, close, volume}]}。
    按最新交易日成交量过滤 + 成交额取前 UNIVERSE_CAP。"""
    import duckdb
    import glob

    base = "/home/zbox/projects/quantmind/data/quantus/1_kline_data/daily_forward"
    cutoff = (date.today() - timedelta(days=200)).isoformat()
    parts = sorted(
        p for p in glob.glob(f"{base}/dt=*/data.parquet")
        if p.split("dt=")[1][:10] >= cutoff)
    if not parts:
        return {}
    union = " UNION ALL ".join(
        f"SELECT symbol, time, open, high, low, close, volume, amount FROM read_parquet('{p}')"
        for p in parts)
    try:
        df = duckdb.sql(f"""
            WITH allbars AS ({union}),
            latest AS (
                SELECT symbol, time, volume, amount,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time DESC) rn
                FROM allbars),
            liquid AS (
                SELECT symbol FROM latest
                WHERE rn = 1 AND volume >= {MIN_VOLUME}
                ORDER BY amount DESC LIMIT {UNIVERSE_CAP}),
            kept AS (
                SELECT a.symbol, a.time, a.close, a.volume
                FROM allbars a JOIN liquid l ON a.symbol = l.symbol)
            SELECT symbol, time, close, volume FROM kept
            ORDER BY symbol, time""").df()
    except Exception:  # noqa: BLE001
        return {}
    bars_map: dict = {}
    for r in df.itertuples():
        bars_map.setdefault(r.symbol, []).append(
            {"date": str(r.time)[:10], "close": _f(r.close), "volume": _f(r.volume)})
    return bars_map


def _ranks(values: list) -> list:
    """百分位排名（并列取平均分）：值越大排名越高 → [0,1]。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 / (len(values) - 1) if len(values) > 1 else 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def score_universe(bars_map: dict) -> list:
    """全池动量评分 → [{code, last_close, mom20, mom60, trend, vol20, score}]。"""
    stats = []
    for code, bars in bars_map.items():
        closes = [b["close"] for b in bars if b["close"] > 0]
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
    r20 = _ranks([s["mom20"] for s in stats])
    r60 = _ranks([s["mom60"] for s in stats])
    rt = _ranks([s["trend"] for s in stats])
    rv = _ranks([-s["vol20"] for s in stats])  # 低波动取反向
    for i, s in enumerate(stats):
        s["score"] = round(
            W_MOM20 * r20[i] + W_MOM60 * r60[i] + W_TREND * rt[i] + W_LOWVOL * rv[i], 4)
    stats.sort(key=lambda s: -s["score"])
    return stats


def market_direction(stats: list) -> str:
    """全池 median mom20 → 大盘方向。"""
    if not stats:
        return "未知"
    moms = sorted(s["mom20"] for s in stats)
    med = moms[len(moms) // 2]
    if med > 1:
        return f"偏多（全池中位 20 日动量 {med:+.2f}%）"
    if med < -1:
        return f"偏空（全池中位 20 日动量 {med:+.2f}%）"
    return f"震荡（全池中位 20 日动量 {med:+.2f}%）"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="美股候选池（quantus 动量打分）")
    parser.add_argument("--top", type=int, default=TOP_N)
    args = parser.parse_args()

    bars_map = fetch_universe_bars()
    if not bars_map:
        print("❌ quantus 数据不可读（检查挂载路径）")
        return 1
    stats = score_universe(bars_map)
    picks = stats[: args.top]
    doc = {
        "date": datetime.now(NY_TZ).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(NY_TZ).isoformat(timespec="seconds"),
        "market_direction": market_direction(stats),
        "universe_size": len(stats),
        "picks": [{"code": s["code"], "name": s["code"], "rank": i + 1,
                   "last_close": s["last_close"], "score": s["score"],
                   "mom20": s["mom20"], "mom60": s["mom60"],
                   "trend": s["trend"], "vol20": s["vol20"]}
                  for i, s in enumerate(picks)],
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_name(OUT_FILE.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT_FILE)
    print(f"\n美股候选池 top {len(picks)}（全池 {len(stats)}，大盘 {doc['market_direction']}）"
          f"→ {OUT_FILE.relative_to(ROOT)}")
    for i, p in enumerate(picks[:10]):
        print(f"  #{i + 1:>2} {p['code']:<6} score {p['score']:.3f} "
              f"mom20 {p['mom20']:+.2f}% mom60 {p['mom60']:+.2f}% trend {p['trend']:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
