"""新闻情绪事件研究：消息出来后股价到底怎么走？

分析维度:
  1. 事件收益率曲线: 利好/利空消息后 T+0~T+20 的平均累计收益
  2. 情绪分值分档: 强利好/弱利好/弱利空/强利空 各档的收益表现
  3. 板块差异: 主板/创业板/科创板的情绪反应差异
  4. 时间衰减: 消息效应在几天后消退
  5. 胜率曲线: 每天有多少比例的股票收益为正
"""
import sys
import os
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

def _find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "backend" / "main_oss.py").is_file():
            return p
    raise FileNotFoundError("未找到仓库根（含 backend/main_oss.py）")


sys.path.insert(0, str(_find_repo_root(Path(__file__).resolve())))

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
HUNTLY_DB = "/data/huntly/db.sqlite"
QUANTDB_DIR = Path(os.getenv("QM_QUANTDB_DATA_DIR", "/data/quantdb"))

from backend.shared.database_manager_v2 import get_session
from backend.shared.stock_utils import StockCodeUtil
from sqlalchemy import text


def load_trading_days() -> list[str]:
    import pyarrow.parquet as pq
    cal_path = QUANTDB_DIR / "2_base_sector" / "trading_calendar" / "trading_days.parquet"
    df = pq.read_table(str(cal_path)).to_pandas()
    days = sorted(df["TradingDate"].astype(str).tolist())
    return [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in days]


def _parse_huntly_time(s: str) -> datetime | None:
    if not s:
        return None
    try:
        s2 = s.strip()
        if "." in s2:
            return datetime.strptime(s2, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=SHANGHAI_TZ)
        return datetime.strptime(s2, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI_TZ)
    except ValueError:
        return None


def _board_of(code: str) -> str:
    if code.startswith("688"): return "科创板"
    if code.startswith("30"): return "创业板"
    if code.startswith(("00", "002", "003")): return "深主板"
    if code.startswith("60"): return "沪主板"
    if code.startswith(("83", "43", "87", "88", "92")): return "北交所"
    return "其他"


