#!/usr/bin/env python3
"""实盘绩效指标（离线 / 可重跑 / 幂等）。

为什么需要：本仓库的绩效指标此前**全在回测侧和研究侧**——
`sharpe`/`profit_factor` 只出现在 `lab_batch_backtest.py`，`胜率` 只出现在
`hypothesis_lab.py`，**实盘账户侧一个都没有**（最大回撤在全部脚本里 0 处命中）。
日报只有"单日净值首末 + 成交笔数"。而数据早就在：
`logs/live_equity.jsonl`（净值曲线）+ `logs/live_roundtrips.jsonl`（回合台账）。

设计（与 scripts/roundtrip_features.py 同套路）：
  * **纯离线**：只读文件，不碰任何实盘路径；
  * **幂等**：整文件重写、排序稳定 → 同输入逐字节一致；
  * **样本量诚实**：每个统计量都带 `n`。实盘才起步，10 个交易日的 Sharpe
    没有统计意义——**宁可标出来，也不给一个看着像结论的数字**；
  * 数据不足的指标写 `null` + 原因，不臆造。

用法：
    python scripts/live_performance.py            # 算并写 logs/performance.json
    python scripts/live_performance.py --print    # 只打印摘要
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EQUITY_LOG = ROOT / "logs" / "live_equity.jsonl"
ROUNDTRIP_LOG = ROOT / "logs" / "live_roundtrips.jsonl"
OUT_FILE = ROOT / "logs" / "performance.json"

#: A 股年均交易日（年化换算用）
TRADING_DAYS_PER_YEAR = 244
#: 统计量少于这么多天时不给年化类指标（10 天样本的年化是噪声）
MIN_DAYS_FOR_ANNUALIZED = 20
#: 单日净值变动超过此幅度 → 疑似**记账口径变更**而非交易损益。
#: 取 12%：A 股单票涨跌停 ±10%，分账组合单日不太可能超过它。
#: 2026-09-01 实录：flash 107,978.80 → 91,325（-15.4%），当日大盘无此跌幅，
#: 是分账口径调整（live_ledger 的"2026-08-31 已买的 5 只不入分账"）所致。
#: 若不标注，区间收益会被当成真实交易亏损来决策。
DISCONTINUITY_PCT = 12.0


def detect_discontinuities(series: list[tuple[str, float]]) -> list[dict]:
    """净值序列里的单日跳变 → 疑似记账变更，逐条列出供报告标注。"""
    out = []
    for (d0, v0), (d1, v1) in zip(series, series[1:]):
        if v0 <= 0:
            continue
        chg = (v1 / v0 - 1) * 100
        if abs(chg) >= DISCONTINUITY_PCT:
            out.append({"date": d1, "from": round(v0, 2), "to": round(v1, 2),
                        "change_pct": round(chg, 2),
                        "note": "疑似记账口径变更（非交易损益），区间收益会因此失真"})
    return out


# ---------- 读数 ----------
def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def daily_series(rows: list[dict], agent: str | None = None) -> list[tuple[str, float]]:
    """→ [(date, 当日最后一个净值)]，升序。同一天多条取 ts 最大的那条。"""
    last: dict[str, tuple[str, float]] = {}
    for r in rows:
        if agent is not None and r.get("agent") != agent:
            continue
        d, ts, v = r.get("date"), str(r.get("ts") or ""), r.get("value")
        if not d or v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if d not in last or ts > last[d][0]:
            last[d] = (ts, v)
    return sorted((d, v) for d, (_ts, v) in last.items())


# ---------- 指标 ----------
def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float | None:
    """样本标准差（n-1）。n<2 返回 None。"""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def max_drawdown(series: list[tuple[str, float]]) -> dict:
    """最大回撤（负值）及其起止日。

    定义：从历史峰值回撤到谷底的最大幅度。峰值为**回撤前的最高点**（不是全局最高）。
    """
    if len(series) < 2:
        return {"max_drawdown": None, "peak_date": None, "trough_date": None,
                "reason": "净值点不足 2 个"}
    peak_v, peak_d = series[0][1], series[0][0]
    worst, worst_peak_d, worst_trough_d = 0.0, peak_d, peak_d
    for d, v in series:
        if v > peak_v:
            peak_v, peak_d = v, d
        if peak_v > 0:
            dd = v / peak_v - 1
            if dd < worst:
                worst, worst_peak_d, worst_trough_d = dd, peak_d, d
    return {"max_drawdown": round(worst * 100, 4) if worst < 0 else 0.0,
            "peak_date": worst_peak_d if worst < 0 else None,
            "trough_date": worst_trough_d if worst < 0 else None}


def returns_metrics(series: list[tuple[str, float]]) -> dict:
    """区间收益 + 日收益统计。年化类指标在样本不足时返回 None 并说明。"""
    if len(series) < 2:
        return {"n_days": len(series), "total_return_pct": None,
                "reason": "净值点不足 2 个"}

    first_v, last_v = series[0][1], series[-1][1]
    total = (last_v / first_v - 1) * 100 if first_v > 0 else None

    daily = []
    for (d0, v0), (d1, v1) in zip(series, series[1:]):
        if v0 > 0:
            daily.append(v1 / v0 - 1)

    out = {
        "n_days": len(series),
        "start_date": series[0][0],
        "end_date": series[-1][0],
        "start_value": round(first_v, 2),
        "end_value": round(last_v, 2),
        "total_return_pct": round(total, 4) if total is not None else None,
        "daily_mean_pct": round(_mean(daily) * 100, 4) if daily else None,
        "daily_std_pct": round(_std(daily) * 100, 4) if _std(daily) is not None else None,
        "n_daily_returns": len(daily),
    }

    sd = _std(daily)
    if len(daily) < MIN_DAYS_FOR_ANNUALIZED or not sd or sd == 0:
        out["annualized_return_pct"] = None
        out["sharpe"] = None
        out["reason_annualized"] = (
            f"样本 {len(daily)} 个日收益 < {MIN_DAYS_FOR_ANNUALIZED}，"
            f"年化/Sharpe 无统计意义，不予给出")
        return out

    m = _mean(daily)
    ann_ret = (1 + m) ** TRADING_DAYS_PER_YEAR - 1
    out["annualized_return_pct"] = round(ann_ret * 100, 4)
    out["sharpe"] = round(m / sd * math.sqrt(TRADING_DAYS_PER_YEAR), 4)  # 无风险利率取 0
    return out


def trading_metrics(roundtrips: list[dict], agent: str | None = None) -> dict:
    """回合台账 → 胜率 / 盈亏比 / profit factor / 平均持仓天数。"""
    rows = [r for r in roundtrips
            if (agent is None or r.get("agent") == agent)
            and r.get("status") != "insufficient_data"
            and r.get("realized_pnl") is not None]
    if not rows:
        return {"n_roundtrips": 0, "reason": "尚无已平仓回合（回合台账随平仓累积）"}

    pnls = [float(r["realized_pnl"]) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    holds = [r["holding_days"] for r in rows if r.get("holding_days") is not None]

    avg_win = _mean(wins)
    avg_loss = _mean(losses)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "n_roundtrips": len(rows),
        "win_rate_pct": round(len(wins) / len(rows) * 100, 2),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(sum(pnls), 2),
        # 盈亏比：平均盈利 / 平均亏损（无亏损时为 None，不写 inf）
        "payoff_ratio": round(avg_win / abs(avg_loss), 3) if avg_win and avg_loss else None,
        # profit factor：总盈利 / 总亏损
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_holding_days": round(_mean(holds), 3) if holds else None,
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
    }


# ---------- 组装 ----------
def build_report() -> dict:
    equity = _read_jsonl(EQUITY_LOG)
    roundtrips = _read_jsonl(ROUNDTRIP_LOG)

    agents = sorted({r.get("agent") for r in equity if r.get("agent")})
    per_agent = {}
    for a in agents:
        series = daily_series(equity, a)
        per_agent[a] = {
            "returns": returns_metrics(series),
            "risk": max_drawdown(series),
            "discontinuities": detect_discontinuities(series),
            "trading": trading_metrics(roundtrips, a),
        }

    # 合计：各 agent 净值逐日求和（分账口径下的组合曲线）
    by_date: dict[str, float] = defaultdict(float)
    for a in agents:
        for d, v in daily_series(equity, a):
            by_date[d] += v
    total_series = sorted(by_date.items())

    return {
        "generated_from": {
            "equity": str(EQUITY_LOG.relative_to(ROOT)),
            "roundtrips": str(ROUNDTRIP_LOG.relative_to(ROOT)),
            "equity_rows": len(equity),
            "roundtrip_rows": len(roundtrips),
        },
        "agents": per_agent,
        "total": {
            "n_agents": len(agents),
            "returns": returns_metrics(total_series),
            "risk": max_drawdown(total_series),
            "discontinuities": detect_discontinuities(total_series),
            "trading": trading_metrics(roundtrips),
        },
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="实盘绩效指标（离线可重跑）")
    ap.add_argument("--print", dest="print_only", action="store_true", help="只打印不写盘")
    args = ap.parse_args(argv)

    rep = build_report()
    src = rep["generated_from"]
    print(f"数据源：净值 {src['equity_rows']} 行、回合 {src['roundtrip_rows']} 行")

    for name, blk in [*rep["agents"].items(), ("合计", rep["total"])]:
        r, k, t = blk["returns"], blk["risk"], blk["trading"]
        if r.get("total_return_pct") is None:
            print(f"  {name}: 数据不足（{r.get('reason')}）")
            continue
        line = (f"  {name}: {r['n_days']} 天  收益 {r['total_return_pct']:+.2f}%")
        if k.get("max_drawdown") is not None:
            line += f"  最大回撤 {k['max_drawdown']:.2f}%"
        print(line)
        if t.get("n_roundtrips"):
            print(f"      回合 {t['n_roundtrips']} 笔  胜率 {t['win_rate_pct']}%  "
                  f"盈亏比 {t['payoff_ratio']}  平均持仓 {t['avg_holding_days']} 天")
        else:
            print(f"      回合: {t.get('reason')}")
        for dc in blk.get("discontinuities") or []:
            print(f"      ⚠️ {dc['date']} 净值 {dc['from']:,.0f} → {dc['to']:,.0f}"
                  f"（{dc['change_pct']:+.1f}%）—— {dc['note']}")
    if rep["agents"]:
        any_reason = next(iter(rep["agents"].values()))["returns"].get("reason_annualized")
        if any_reason:
            print(f"  ⚠️ {any_reason}")

    if not args.print_only:
        OUT_FILE.parent.mkdir(exist_ok=True)
        tmp = OUT_FILE.with_name(OUT_FILE.name + ".tmp")
        tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(OUT_FILE)
        print(f"已写入 {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
