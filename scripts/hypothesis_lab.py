#!/usr/bin/env python3
"""假设库实验室 v1：把定性认知变成带胜率的可验证假设（阶段2 P1）。

首批 3 条价格代理规则（未来可用事件数据升级）：
 R1 放量滞涨：量>2×5日均量 且当日涨 0~3% → 次5日
 R2 缩量回踩：连跌3日 且末日量<0.7×5日均量 → 次5日
 R3 动量追高：5日涨>8% 后 5 日表现（追高回撤检测）
统计口径：次5日胜率=P(chg>0)、均值、样本 n；市场=全体样本对照。
用法：python scripts/hypothesis_lab.py [--symbols 50] [--days 260]
结果写回 configs/hypotheses.json（win_rate/n/updated），供提示词注入。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HYP_FILE = ROOT / "configs" / "hypotheses.json"

DEFAULT_HYPOTHESES = {
    "R1_volume_stall": {
        "name": "放量滞涨（量>2×5日均量 且日涨0~3%）", "direction": "次日5日偏空",
        "win_rate": None, "avg_ret": None, "n": None, "updated": None,
        "status": "proposed", "note": "价格代理，待事件数据升级"},
    "R2_shrink_pullback": {
        "name": "缩量回踩（连跌3日 末日量<0.7×均量）", "direction": "次日5日偏多",
        "win_rate": None, "avg_ret": None, "n": None, "updated": None,
        "status": "proposed", "note": "价格代理"},
    "R3_momentum_chase": {
        "name": "动量追高（5日涨>8%后次5日）", "direction": "追高回撤检测",
        "win_rate": None, "avg_ret": None, "n": None, "updated": None,
        "status": "proposed", "note": "价格代理"},
}


def _pick_samples(top: int) -> list:
    """用最新 partition 按成交额取流动性前 top 只（避免全市场权重失真）。"""
    import duckdb
    import glob

    base = ROOT / ".." / "projects" / "quantmind" / "data" / "quantdb" / "1_kline_data" / "daily_backward"
    if not base.is_dir():
        base = Path("/data/quantdb/1_kline_data/daily_backward")
    parts = sorted(glob.glob(f"{base}/dt=*"))[-2:]
    files = [f"{p}/*.parquet" for p in parts]
    con = duckdb.connect()
    df = con.execute(
        f"SELECT symbol, sum(amount) amt FROM read_parquet({files!r}) GROUP BY 1 ORDER BY 2 DESC LIMIT {int(top)}").df()
    return list(df["symbol"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=60)
    ap.add_argument("--days", type=int, default=260)
    args = ap.parse_args()

    import duckdb
    import glob

    base = ROOT / ".." / "projects" / "quantmind" / "data" / "quantdb" / "1_kline_data" / "daily_backward"
    if not base.is_dir():
        base = Path("/data/quantdb/1_kline_data/daily_backward")
    syms = _pick_samples(args.symbols)
    parts = sorted(glob.glob(f"{base}/dt=*"))[-args.days:]
    print(f"样本 {len(syms)} 只 · 窗口 {len(parts)} 交易日")
    con = duckdb.connect()
    df = con.execute(
        f"SELECT symbol, dt d, close, volume, amount "
        f"FROM read_parquet({[f'{p}/*.parquet' for p in parts]!r}) "
        f"WHERE symbol IN ({','.join(repr(s) for s in syms)})").df()
    df = df.sort_values(["symbol", "d"]).reset_index(drop=True)
    g = df.groupby("symbol")
    df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=4).mean().shift(1))
    df["chg"] = g["close"].pct_change() * 100
    df["chg5"] = g["close"].pct_change(5) * 100
    f = g["close"].shift(-5)  # 分组内前视，防跨股票边界错位
    df["fwd5"] = (f / df["close"] - 1) * 100

    stats: dict = {}
    cond = df["vol5"] > 0
    # R1 放量滞涨
    m = cond & (df["volume"] > 2 * df["vol5"]) & (df["chg"] >= 0) & (df["chg"] <= 3)
    stats["R1_volume_stall"] = df.loc[m, "fwd5"]
    # R2 缩量回踩
    down3 = df.groupby("symbol")["chg"].transform(
        lambda s: (s < 0) & (s.shift(1) < 0) & (s.shift(2) < 0))
    m2 = cond & down3 & (df["volume"] < 0.7 * df["vol5"])
    stats["R2_shrink_pullback"] = df.loc[m2, "fwd5"]
    # R3 动量追高
    m3 = df["chg5"] > 8
    stats["R3_momentum_chase"] = df.loc[m3, "fwd5"]
    # 市场对照
    base_ret = df["fwd5"].dropna()

    base_win = float((base_ret > 0).mean())
    base_avg = float(base_ret.mean())
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8")) if HYP_FILE.is_file() else dict(DEFAULT_HYPOTHESES)
    for key, series in stats.items():
        s = series.dropna()
        n = int(len(s))
        win = float((s > 0).mean()) if n else None
        avg = float(s.mean()) if n else None
        if key not in hyps:
            hyps[key] = dict(DEFAULT_HYPOTHESES[key])
        diff = (win - base_win) if win is not None else None
        # 状态判定对照基准：方向与实测一致且差≥3pp=verified；相反≥3pp=contradicted
        want_bull = "多" in str(hyps[key].get("direction", ""))
        if n is None or n < 30 or diff is None:
            status = "insufficient" if n < 30 else "proposed"
        elif abs(diff) < 0.03:
            status = "proposed"
        elif (diff > 0) == want_bull:
            status = "verified"
        else:
            status = "contradicted"
        hyps[key].update({"win_rate": round(win, 3) if win is not None else None,
                          "avg_ret": round(avg, 3) if avg is not None else None,
                          "vs_base_pp": round(diff * 100, 1) if diff is not None else None,
                          "n": n, "updated": datetime.now().strftime("%Y-%m-%d"),
                          "status": status})
        print(f"{key}: n={n} 胜率 {win:.1%} 均值 {avg:+.2f}% vs 基准 {base_win:.1%}/{base_avg:+.2f}% "
              f"→ {status} ({diff * 100:+.1f}pp)" if diff is not None else f"{key}: n={n}")
    HYP_FILE.parent.mkdir(exist_ok=True)
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 假设库已更新 → configs/hypotheses.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())