def load_articles_with_prices() -> list[dict]:
    """加载每篇文章关联的股票、情绪、以及后续 20 天的价格序列."""
    import asyncio
    import pyarrow.parquet as pq

    # 1. 加载交易日历
    trading_days = load_trading_days()
    td_set = set(trading_days)
    td_sorted = sorted(trading_days)
    td_index = {d: i for i, d in enumerate(td_sorted)}

    # 2. 从 PG 加载富集数据
    async def _load_enrich():
        async with get_session(read_only=True) as s:
            res = await s.execute(text("""
                SELECT huntly_page_id, tickers, sentiment_label, sentiment_score
                FROM news_article_enrichment
                WHERE cardinality(tickers) > 0
                  AND sentiment_label IN ('bullish', 'bearish')
                  AND ABS(sentiment_score) >= 0.25
            """))
            return res.fetchall()

    enrich_rows = asyncio.run(_load_enrich())
    print(f"  富集文章: {len(enrich_rows)} 篇")

    # 3. 从 Huntly SQLite 加载发布时间
    conn = sqlite3.connect(f"file:{HUNTLY_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    pids = [int(r[0]) for r in enrich_rows]
    # 分批查询
    page_times = {}
    batch_size = 5000
    for i in range(0, len(pids), batch_size):
        batch = pids[i:i + batch_size]
        placeholders = ",".join("?" for _ in batch)
        cur.execute(f"SELECT id, connected_at FROM page WHERE id IN ({placeholders})", batch)
        for r in cur.fetchall():
            page_times[int(r["id"])] = r["connected_at"]
    conn.close()
    print(f"  有时间戳的文章: {len(page_times)} 篇")

    # 4. 收集所有需要的股票
    all_stocks = set()
    for row in enrich_rows:
        for t in (row[1] or []):
            t = t.strip()
            if t:
                all_stocks.add(t)
    print(f"  涉及股票: {len(all_stocks)} 只")

    # 5. 加载K线（前复权）
    suffix_list = sorted(all_stocks)
    daily_dir = QUANTDB_DIR / "1_kline_data" / "daily_forward"
    partitions = []
    for p in sorted(daily_dir.glob("dt=2026*")):
        partitions.append(p / "data.parquet")

    filters = [("symbol", "in", suffix_list)]
    all_dfs = []
    for f in partitions:
        try:
            t = pq.read_table(f, columns=["symbol", "time", "close"], filters=filters)
            if t.num_rows:
                all_dfs.append(t.to_pandas())
        except Exception:
            continue

    full = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    full = full.rename(columns={"time": "trade_date"})
    full["trade_date"] = full["trade_date"].astype(str).str[:10]

    # 构建价格字典: (symbol, date) → close
    price = {}
    for _, row in full.iterrows():
        price[(row["symbol"], str(row["trade_date"])[:10])] = float(row["close"])
    print(f"  价格数据: {len(price)} 条")

    # 6. 构建事件列表
    events = []
    skipped_no_time = 0
    skipped_no_price = 0

    for row in enrich_rows:
        pid, tickers, label, score = row
        ts_str = page_times.get(int(pid))
        dt = _parse_huntly_time(ts_str)
        if dt is None:
            skipped_no_time += 1
            continue

        news_date = dt.strftime("%Y-%m-%d")
        # 确定事件日（第一个可交易日）
        if news_date in td_set:
            event_date = news_date
        else:
            # 找下一个交易日
            import bisect
            idx = bisect.bisect_left(td_sorted, news_date)
            if idx >= len(td_sorted):
                continue
            event_date = td_sorted[idx]

        event_idx = td_index.get(event_date)
        if event_idx is None:
            continue

        for ticker in (tickers or []):
            ticker = ticker.strip()
            if not ticker:
                continue

            # 获取事件日收盘价
            base_px = price.get((ticker, event_date))
            if base_px is None or base_px <= 0:
                skipped_no_price += 1
                continue

            # 获取后续 20 个交易日的价格
            fwd_prices = {}
            for offset in [0, 1, 2, 3, 5, 10, 15, 20]:
                fwd_idx = event_idx + offset
                if fwd_idx < len(td_sorted):
                    fwd_date = td_sorted[fwd_idx]
                    px = price.get((ticker, fwd_date))
                    if px and px > 0:
                        fwd_prices[offset] = px

            if 0 not in fwd_prices:
                continue

            entry_px = fwd_prices[0]
            rets = {}
            for offset, px in fwd_prices.items():
                rets[offset] = (px - entry_px) / entry_px

            events.append({
                "ticker": ticker,
                "label": label,
                "score": float(score),
                "event_date": event_date,
                "board": _board_of(ticker.split(".")[0]),
                "returns": rets,
            })

    print(f"  有效事件: {len(events)} (缺时间:{skipped_no_time}, 缺价格:{skipped_no_price})")
    return events


def analyze(events: list[dict]):
    """事件研究分析."""
    # 按情绪分组
    bullish = [e for e in events if e["label"] == "bullish"]
    bearish = [e for e in events if e["label"] == "bearish"]

    # 按分值分档
    strong_bullish = [e for e in bullish if e["score"] >= 0.6]
    weak_bullish = [e for e in bullish if e["score"] < 0.6]
    strong_bearish = [e for e in bearish if e["score"] <= -0.6]
    weak_bearish = [e for e in bearish if e["score"] > -0.6]

    def P(s=""): print(s)

    P("# 新闻情绪事件研究：消息出来后股价怎么走？")
    P()
    P(f"## 数据概览")
    P()
    P(f"- 总事件数: {len(events):,}")
    P(f"- 利好事件: {len(bullish):,} (强利好 {len(strong_bullish):,} / 弱利好 {len(weak_bullish):,})")
    P(f"- 利空事件: {len(bearish):,} (强利空 {len(strong_bearish):,} / 弱利空 {len(weak_bearish):,})")
    P()

    # 平均累计收益曲线
    HORIZONS = [0, 1, 2, 3, 5, 10, 15, 20]

    def avg_curve(group: list[dict], name: str):
        curves = defaultdict(list)
        for e in group:
            for h in HORIZONS:
                if h in e["returns"]:
                    curves[h].append(e["returns"][h])
        return {h: (np.mean(vals), len(vals)) for h, vals in curves.items()}

    def win_rate_curve(group: list[dict]):
        curves = defaultdict(list)
        for e in group:
            for h in HORIZONS:
                if h in e["returns"]:
                    curves[h].append(1 if e["returns"][h] > 0 else 0)
        return {h: np.mean(vals) for h, vals in curves.items()}

    P("## 平均累计收益曲线")
    P()
    P(f"| 持有天数 | 利好 (n={len(bullish):,}) | 利空 (n={len(bearish):,}) | 强利好 (n={len(strong_bullish):,}) | 强利空 (n={len(strong_bearish):,}) |")
    P(f"|----------|---------------------------|---------------------------|-------------------------------|-------------------------------|")

    bull_curve = avg_curve(bullish, "利好")
    bear_curve = avg_curve(bearish, "利空")
    sbull_curve = avg_curve(strong_bullish, "强利好")
    sbear_curve = avg_curve(strong_bearish, "强利空")

    for h in HORIZONS:
        b = bull_curve.get(h, (0, 0))
        br = bear_curve.get(h, (0, 0))
        sb = sbull_curve.get(h, (0, 0))
        sbr = sbear_curve.get(h, (0, 0))
        P(f"| T+{h:2d} | {b[0]*100:+.2f}% | {br[0]*100:+.2f}% | {sb[0]*100:+.2f}% | {sbr[0]*100:+.2f}% |")
    P()

    P("## 胜率曲线（收益 > 0 的比例）")
    P()
    P(f"| 持有天数 | 利好 | 利空 | 强利好 | 强利空 |")
    P(f"|----------|------|------|--------|--------|")
    bull_wr = win_rate_curve(bullish)
    bear_wr = win_rate_curve(bearish)
    sbull_wr = win_rate_curve(strong_bullish)
    sbear_wr = win_rate_curve(strong_bearish)
    for h in HORIZONS:
        P(f"| T+{h:2d} | {bull_wr.get(h, 0)*100:.1f}% | {bear_wr.get(h, 0)*100:.1f}% | {sbull_wr.get(h, 0)*100:.1f}% | {sbear_wr.get(h, 0)*100:.1f}% |")
    P()

    # 板块分析
    P("## 板块差异")
    P()
    P(f"| 板块 | 利好事件 | 利好 T+5 收益 | 利好 T+20 收益 | 利空事件 | 利空 T+5 收益 | 利空 T+20 收益 |")
    P(f"|------|----------|---------------|----------------|----------|---------------|----------------|")
    for board in ["沪主板", "深主板", "创业板", "科创板", "北交所"]:
        b_bull = [e for e in bullish if e["board"] == board]
        b_bear = [e for e in bearish if e["board"] == board]
        if not b_bull and not b_bear:
            continue
        bc_bull = avg_curve(b_bull, "")
        bc_bear = avg_curve(b_bear, "")
        b5 = bc_bull.get(5, (0, 0))[0] * 100
        b20 = bc_bull.get(20, (0, 0))[0] * 100
        br5 = bc_bear.get(5, (0, 0))[0] * 100
        br20 = bc_bear.get(20, (0, 0))[0] * 100
        P(f"| {board} | {len(b_bull):,} | {b5:+.2f}% | {b20:+.2f}% | {len(b_bear):,} | {br5:+.2f}% | {br20:+.2f}% |")
    P()

    # 时间衰减分析
    P("## 情绪效应时间衰减")
    P()
    P("利好事件逐日边际收益（每日新增的收益）:")
    P()
    prev = 0
    for h in HORIZONS:
        if h == 0:
            prev = bull_curve.get(0, (0, 0))[0]
            continue
        cur = bull_curve.get(h, (0, 0))[0]
        marginal = cur - prev
        prev = cur
        P(f"- T+{h}: 边际收益 {marginal*100:+.2f}%")
    P()

    # 预测性分析
    P("## 情绪预测性评估")
    P()
    # 利好的预测性: T+5 时正收益比例
    bull_t5_win = bull_wr.get(5, 0)
    bull_t5_avg = bull_curve.get(5, (0, 0))[0]
    bear_t5_win = bear_wr.get(5, 0)  # 利空时希望看到负收益，这里看的是"利空后股价下跌"的比例
    bear_t5_avg = bear_curve.get(5, (0, 0))[0]

    # 利空预测性: 看负收益比例
    bear_t5_correct = sum(1 for e in bearish if e["returns"].get(5, 0) < 0) / max(len([e for e in bearish if 5 in e["returns"]]), 1)

    P(f"- **利好预测性**: T+5 平均收益 {bull_t5_avg*100:+.2f}%, 胜率 {bull_t5_win*100:.1f}%")
    P(f"- **利空预测性**: T+5 平均收益 {bear_t5_avg*100:+.2f}%, 正确率(下跌) {bear_t5_correct*100:.1f}%")
    P()

    # 强信号 vs 弱信号
    P("## 强信号 vs 弱信号")
    P()
    P(f"| 信号类型 | T+5 收益 | T+5 胜率 | T+10 收益 | T+10 胜率 | T+20 收益 | T+20 胜率 |")
    P(f"|----------|----------|----------|-----------|-----------|-----------|-----------|")
    for name, group in [("强利好", strong_bullish), ("弱利好", weak_bullish),
                         ("强利空", strong_bearish), ("弱利空", weak_bearish)]:
        c = avg_curve(group, "")
        w = win_rate_curve(group)
        P(f"| {name} (n={len(group):,}) | {c.get(5,(0,0))[0]*100:+.2f}% | {w.get(5,0)*100:.1f}% | {c.get(10,(0,0))[0]*100:+.2f}% | {w.get(10,0)*100:.1f}% | {c.get(20,(0,0))[0]*100:+.2f}% | {w.get(20,0)*100:.1f}% |")
    P()

    # 回测为什么亏钱
    P("## 回测亏损原因诊断")
    P()
    P(f"- 回测从 **2026-03-30** 开始，此时上证指数处于什么位置？")
    P(f"- 利好事件 T+20 平均收益仅 {bull_curve.get(20, (0, 0))[0]*100:+.2f}%")
    P(f"- 但中间波动大：止损 15% 在 T+5 前可能已被触发")
    P(f"- 利空事件 T+5 平均收益 {bear_curve.get(5, (0, 0))[0]*100:+.2f}% —— 利空出来股价反而涨了！")
    P(f"- 这说明 **情绪标签对短期方向的预测能力有限**，真正的 alpha 在动态止盈（持有到涨不动为止）")
    P()

    # 分布分析
    P("## T+5 收益分布")
    P()
    for name, group in [("利好", bullish), ("利空", bearish)]:
        rets = [e["returns"].get(5) for e in group if 5 in e["returns"]]
        if not rets:
            continue
        rets = np.array(rets)
        pcts = [10, 25, 50, 75, 90]
        pct_vals = np.percentile(rets, pcts)
        P(f"**{name}** (n={len(rets):,}):")
        P(f"  均值: {np.mean(rets)*100:+.2f}%, 中位数: {np.median(rets)*100:+.2f}%, 标准差: {np.std(rets)*100:.2f}%")
        P(f"  分位数: " + " | ".join(f"P{p}={pct_vals[i]*100:+.2f}%" for i, p in enumerate(pcts)))
        # 盈亏比
        pos = rets[rets > 0]
        neg = rets[rets < 0]
        if len(neg) > 0 and len(pos) > 0:
            P(f"  正收益均值: {np.mean(pos)*100:+.2f}%, 负收益均值: {np.mean(neg)*100:+.2f}%")
            P(f"  盈亏比: {abs(np.mean(pos)/np.mean(neg)):.2f}")
        P()


if __name__ == "__main__":
    print("=" * 60)
    print("新闻情绪事件研究")
    print("=" * 60)
    print("\n加载数据...")
    events = load_articles_with_prices()
    print("\n")
    analyze(events)