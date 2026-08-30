#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无未来视觉多日回测：逐日选股（只用当日收盘前数据）→ 事后验证后期涨跌 vs 上证指数。

用法：
  python3 backtest_picks.py --from 20260803 --to 20260820 --top 10
  python3 backtest_picks.py --from 20260803 --to 20260820 --top 10 --json

每交易日流程：
  1) 数据日 D（收盘前数据：fusion 用 data_trade_date=D 的推理 run、L2 用 dt=D 分区）
  2) 生成 top-N 候选（L2 40% + 融合 30% + 仓位/板块/新闻，趋势不纳入，无未来视觉）
  3) 事后（后视）计算每只候选的后期涨跌：
       r1  = close[买入日B] / close[D] - 1       （买入日当天，D 收盘买）
       r3  = close[B 后第2交易日] / close[D] - 1  （持有到买入日后第2个交易日）
  4) 对上证指数同窗口收益算超额 alpha，统计命中率。

输出：data/reports/stock_picks/backtest_{from}_{to}.json / .md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

import duckdb
import pandas as pd

from pick_candidates import (
    pg_connect, resolve_primary_run, load_scores, load_l2, load_l1,
    load_names, load_industries, load_market_direction, _per_day_scores, score_candidates,
    _find_quantdb, _DEFAULT_DAILY_MODEL, IC_TOP_N,
)


def trading_dates(qs: str, lo: str, hi: str) -> list[str]:
    ds = os.path.join(qs, "1_kline_data", "index_daily")
    df = duckdb.connect().execute(
        f"SELECT DISTINCT CAST(time AS DATE) t FROM read_parquet('{ds}/dt=*/data.parquet') "
        f"WHERE time BETWEEN '{lo}' AND '{hi}' ORDER BY t"
    ).df()
    return [str(pd.Timestamp(t).date()) for t in df["t"]]


def close_matrix(qs: str, lo: str, hi: str, kline: str = "daily_backward") -> dict[str, dict[str, float]]:
    """日线收盘：{symbol: {date: close}}（同时按 suffix 与纯代码建索引）。
    kline: daily_backward(后复权,推荐) / daily_forward(前复权) / daily_unadjusted(不复权)。"""
    ds = os.path.join(qs, "1_kline_data", kline)
    df = duckdb.connect().execute(
        f"SELECT symbol, CAST(time AS DATE) t, close FROM read_parquet('{ds}/dt=*/data.parquet') "
        f"WHERE time BETWEEN '{lo}' AND '{hi}'"
    ).df()
    out: dict[str, dict[str, float]] = {}
    for sym, t, c in df.itertuples(index=False):
        s = str(sym)
        ts = str(pd.Timestamp(t).date())
        out.setdefault(s, {})[ts] = float(c)
        out.setdefault(s.split(".")[0], {})[ts] = float(c)
    return out


def index_closes(qs: str, lo: str, hi: str) -> dict[str, float]:
    ds = os.path.join(qs, "1_kline_data", "index_daily")
    df = duckdb.connect().execute(
        f"SELECT CAST(time AS DATE) t, close FROM read_parquet('{ds}/dt=*/data.parquet') "
        f"WHERE symbol='000001.SH' AND time BETWEEN '{lo}' AND '{hi}'"
    ).df()
    return {str(pd.Timestamp(t).date()): float(c) for t, c in df.itertuples(index=False)}


