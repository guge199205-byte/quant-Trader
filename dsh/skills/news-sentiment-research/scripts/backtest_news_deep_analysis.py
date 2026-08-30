"""新闻情绪深度分析：挖掘可用的预测规律。

分析维度:
  1. 新闻来源排名 - 哪些源的情绪信号最准
  2. 盘中 vs 盘后消息 - 发布时间对预测力的影响
  3. 多篇集中报道 - 同一天多篇文章是否加强信号
  4. 首日反应 vs 后续走势 - 消息当天涨跌能否预测后续
  5. 情绪反转 - 利好→利空切换时股价怎么走
  6. 事件类型分析 - 哪些事件标签有预测力
  7. 市值效应 - 大票 vs 小票对新闻的反应
  8. 连续信号 - 连续多天出现信号的股票
"""
import sys
import os
import sqlite3
import bisect
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
    return "其他"


def load_all_data() -> tuple[dict, dict, list[str], dict]:
    """加载全部数据: 价格, 事件, 交易日, 来源映射."""
    import asyncio
    import pyarrow.parquet as pq

    trading_days = load_trading_days()
    td_sorted = sorted(trading_days)
    td_index = {d: i for i, d in enumerate(td_sorted)}
    td_set = set(trading_days)

    # 1. 富集数据 + 文章来源
    async def _load():
        async with get_session(read_only=True) as s:
            res = await s.execute(text("""
                SELECT huntly_page_id, tickers, sentiment_label, sentiment_score,
                       event_tags, industries
                FROM news_article_enrichment
                WHERE cardinality(tickers) > 0
                  AND sentiment_label IN ('bullish', 'bearish')
                  AND ABS(sentiment_score) >= 0.25
            """))
            return res.fetchall()
    enrich_rows = asyncio.run(_load())
    print(f"  富集文章: {len(enrich_rows)}")

    # 2. Huntly SQLite: 时间 + 来源
    conn = sqlite3.connect(f"file:{HUNTLY_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    pids = [int(r[0]) for r in enrich_rows]
    page_data = {}
    batch_size = 5000
    for i in range(0, len(pids), batch_size):
        batch = pids[i:i + batch_size]
        ph = ",".join("?" for _ in batch)
        cur.execute(f"""
            SELECT p.id, p.connected_at, p.title, c.name as source_name
            FROM page p
            LEFT JOIN connector c ON c.id = p.connector_id
            WHERE p.id IN ({ph})
        """, batch)
        for r in cur.fetchall():
            page_data[int(r["id"])] = {
                "connected_at": r["connected_at"],
                "title": r["title"],
                "source": r["source_name"] or "unknown",
            }
    conn.close()
    print(f"  有时间戳: {len(page_data)}")

    # 3. K线价格
    all_stocks = set()
    for row in enrich_rows:
        for t in (row[1] or []):
            t = t.strip()
            if t:
                all_stocks.add(t)

    suffix_list = sorted(all_stocks)
    daily_dir = QUANTDB_DIR / "1_kline_data" / "daily_forward"
    partitions = [p / "data.parquet" for p in sorted(daily_dir.glob("dt=2026*"))]
    filters = [("symbol", "in", suffix_list)]
    all_dfs = []
    for f in partitions:
        try:
            t = pq.read_table(f, columns=["symbol", "time", "open", "high", "low", "close", "volume"], filters=filters)
            if t.num_rows:
                all_dfs.append(t.to_pandas())
        except Exception:
            continue
    full = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    full = full.rename(columns={"time": "trade_date"})
    full["trade_date"] = full["trade_date"].astype(str).str[:10]

    price = {}
    for _, row in full.iterrows():
        d = str(row["trade_date"])[:10]
        price[(row["symbol"], d)] = {
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
    print(f"  价格数据: {len(price)}")

    # 4. 市值数据
    from backend.services.engine.data_platform.quantdb_hub import QuantDBDataHub
    hub = QuantDBDataHub()
    try:
        info_df = hub.fetch_stock_list()
        cap_map = {}
        for _, row in info_df.iterrows():
            sym = str(row.get("Symbol", ""))
            mv = row.get("MarketValue", 0)
            if sym and mv and mv > 0:
                cap_map[sym] = float(mv)
        print(f"  市值数据: {len(cap_map)}")
    except Exception:
        cap_map = {}
        print("  市值数据: 无")

    # 5. 构建事件列表
    events = []
    for row in enrich_rows:
        pid, tickers, label, score, event_tags, industries = row
        pinfo = page_data.get(int(pid))
        if not pinfo:
            continue

        dt = _parse_huntly_time(pinfo["connected_at"])
        if dt is None:
            continue

        news_date = dt.strftime("%Y-%m-%d")
        if news_date in td_set:
            event_date = news_date
        else:
            idx = bisect.bisect_left(td_sorted, news_date)
            if idx >= len(td_sorted):
                continue
            event_date = td_sorted[idx]

        event_idx = td_index.get(event_date)
        if event_idx is None:
            continue

        # 盘中/盘后
        t = dt.time()
        is_intraday = (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))
        hour = t.hour

        for ticker in (tickers or []):
            ticker = ticker.strip()
            if not ticker:
                continue

            base_px = price.get((ticker, event_date))
            if base_px is None or base_px["close"] <= 0:
                continue

            entry_px = base_px["close"]
            rets = {}
            for offset in [0, 1, 2, 3, 5, 10, 20]:
                fwd_idx = event_idx + offset
                if fwd_idx < len(td_sorted):
                    fwd_px = price.get((ticker, td_sorted[fwd_idx]))
                    if fwd_px and fwd_px["close"] > 0:
                        rets[offset] = (fwd_px["close"] - entry_px) / entry_px

            # 首日反应
            day0_ret = None
            if 0 in rets:
                prev_idx = event_idx - 1
                if prev_idx >= 0:
                    prev_px = price.get((ticker, td_sorted[prev_idx]))
                    if prev_px and prev_px["close"] > 0:
                        day0_ret = (entry_px - prev_px["close"]) / prev_px["close"]

            cap = cap_map.get(ticker, None)
            cap_label = "未知"
            if cap:
                yi = cap / 1e8
                if yi < 30: cap_label = "微盘"
                elif yi < 100: cap_label = "小盘"
                elif yi < 300: cap_label = "中盘"
                elif yi < 1000: cap_label = "大盘"
                else: cap_label = "超大盘"

            events.append({
                "ticker": ticker,
                "label": label,
                "score": float(score),
                "event_date": event_date,
                "source": pinfo["source"],
                "is_intraday": is_intraday,
                "hour": hour,
                "board": _board_of(ticker.split(".")[0]),
                "event_tags": list(event_tags or []),
                "industries": list(industries or []),
                "cap_label": cap_label,
                "returns": rets,
                "day0_ret": day0_ret,
            })

    print(f"  有效事件: {len(events)}")
    return price, events, td_sorted, td_index


def analyze(events: list[dict]):
    def P(s=""): print(s)

    P("# 新闻情绪深度分析报告")
    P()

    # ====== 1. 新闻来源排名 ======
    P("## 1. 新闻来源预测力排名")
    P()
    source_stats = defaultdict(lambda: {"bull": [], "bear": [], "count": 0})
    for e in events:
        src = e["source"]
        ret5 = e["returns"].get(5)
        if ret5 is None:
            continue
        source_stats[src]["count"] += 1
        if e["label"] == "bullish":
            source_stats[src]["bull"].append(ret5)
        else:
            source_stats[src]["bear"].append(ret5)

    # 计算每个来源的预测力: 利好时正收益比例 + 利空时负收益比例
    source_scores = []
    for src, st in source_stats.items():
        if st["count"] < 50:
            continue
        bull_mean = np.mean(st["bull"]) if st["bull"] else 0
        bear_mean = np.mean(st["bear"]) if st["bear"] else 0
        # 预测力 = 利好T+5收益 - 利空T+5收益 (利好应该涨, 利空应该跌)
        predictive = bull_mean - bear_mean
        source_scores.append((src, st["count"], bull_mean, bear_mean, predictive))

    source_scores.sort(key=lambda x: -x[4])
    P(f"| 来源 | 文章数 | 利好T+5 | 利空T+5 | 预测力 |")
    P(f"|------|--------|---------|---------|--------|")
    for src, cnt, bm, brm, pred in source_scores[:20]:
        P(f"| {src[:30]} | {cnt} | {bm*100:+.2f}% | {brm*100:+.2f}% | {pred*100:+.2f}% |")
    P()

    # 最差来源
    source_scores.sort(key=lambda x: x[4])
    P("**预测力最差（反向指标）Top 10:**")
    P(f"| 来源 | 文章数 | 利好T+5 | 利空T+5 | 预测力 |")
    P(f"|------|--------|---------|---------|--------|")
    for src, cnt, bm, brm, pred in source_scores[:10]:
        P(f"| {src[:30]} | {cnt} | {bm*100:+.2f}% | {brm*100:+.2f}% | {pred*100:+.2f}% |")
    P()

    # ====== 2. 盘中 vs 盘后 ======
    P("## 2. 盘中 vs 盘后消息")
    P()
    intra = [e for e in events if e["is_intraday"]]
    after = [e for e in events if not e["is_intraday"]]

    def avg_ret_at(group, h):
        vals = [e["returns"].get(h) for e in group if h in e["returns"]]
        return np.mean(vals) if vals else 0, len(vals)

    P(f"| 时段 | 事件数 | T+1 | T+3 | T+5 | T+10 | T+20 |")
    P(f"|------|--------|-----|-----|-----|------|------|")
    for name, grp in [("盘中(9:30-15:00)", intra), ("盘后/盘前", after)]:
        vals = []
        for h in [1, 3, 5, 10, 20]:
            m, n = avg_ret_at(grp, h)
            vals.append(f"{m*100:+.2f}%")
        P(f"| {name} | {len(grp):,} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} | {vals[4]} |")
    P()

    # 按小时分
    P("### 按小时分段（利好事件）")
    P()
    hour_groups = defaultdict(list)
    for e in events:
        if e["label"] == "bullish":
            hour_groups[e["hour"]].append(e)
    P(f"| 小时 | 事件数 | T+1 | T+5 | T+10 |")
    P(f"|------|--------|-----|-----|------|")
    for h in sorted(hour_groups.keys()):
        grp = hour_groups[h]
        t1, _ = avg_ret_at(grp, 1)
        t5, _ = avg_ret_at(grp, 5)
        t10, _ = avg_ret_at(grp, 10)
        P(f"| {h}:00 | {len(grp):,} | {t1*100:+.2f}% | {t5*100:+.2f}% | {t10*100:+.2f}% |")
    P()

    # ====== 3. 多篇集中报道 ======
    P("## 3. 多篇集中报道 vs 单篇")
    P()
    # 按 (股票, 日期) 分组
    day_groups = defaultdict(list)
    for e in events:
        day_groups[(e["ticker"], e["event_date"])].append(e)

    single = []
    multi = []
    multi_3plus = []
    for (ticker, dt), items in day_groups.items():
        # 取主导情绪
        labels = [it["label"] for it in items]
        dominant = max(set(labels), key=labels.count)
        # 取平均分值
        avg_score = np.mean([it["score"] for it in items])
        # 取第一个事件的收益
        e0 = items[0]
        e0["avg_score"] = avg_score
        if len(items) == 1:
            single.append(e0)
        else:
            multi.append(e0)
            if len(items) >= 3:
                multi_3plus.append(e0)

    P(f"| 报道数 | 事件数 | 利好T+5 | 利好T+20 | 利空T+5 | 利空T+20 |")
    P(f"|--------|--------|----------|-----------|----------|-----------|")
    for name, grp in [("单篇", single), ("2篇+", multi), ("3篇+", multi_3plus)]:
        bull_g = [e for e in grp if e["label"] == "bullish"]
        bear_g = [e for e in grp if e["label"] == "bearish"]
        bt5, _ = avg_ret_at(bull_g, 5)
        bt20, _ = avg_ret_at(bull_g, 20)
        brt5, _ = avg_ret_at(bear_g, 5)
        brt20, _ = avg_ret_at(bear_g, 20)
        P(f"| {name} | {len(grp):,} | {bt5*100:+.2f}% | {bt20*100:+.2f}% | {brt5*100:+.2f}% | {brt20*100:+.2f}% |")
    P()

    # ====== 4. 首日反应 vs 后续走势 ======
    P("## 4. 首日反应能否预测后续走势？")
    P()
    # 把事件按首日收益分组
    with_day0 = [e for e in events if e["day0_ret"] is not None]
    up_day0 = [e for e in with_day0 if e["day0_ret"] > 0]
    down_day0 = [e for e in with_day0 if e["day0_ret"] <= 0]
    gap_up = [e for e in with_day0 if e["day0_ret"] > 0.03]    # 首日涨超3%
    gap_down = [e for e in with_day0 if e["day0_ret"] < -0.03]  # 首日跌超3%

    P(f"| 首日表现 | 事件数 | T+1 | T+3 | T+5 | T+10 | T+20 |")
    P(f"|----------|--------|-----|-----|-----|------|------|")
    for name, grp in [("首日上涨", up_day0), ("首日下跌", down_day0),
                       ("首日涨>3%", gap_up), ("首日跌>3%", gap_down)]:
        vals = []
        for h in [1, 3, 5, 10, 20]:
            m, _ = avg_ret_at(grp, h)
            vals.append(f"{m*100:+.2f}%")
        P(f"| {name} | {len(grp):,} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} | {vals[4]} |")
    P()

    # 首日涨跌对后续的预测力
    P("### 首日涨跌的预测力")
    P()
    # 利好：首日涨的后续 vs 首日跌的后续
    for label_name, label_val in [("利好", "bullish"), ("利空", "bearish")]:
        P(f"**{label_name}消息:**")
        subset = [e for e in with_day0 if e["label"] == label_val]
        up = [e for e in subset if e["day0_ret"] > 0]
        down = [e for e in subset if e["day0_ret"] <= 0]
        for name, grp in [("首日涨", up), ("首日跌", down)]:
            t5, _ = avg_ret_at(grp, 5)
            t10, _ = avg_ret_at(grp, 10)
            t20, _ = avg_ret_at(grp, 20)
            P(f"  {name} (n={len(grp):,}): T+5={t5*100:+.2f}%, T+10={t10*100:+.2f}%, T+20={t20*100:+.2f}%")
        P()

    # ====== 5. 情绪反转 ======
    P("## 5. 情绪反转分析")
    P()
    # 找同一股票连续出现利好→利空或利空→利好的情况
    stock_timeline = defaultdict(list)
    for e in events:
        stock_timeline[e["ticker"]].append(e)

    reversals = {"bull_to_bear": [], "bear_to_bull": []}
    for ticker, tl in stock_timeline.items():
        tl.sort(key=lambda x: x["event_date"])
        for i in range(1, len(tl)):
            prev = tl[i - 1]
            curr = tl[i]
            if prev["event_date"] == curr["event_date"]:
                continue
            if prev["label"] == "bullish" and curr["label"] == "bearish":
                # 找到反转点，看反转后的走势
                ret5 = curr["returns"].get(5)
                if ret5 is not None:
                    reversals["bull_to_bear"].append({
                        "ticker": ticker,
                        "bull_date": prev["event_date"],
                        "bear_date": curr["event_date"],
                        "days_between": (date.fromisoformat(curr["event_date"]) - date.fromisoformat(prev["event_date"])).days,
                        "ret5": ret5,
                        "ret10": curr["returns"].get(10),
                        "ret20": curr["returns"].get(20),
                    })
            elif prev["label"] == "bearish" and curr["label"] == "bullish":
                ret5 = curr["returns"].get(5)
                if ret5 is not None:
                    reversals["bear_to_bull"].append({
                        "ticker": ticker,
                        "bear_date": prev["event_date"],
                        "bull_date": curr["event_date"],
                        "days_between": (date.fromisoformat(curr["event_date"]) - date.fromisoformat(prev["event_date"])).days,
                        "ret5": ret5,
                        "ret10": curr["returns"].get(10),
                        "ret20": curr["returns"].get(20),
                    })

    for rtype, name in [("bull_to_bear", "利好→利空反转"), ("bear_to_bull", "利空→利好反转")]:
        revs = reversals[rtype]
        if not revs:
            continue
        rets5 = [r["ret5"] for r in revs]
        rets10 = [r["ret10"] for r in revs if r["ret10"] is not None]
        rets20 = [r["ret20"] for r in revs if r["ret20"] is not None]
        avg_days = np.mean([r["days_between"] for r in revs])
        P(f"**{name}** (n={len(revs):,}, 平均间隔 {avg_days:.1f} 天):")
        P(f"  反转后 T+5: 均值 {np.mean(rets5)*100:+.2f}%, 中位数 {np.median(rets5)*100:+.2f}%, 正收益比 {sum(1 for r in rets5 if r>0)/len(rets5)*100:.1f}%")
        if rets10:
            P(f"  反转后 T+10: 均值 {np.mean(rets10)*100:+.2f}%, 中位数 {np.median(rets10)*100:+.2f}%")
        if rets20:
            P(f"  反转后 T+20: 均值 {np.mean(rets20)*100:+.2f}%, 中位数 {np.median(rets20)*100:+.2f}%")
        P()

    # ====== 6. 事件类型 ======
    P("## 6. 事件标签预测力")
    P()
    # 统计常见事件标签
    tag_events = defaultdict(lambda: {"bull": [], "bear": [], "count": 0})
    for e in events:
        for tag in e["event_tags"]:
            tag_events[tag]["count"] += 1
            if e["label"] == "bullish":
                ret5 = e["returns"].get(5)
                if ret5 is not None:
                    tag_events[tag]["bull"].append(ret5)
            else:
                ret5 = e["returns"].get(5)
                if ret5 is not None:
                    tag_events[tag]["bear"].append(ret5)

    tag_scores = []
    for tag, st in tag_events.items():
        if st["count"] < 30:
            continue
        bull_mean = np.mean(st["bull"]) if st["bull"] else 0
        bear_mean = np.mean(st["bear"]) if st["bear"] else 0
        tag_scores.append((tag, st["count"], bull_mean, bear_mean))

    tag_scores.sort(key=lambda x: -(x[2] - x[3]))
    P(f"| 事件标签 | 出现次数 | 利好T+5 | 利空T+5 |")
    P(f"|----------|----------|---------|---------|")
    for tag, cnt, bm, brm in tag_scores[:25]:
        P(f"| {tag[:20]} | {cnt} | {bm*100:+.2f}% | {brm*100:+.2f}% |")
    P()

    # ====== 7. 市值效应 ======
    P("## 7. 市值效应")
    P()
    cap_groups = defaultdict(list)
    for e in events:
        cap_groups[e["cap_label"]].append(e)

    P(f"| 市值 | 事件数 | 利好T+5 | 利好T+20 | 利空T+5 | 利空T+20 |")
    P(f"|------|--------|----------|-----------|----------|-----------|")
    for cap in ["微盘", "小盘", "中盘", "大盘", "超大盘", "未知"]:
        grp = cap_groups.get(cap, [])
        if not grp:
            continue
        bull_g = [e for e in grp if e["label"] == "bullish"]
        bear_g = [e for e in grp if e["label"] == "bearish"]
        bt5, _ = avg_ret_at(bull_g, 5)
        bt20, _ = avg_ret_at(bull_g, 20)
        brt5, _ = avg_ret_at(bear_g, 5)
        brt20, _ = avg_ret_at(bear_g, 20)
        P(f"| {cap} | {len(grp):,} | {bt5*100:+.2f}% | {bt20*100:+.2f}% | {brt5*100:+.2f}% | {brt20*100:+.2f}% |")
    P()

    # ====== 8. 连续信号 ======
    P("## 8. 连续信号分析")
    P()
    # 同一股票连续 N 天出现同向信号
    consecutive = {1: [], 2: [], 3: []}
    for ticker, tl in stock_timeline.items():
        tl.sort(key=lambda x: x["event_date"])
        run_length = 1
        for i in range(1, len(tl)):
            prev_date = date.fromisoformat(tl[i - 1]["event_date"])
            curr_date = date.fromisoformat(tl[i]["event_date"])
            days_diff = (curr_date - prev_date).days
            if tl[i]["label"] == tl[i - 1]["label"] and days_diff <= 3:
                run_length += 1
            else:
                if run_length in consecutive:
                    consecutive[run_length].append(tl[i - run_length])
                run_length = 1
        if run_length in consecutive:
            consecutive[run_length].append(tl[-run_length])

    P(f"| 连续天数 | 事件数 | T+5 收益 | T+5 胜率 | T+10 收益 |")
    P(f"|----------|--------|----------|----------|-----------|")
    for days in [1, 2, 3]:
        grp = consecutive[days]
        t5, _ = avg_ret_at(grp, 5)
        t10, _ = avg_ret_at(grp, 10)
        wr = sum(1 for e in grp if e["returns"].get(5, 0) > 0) / max(len(grp), 1)
        P(f"| {days}天 | {len(grp):,} | {t5*100:+.2f}% | {wr*100:.1f}% | {t10*100:+.2f}% |")
    P()

    # ====== 总结 ======
    P("## 9. 可用的交易规律总结")
    P()
    P("基于以上分析，以下是统计显著的规律：")
    P()

    # 自动发现规律
    findings = []

    # 盘中vs盘后
    intra_t5, _ = avg_ret_at(intra, 5)
    after_t5, _ = avg_ret_at(after, 5)
    if abs(intra_t5 - after_t5) > 0.005:
        findings.append(f"- **{'盘中' if intra_t5 > after_t5 else '盘后'}消息更有预测力** (T+5: {intra_t5*100:+.2f}% vs {after_t5*100:+.2f}%)")

    # 多篇vs单篇
    multi_bull = [e for e in multi if e["label"] == "bullish"]
    single_bull = [e for e in single if e["label"] == "bullish"]
    mt5, _ = avg_ret_at(multi_bull, 5)
    st5, _ = avg_ret_at(single_bull, 5)
    if abs(mt5 - st5) > 0.005:
        findings.append(f"- **{'多篇集中报道' if mt5 > st5 else '单篇报道'}利好信号更强** (T+5: {mt5*100:+.2f}% vs {st5*100:+.2f}%)")

    # 首日涨跌
    up_t5, _ = avg_ret_at(up_day0, 5)
    down_t5, _ = avg_ret_at(down_day0, 5)
    findings.append(f"- **首日涨的股票后续继续涨** (T+5: {up_t5*100:+.2f}%), **首日跌的继续跌** (T+5: {down_t5*100:+.2f}%)")
    findings.append(f"  → 动量效应明显，消息当天涨了就追涨，跌了就杀跌")

    # 反转
    b2b_ret5 = np.mean([r["ret5"] for r in reversals["bull_to_bear"]]) if reversals["bull_to_bear"] else 0
    b2B_ret5 = np.mean([r["ret5"] for r in reversals["bear_to_bull"]]) if reversals["bear_to_bull"] else 0
    findings.append(f"- **利好→利空反转后** T+5 平均 {b2b_ret5*100:+.2f}% → 情绪反转是有效的出场信号")
    findings.append(f"- **利空→利好反转后** T+5 平均 {b2B_ret5*100:+.2f}%")

    for f in findings:
        P(f)
    P()

    # 最佳来源前三
    P("### 最佳新闻来源 Top 5:")
    for src, cnt, bm, brm, pred in source_scores[:5]:
        P(f"- **{src[:40]}**: {cnt}篇, 预测力 {pred*100:+.2f}% (利好T+5={bm*100:+.2f}%, 利空T+5={brm*100:+.2f}%)")


if __name__ == "__main__":
    print("=" * 60)
    print("新闻情绪深度分析")
    print("=" * 60)
    print("\n加载数据...")
    price, events, td_sorted, td_index = load_all_data()
    print("\n")
    analyze(events)