def _date_dates(cur, model: str | None, start: date, end: date) -> list[date]:
    """有已完成推理批次的数据日（默认不过滤模型——单模型主 run 由 resolve 决定）。"""
    if model:
        cur.execute(
            "SELECT DISTINCT data_trade_date FROM qm_model_inference_runs "
            "WHERE model_id=%s AND status='completed' AND data_trade_date BETWEEN %s AND %s "
            "ORDER BY data_trade_date",
            (model, start, end),
        )
    else:
        cur.execute(
            "SELECT DISTINCT data_trade_date FROM qm_model_inference_runs "
            "WHERE status='completed' AND data_trade_date BETWEEN %s AND %s "
            "ORDER BY data_trade_date",
            (start, end),
        )
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="20260803", help="起始数据日 YYYYMMDD")
    ap.add_argument("--to", dest="dto", default="20260820", help="结束数据日 YYYYMMDD")
    ap.add_argument("--top", type=int, default=5, help="每期候选数（默认精选 5 只）")
    ap.add_argument("--no-l2", action="store_true", help="跳过 L2 维度")
    ap.add_argument("--model", default=_DEFAULT_DAILY_MODEL, help="推理模型（默认日推模型）")
    ap.add_argument("--kline", default="daily_backward",
                    help="K线数据集：daily_backward(后复权,全量) / daily_forward(前复权,可能滞后) / daily_unadjusted(不复权)")
    ap.add_argument("--stop", type=float, default=5.0, help="止损阈值%%（持有期任一天累计跌超即当日退出）")
    ap.add_argument("--ic-top", type=int, default=None,
                    help="L2 IC 因子集规模：缺省=去冗余14，N=读检验报告按 ICIR 取前 N（深挖用）")
    ap.add_argument("--monthly", type=int, default=0,
                    help="每月随机抽 N 个数据日回测（N>0 启用；配合 --seed 可复现）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子（--monthly 用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    if args.ic_top is not None:
        import pick_candidates as _pc
        _pc.IC_TOP_N = args.ic_top if args.ic_top > 0 else None
        _pc._ic_factor_cache = None

    start = datetime.strptime(args.dfrom, "%Y%m%d").date()
    end = datetime.strptime(args.dto, "%Y%m%d").date()

    conn = pg_connect()
    cur = conn.cursor()
    names = load_names(cur)
    industries = load_industries()
    dates = _date_dates(cur, args.model, start, end)
    cur.close()
    conn.close()
    if not dates:
        print("范围内无已完成推理批次")
        sys.exit(1)

    # --monthly：每月随机抽 N 个数据日
    if args.monthly > 0:
        import random
        rng = random.Random(args.seed)
        from collections import defaultdict
        by_month: dict[str, list[date]] = defaultdict(list)
        for d in dates:
            by_month[f"{d.year:04d}-{d.month:02d}"].append(d)
        dates = []
        for m, ds in sorted(by_month.items()):
            k = min(args.monthly, len(ds))
            dates.extend(rng.sample(sorted(ds), k))
        print(f"[info] 每月抽 {args.monthly} 天（seed={args.seed}）共 {len(dates)} 个数据日")

    qs = _find_quantdb()
    lo = (min(dates)).strftime("%Y-%m-%d")
    hi = (max(dates) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")  # 后视窗口多取 10 天
    tdates = trading_dates(qs, lo, hi)
    idx = index_closes(qs, lo, hi)
    stocks = close_matrix(qs, lo, hi, kline=args.kline)

    def _next_n(d: date, n: int) -> date | None:
        """d 之后的第 n 个交易日。"""
        later = [t for t in tdates if t > d.strftime("%Y-%m-%d")]
        return later[n] if len(later) > n else None

    rows: list[dict] = []
    for D in dates:
        Ds = D.strftime("%Y-%m-%d")
        B = _next_n(D, 0)      # 第1交易日（买入日）
        B1 = _next_n(D, 1)     # 第2交易日（评估日：第二天不好）
        B2 = _next_n(D, 2)     # 第3交易日（卖出日：第三天要卖）
        if B is None:
            continue
        # 复盘方向门：看空/强烈看空 → 清空选股（空仓）
        market_dir = load_market_direction(D, D)
        gated = market_dir and market_dir.get("direction") in ("看空", "强烈看空")

        # 无未来视觉选股（仅 D 收盘前数据）
        cands: list[dict] = []
        if not gated:
            conn2 = pg_connect(); cur2 = conn2.cursor()
            scores = load_scores(cur2, D, D, model_id=args.model)
            l2 = {} if args.no_l2 else load_l2(D, D)
            l1 = {} if args.no_l2 else load_l1(D, D)
            day = _per_day_scores(scores, {}, l2, l1, industries, {})
            cands = score_candidates([day], names)[: args.top]
            cur2.close(); conn2.close()

        Bs = B
        # 第 1/2/3/5 交易日（持有期路径，D 收盘买入）
        days_hold = [_next_n(D, i) for i in range(5)]
        B5 = days_hold[4] if len(days_hold) > 4 else None
        idx_d = idx.get(Ds)
        idx_b = idx.get(Bs)
        idx_r1 = (idx_b / idx_d - 1) if (idx_d and idx_b) else None
        idx_r3 = None
        idx_r5 = None
        if B2 is not None:
            ib2 = idx.get(B2)
            idx_r3 = (ib2 / idx_d - 1) if (idx_d and ib2) else None
        if B5 is not None:
            ib5 = idx.get(B5)
            idx_r5 = (ib5 / idx_d - 1) if (idx_d and ib5) else None

        def _path_ret(closes: dict, cd: float | None) -> list[float | None]:
            """D 收盘买入 → 第1/2/3/5 交易日的累计收益（%）。"""
            if cd is None:
                return [None] * 5
            out = []
            for d in days_hold:
                if d is None:
                    out.append(None)
                    continue
                c = closes.get(d)
                out.append((c / cd - 1) * 100 if (cd and c) else None)
            return out

        def _stop_exit(path: list, stop_pct: float, target_idx: int) -> float | None:
            """带止损的退出：任一天累计收益 <= -stop% 则当日退出；否则持有到 target_idx 天。"""
            for i in range(target_idx):
                if path[i] is not None and path[i] <= -stop_pct:
                    return path[i]
            return path[target_idx] if target_idx < len(path) else None

        pick_rows = []
        for c in cands:
            sym = c["symbol"]
            closes = stocks.get(sym, {})  # close_matrix 已按纯代码建索引
            cd = closes.get(Ds)
            path = _path_ret(closes, cd)
            sell3 = path[2]          # 第3日卖
            sell5 = path[4]          # 第5日卖
            sell5_stop = _stop_exit(path, args.stop, 4)  # 第5日卖 + 5%止损
            pick_rows.append({
                "rank": c["rank"], "symbol": sym, "name": c["name"],
                "industry": c["industry"], "fusion": c["fusion"], "d_l2": c["d_l2"],
                "r1": round(path[0], 2) if path[0] is not None else None,
                "r3": round(sell3, 2) if sell3 is not None else None,
                "r5": round(sell5, 2) if sell5 is not None else None,
                "r5_stop": round(sell5_stop, 2) if sell5_stop is not None else None,
            })

        def _avg(vals: list[float | None]) -> float | None:
            v = [x for x in vals if x is not None]
            return sum(v) / len(v) if v else None

        if pick_rows:
            avg1 = _avg([p["r1"] for p in pick_rows])
            avg3 = _avg([p["r3"] for p in pick_rows])
            avg5 = _avg([p["r5"] for p in pick_rows])
            avg5s = _avg([p["r5_stop"] for p in pick_rows])
        else:
            # 复盘看空 → 空仓：收益 0%（现金）
            avg1 = avg3 = avg5 = avg5s = 0.0
        alpha1 = (avg1 - idx_r1 * 100) if (avg1 is not None and idx_r1 is not None) else None
        alpha3 = (avg3 - idx_r3 * 100) if (avg3 is not None and idx_r3 is not None) else None
        alpha5 = (avg5 - idx_r5 * 100) if (avg5 is not None and idx_r5 is not None) else None
        alpha5s = (avg5s - idx_r5 * 100) if (avg5s is not None and idx_r5 is not None) else None
        hit = sum(1 for p in pick_rows if p["r1"] is not None and (idx_r1 is not None) and p["r1"] > idx_r1 * 100)
        rows.append({
            "data_date": Ds, "buy_date": Bs, "n_picks": len(pick_rows),
            "avg_r1_pct": round(avg1, 2) if avg1 is not None else None,
            "avg_r3_pct": round(avg3, 2) if avg3 is not None else None,
            "avg_r5_pct": round(avg5, 2) if avg5 is not None else None,
            "avg_r5stop_pct": round(avg5s, 2) if avg5s is not None else None,
            "idx_r1_pct": round(idx_r1 * 100, 2) if idx_r1 is not None else None,
            "idx_r3_pct": round(idx_r3 * 100, 2) if idx_r3 is not None else None,
            "idx_r5_pct": round(idx_r5 * 100, 2) if idx_r5 is not None else None,
            "alpha1_pct": round(alpha1, 2) if alpha1 is not None else None,
            "alpha3_pct": round(alpha3, 2) if alpha3 is not None else None,
            "alpha5_pct": round(alpha5, 2) if alpha5 is not None else None,
            "alpha5stop_pct": round(alpha5s, 2) if alpha5s is not None else None,
            "hit_rate": f"{hit}/{len(pick_rows)}" if pick_rows else "0/0",
            "picks": pick_rows,
        })

    # 汇总（多策略对比）
    def _sum(vals: list[float | None]) -> float | None:
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 2) if v else None
    summary = {
        "n_days": len(rows),
        "avg_alpha1": _sum([r["alpha1_pct"] for r in rows]),
        "avg_alpha3": _sum([r["alpha3_pct"] for r in rows]),
        "avg_alpha5": _sum([r["alpha5_pct"] for r in rows]),
        "avg_alpha5_stop5": _sum([r["alpha5stop_pct"] for r in rows]),
        "days_beat_mkt_alpha3": sum(1 for r in rows if r["alpha3_pct"] is not None and r["alpha3_pct"] > 0),
        "days_beat_mkt_alpha5": sum(1 for r in rows if r["alpha5_pct"] is not None and r["alpha5_pct"] > 0),
        "days_beat_mkt_alpha5_stop": sum(1 for r in rows if r["alpha5stop_pct"] is not None and r["alpha5stop_pct"] > 0),
    }

    print("\n=== 无未来视觉多日回测（第3日卖 vs 第5日卖 vs 第5日+5%止损） ===")
    print(f"{'数据日':<12}{'买入日':<12}{'票数':<4}{'r1':>7}{'r3':>7}{'r5':>7}{'r5stop':>8}{'idx_r5':>8}{'a1':>6}{'a3':>6}{'a5':>6}{'a5s':>6}")
    for r in rows:
        print(f"{r['data_date']:<12}{r['buy_date']:<12}{r['n_picks']:<4}"
              f"{str(r['avg_r1_pct']):>7}{str(r['avg_r3_pct']):>7}{str(r['avg_r5_pct']):>7}"
              f"{str(r['avg_r5stop_pct']):>8}{str(r['idx_r5_pct']):>8}"
              f"{str(r['alpha1_pct']):>6}{str(r['alpha3_pct']):>6}{str(r['alpha5_pct']):>6}{str(r['alpha5stop_pct']):>6}")
    print(f"\n汇总: 平均 alpha  T+1={summary['avg_alpha1']}% | T+3(第3日卖)={summary['avg_alpha3']}% | "
          f"T+5(第5日卖)={summary['avg_alpha5']}% | T+5+5%止损={summary['avg_alpha5_stop5']}%")
    print(f"跑赢指数(天数): T+3={summary['days_beat_mkt_alpha3']}/{summary['n_days']} | "
          f"T+5={summary['days_beat_mkt_alpha5']}/{summary['n_days']} | T+5止损={summary['days_beat_mkt_alpha5_stop']}/{summary['n_days']}")

    # 落盘
    out_dir = os.getenv("QM_PICKS_DIR", "data/reports/stock_picks")
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{args.dfrom}_{args.dto}"
    with open(os.path.join(out_dir, f"backtest_{tag}.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f"[info] 回测结果: data/reports/stock_picks/backtest_{tag}.json")

    if args.json:
        # 打印每期 Top 候选明细
        for r in rows:
            print(f"\n--- {r['data_date']} -> {r['buy_date']} ---")
            for p in r["picks"]:
                print(f"  {p['rank']:>2}. {p['name']}({p['symbol']}) {p['industry']} "
                      f"fusion={p['fusion']} L2={p['d_l2']} r1={p['r1']}% r3={p['r3']}%")


if __name__ == "__main__":
    main()
