#!/usr/bin/env python3
"""因子批量筛选器：alpha_library（452 因子）× 事件研究框架 → 假设库扩容。

口径（与 hypothesis_lab 对齐）：
  - 每日横截面 Spearman IC（因子值 vs fwd_ret_5，标签来自 alpha_library_labels）
  - 事件化：每日因子值 Top-5 的次5日胜率 vs 全样本基准（diff_pp）
  - Walk-forward 纪律：末窗 OOS IC 与 IS 同向才可注册 verified（复用 decide_status_wf）
  - 注册门槛：|mean IC| ≥ ic-min 且 ICIR ≥ icir-min 且 OOS 同向 且 pooled diff ≥ 3pp

用法：
  python scripts/factor_screen.py                       # 全量筛选并注册 top-K 进假设库
  python scripts/factor_screen.py --top-k 15 --ic-min 0.03
  python scripts/factor_screen.py --dry                 # 只出排名不注册
"""
import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from hypothesis_lab import decide_status, decide_status_wf  # noqa: E402

ALPHA_DIR = ROOT.parent / "projects/quantmind/data/quantdb/6_ml_datasets/alpha_library"
LABEL_DIR = ROOT.parent / "projects/quantmind/data/quantdb/6_ml_datasets/alpha_library_labels"
if not ALPHA_DIR.is_dir():
    ALPHA_DIR = Path("/data/quantdb/6_ml_datasets/alpha_library")
    LABEL_DIR = Path("/data/quantdb/6_ml_datasets/alpha_library_labels")
HYP_FILE = ROOT / "configs" / "hypotheses.json"


def _pick_symbols(top: int, days: int) -> list:
    parts = sorted(ALPHA_DIR.glob("dt=*"))[-days:]
    files = [f"{p}/*.parquet" for p in parts]
    con = __import__("duckdb").connect()
    df = con.execute(
        f"SELECT symbol, count(*) n FROM read_parquet({files!r}) GROUP BY 1 "
        f"HAVING n = {days} ORDER BY 1 LIMIT {int(top) * 3}").df()
    return list(df["symbol"])[:top]


BASE_COLS = {"open", "high", "low", "close", "volume", "amount", "symbol",
             "date", "time", "dt", "filename"}
ML_DIR = ROOT.parent / "projects/quantmind/data/quantdb/6_ml_datasets"
if not ML_DIR.is_dir():
    ML_DIR = Path("/data/quantdb/6_ml_datasets")


def _read_factors(dataset: str, symbols: list, days: int) -> pd.DataFrame:
    """读一个因子数据集最近 days 分区（仅因子列 + symbol/date）。"""
    d = ML_DIR / dataset
    parts = sorted(d.glob("dt=*"))[-days:]
    files = []
    for p in parts:  # 解析真实 parquet 文件并过滤空/缺失（重建期间分区会被替换）
        files.extend(glob.glob(f"{p}/*.parquet"))
    files = [f for f in files if __import__("os").path.getsize(f) > 512]
    con = __import__("duckdb").connect()
    cols = [c[0] for c in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",
                                      [files[0]]).fetchall()]
    factors = [c for c in cols if c not in BASE_COLS]
    ph = ",".join(repr(s) for s in symbols)
    if "date" in cols:
        sel = "symbol, date, " + ", ".join(factors)
        df = con.execute(
            f"SELECT {sel} FROM read_parquet({files!r}) WHERE symbol IN ({ph})").df()
        df["date"] = df["date"].astype(str).str[:10]
    else:  # alpha_library：日期在分区名里
        sel = "symbol, filename, " + ", ".join(factors)
        df = con.execute(
            f"SELECT {sel} FROM read_parquet({files!r}) WHERE symbol IN ({ph})").df()
        df["date"] = df["filename"].str.extract(r"dt=(\d{8})", expand=False)\
            .map(lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}")
        df = df.drop(columns=["filename"])
    return df


def load_panel(symbols: list, days: int) -> pd.DataFrame:
    """三库合并面板：alpha(452) + l1(换手/动量/波动/筹码/风格/行业/概念) + l2(微观/资金流)。"""
    con = __import__("duckdb").connect()
    lparts = sorted(LABEL_DIR.glob("dt=*"))[-days:]
    lfiles = [f"{p}/*.parquet" for p in lparts]
    ph = ",".join(repr(s) for s in symbols)
    lab = con.execute(
        f"SELECT symbol, time, fwd_ret_5 FROM read_parquet({lfiles!r}) "
        f"WHERE symbol IN ({ph})").df()
    lab["date"] = pd.to_datetime(lab["time"]).dt.strftime("%Y-%m-%d")
    lab = lab[["symbol", "date", "fwd_ret_5"]].drop_duplicates(["symbol", "date"])
    out = lab
    for dataset in ("alpha_library", "l1_factors", "l2_factors"):
        try:
            part = _read_factors(dataset, symbols, days)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ {dataset} 读取失败跳过: {str(exc)[:80]}")
            continue
        if part.empty:
            continue
        out = out.merge(part, on=["symbol", "date"], how="left") if             "fwd_ret_5" in out.columns or dataset != "alpha_library" else \
            part.merge(lab, on=["symbol", "date"], how="left")
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def screen(df: pd.DataFrame, wf_window: int = 40, ic_min: float = 0.02,
           icir_min: float = 0.5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (过门槛排名表, 全部逐日 IC 明细 ev)。"""
    ev_all = pd.DataFrame()
    factor_cols = [c for c in df.columns if c not in BASE_COLS
                   and c != "fwd_ret_5"]
    # 每日横截面秩（Spearman 的 rank 前置），一次算好全部因子
    rank_df = df.groupby("date")[factor_cols].rank(pct=True)
    rank_fwd = df.groupby("date")["fwd_ret_5"].rank(pct=True)
    dates = sorted(df["date"].unique())
    recs, ev_all_rows = [], []
    for d in dates:
        m = df["date"] == d
        R = rank_df.loc[m, factor_cols].to_numpy(dtype=float)
        y = rank_fwd.loc[m].to_numpy(dtype=float)
        fwd = df.loc[m, "fwd_ret_5"].to_numpy(dtype=float)
        if len(y) < 20:
            continue
        # 逐因子成对有效（各列缺失模式不同，不能要求整行全有效）
        Rz = np.where(np.isfinite(R), R - np.nanmean(R, axis=0, keepdims=True), 0.0)
        yz = np.where(np.isfinite(y), y - np.nanmean(y), 0.0)
        valid = np.isfinite(R) & np.isfinite(y)[:, None] & np.isfinite(fwd)[:, None]
        nv = valid.sum(axis=0)
        if nv.max() < 20:
            continue
        cov = (Rz * yz[:, None] * valid).sum(axis=0) / np.maximum(nv - 1, 1)
        sd_r = np.sqrt((Rz * Rz * valid).sum(axis=0) / np.maximum(nv - 1, 1))
        sd_y = np.sqrt((yz[:, None] ** 2 * valid).sum(axis=0) / np.maximum(nv - 1, 1))
        with np.errstate(divide="ignore", invalid="ignore"):
            ic = np.where((nv >= 20) & (sd_r > 0) & (sd_y > 0),
                          cov / (sd_r * sd_y), np.nan)
        # 事件化：Top-5（正向）与 Bottom-5（反向）因子值的次5日胜率 vs 全样本
        R_order = np.where(np.isfinite(R), R, -np.inf)
        idx = np.argsort(-R_order, axis=0)[:5]                # 每因子 top5 行号
        top_fwd = np.take_along_axis(
            fwd[:, None].repeat(len(factor_cols), axis=1), idx, axis=0)
        with np.errstate(invalid="ignore"):
            top_win = np.nanmean(top_fwd > 0, axis=0)
            top_ret = np.nanmean(top_fwd, axis=0)
        R_asc = np.where(np.isfinite(R), R, np.inf)
        idx_b = np.argsort(R_asc, axis=0)[:5]                 # 每因子 bottom5 行号
        bot_fwd = np.take_along_axis(
            fwd[:, None].repeat(len(factor_cols), axis=1), idx_b, axis=0)
        with np.errstate(invalid="ignore"):
            bot_win = np.nanmean(bot_fwd > 0, axis=0)
            bot_ret = np.nanmean(bot_fwd, axis=0)
        base_win = float((fwd > 0).mean())
        for j, f in enumerate(factor_cols):
            if not np.isfinite(ic[j]):
                continue
            recs.append({"factor": f, "date": d, "ic": float(ic[j]),
                         "top_win": float(top_win[j]), "top_ret": float(top_ret[j]),
                         "bot_win": float(bot_win[j]), "bot_ret": float(bot_ret[j]),
                         "base_win": base_win, "n_day": int(nv[j])})
            ev_all_rows.append(dict({"date": d, "factor": f, "ic": float(ic[j]),
                                     "top_win": float(top_win[j]), "top_ret": float(top_ret[j]),
                                     "bot_win": float(bot_win[j]), "bot_ret": float(bot_ret[j])}))
    ev = pd.DataFrame(recs)
    ev_all = pd.DataFrame(ev_all_rows)
    out = []
    for f, g in ev.groupby("factor"):
        ic_m, ic_s = g["ic"].mean(), g["ic"].std()
        icir = ic_m / ic_s if ic_s else 0.0
        diff_pp = (g["top_win"].mean() - g["base_win"].mean()) * 100
        n = int(g["n_day"].sum() / 5)
        g_sorted = g.sort_values("date")
        # WF：末 wf_window 天为 OOS
        oos_days = sorted(g_sorted["date"].unique())[-wf_window:]
        is_ic = g_sorted[~g_sorted["date"].isin(oos_days)]["ic"].mean()
        oos_ic = g_sorted[g_sorted["date"].isin(oos_days)]["ic"].mean()
        bot_diff_pp = (g["bot_win"].mean() - g["base_win"].mean()) * 100
        bot_oos_days = oos_days
        bot_is = g_sorted[~g_sorted["date"].isin(bot_oos_days)]["bot_win"].mean() \
            - g_sorted[~g_sorted["date"].isin(bot_oos_days)]["base_win"].mean()
        bot_oos = g_sorted[g_sorted["date"].isin(bot_oos_days)]["bot_win"].mean() \
            - g_sorted[g_sorted["date"].isin(bot_oos_days)]["base_win"].mean()
        out.append({"factor": f, "ic": round(ic_m, 4), "icir": round(icir, 2),
                    "diff_pp": round(diff_pp, 1), "n": n,
                    "win_rate": round(float(g["top_win"].mean()), 3),
                    "avg_ret": round(float(g["top_ret"].mean()), 3),
                    "bot_win_rate": round(float(g["bot_win"].mean()), 3),
                    "bot_ret": round(float(g["bot_ret"].mean()), 3),
                    "is_ic": round(is_ic, 4), "oos_ic": round(oos_ic, 4),
                    "wf_ok": bool(is_ic * oos_ic > 0),
                    "bot_diff_pp": round(bot_diff_pp, 1),
                    "bot_wf_ok": bool(bot_is * bot_oos > 0)})
    res = pd.DataFrame(out)
    res = res[res["ic"].abs() >= ic_min]
    res = res[res["icir"].abs() >= icir_min]
    res["rank_key"] = res["ic"].abs() * res["icir"].abs()
    return res.sort_values("rank_key", ascending=False), ev_all


def orthogonal_select(res: pd.DataFrame, ev: pd.DataFrame,
                      max_keep: int, corr_max: float = 0.7) -> pd.DataFrame:
    """贪心正交：按 rank_key 降序，日 IC 序列与已选因子相关 <corr_max 才入选，
    防止同族高相关因子刷屏。"""
    pivot = ev.pivot_table(index="date", columns="factor", values="ic")
    chosen: list = []
    for f in res["factor"]:
        if f not in pivot.columns:
            continue
        ok = True
        for g in chosen:
            a, b = pivot[f], pivot[g]
            joined = pd.concat([a, b], axis=1).dropna()
            if len(joined) > 30 and joined.corr().iloc[0, 1] > corr_max:
                ok = False
                break
        if ok:
            chosen.append(f)
        if len(chosen) >= max_keep:
            break
    return res[res["factor"].isin(chosen)]


def _backfill_stale(hyps: dict, ev_all: pd.DataFrame) -> int:
    """存量 A_/AI_ 条目（本轮未入选）也从逐日明细回填胜率。"""
    if ev_all is None or ev_all.empty:
        return 0
    n = 0
    for k, v in hyps.items():
        if not k.startswith(("A_", "AI_")) or v.get("win_rate") is not None:
            continue
        g = ev_all[ev_all["factor"] == k[2:]]
        if g.empty:
            continue
        inv = k.startswith("AI_")
        v["win_rate"] = round(float(g["bot_win" if inv else "top_win"].mean()), 3)
        v["avg_ret"] = round(float(g["bot_ret" if inv else "top_ret"].mean()), 3)
        n += 1
    return n


def register(res: pd.DataFrame, ev_all: pd.DataFrame | None = None) -> int:
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8")) if HYP_FILE.is_file() else {}
    registered = 0
    for _, r in res.iterrows():
        key = f"A_{r['factor']}"
        existed = key in hyps  # 重复运行：更新统计与状态，不重复注册
        want_bull = r["ic"] > 0  # IC>0：高因子值 → 次5日偏多
        pooled = decide_status(want_bull, r["diff_pp"] / 100, r["n"])
        status, wf_note = decide_status_wf(pooled, r["is_ic"], r["oos_ic"],
                                           1.0 if r["wf_ok"] else 0.0)
        sign = "偏多" if r["ic"] > 0 else "偏空"
        hyps[key] = {
            "name": f"{r['factor']}（Alpha 因子·次5日截面，IC {r['ic']:+.3f}/ICIR {r['icir']:.1f}）",
            "direction": f"因子高值次日5日{sign}",
            "win_rate": r["win_rate"], "avg_ret": r["avg_ret"],
            "vs_base_pp": r["diff_pp"], "n": r["n"],
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "status": status,
            "note": (wf_note or f"批量筛选注册：OOS IC {r['oos_ic']:+.4f} 与 IS 同向")
                    + "；事件化口径=每日因子 Top-5 次日5日胜率",
            "source": "factor_screen",
        }
        if not existed:
            registered += 1
    registered += _backfill_stale(hyps, ev_all)
    HYP_FILE.parent.mkdir(exist_ok=True)
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=1), encoding="utf-8")
    return registered


def register_inverse(res: pd.DataFrame, inv_top_k: int = 15,
                     bot_min_pp: float = 3.0,
                     ev_all: pd.DataFrame | None = None) -> int:
    """反向（反指）假设注册：AI_<factor>。

    用户口径："预测做多实际做空、跟着做一直亏"的因子 → 反着用才对。
    操作化：因子 **低值侧（Bottom-5）做多** 稳定跑赢基准（|bot_diff|≥3pp、
    OOS 同向、|IC|≥0.02）→ 注册为"低值→偏多（反向使用）"假设。
    （因子高值侧亏损 = IC<0 的偏空假设已在 A_ 里，两边互为镜像、样本不同。）"""
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8")) if HYP_FILE.is_file() else {}
    cand = res[(res["bot_diff_pp"].abs() >= bot_min_pp)
               & (res["ic"].abs() >= 0.02) & (res["bot_wf_ok"])]
    cand = cand.assign(_k=cand["bot_diff_pp"].abs()).sort_values("_k", ascending=False)
    registered = 0
    for _, r in cand.head(inv_top_k).iterrows():
        key = f"AI_{r['factor']}"
        existed = key in hyps
        want_bull = r["bot_diff_pp"] > 0  # 低值侧做多赚钱 → 偏多假设
        pooled = decide_status(want_bull, r["bot_diff_pp"] / 100, r["n"])
        status, wf_note = decide_status_wf(pooled, r["bot_diff_pp"], r["bot_diff_pp"],
                                           1.0 if r["bot_wf_ok"] else 0.0)
        side = "偏多" if r["bot_diff_pp"] > 0 else "偏空"
        hyps[key] = {
            "name": f"反向·{r['factor']}（低值侧{side}，bot_diff {r['bot_diff_pp']:+.1f}pp，"
                    f"IC {r['ic']:+.3f}）",
            "direction": f"因子低值→次日5日{side}（反向使用：按直觉做会亏，反着做）",
            "win_rate": r["bot_win_rate"], "avg_ret": r["bot_ret"],
            "vs_base_pp": r["bot_diff_pp"], "n": r["n"],
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "status": status,
            "note": (wf_note or "反向事件=每日因子 Bottom-5 的次5日胜率")
                    + "；直觉方向亏损成立（OOS 同向），反着用",
            "source": "factor_screen_inverse",
        }
        if not existed:
            registered += 1
    registered += _backfill_stale(hyps, ev_all)
    HYP_FILE.parent.mkdir(exist_ok=True)
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=1), encoding="utf-8")
    return registered


def main() -> int:
    ap = argparse.ArgumentParser(description="alpha_library 批量因子筛选 → 假设库注册")
    ap.add_argument("--symbols", type=int, default=60)
    ap.add_argument("--days", type=int, default=260)
    ap.add_argument("--wf-window", type=int, default=40)
    ap.add_argument("--ic-min", type=float, default=0.015)
    ap.add_argument("--icir-min", type=float, default=0.25)
    ap.add_argument("--top-k", type=int, default=50, help="正交去重后的注册上限")
    ap.add_argument("--dry", action="store_true", help="只出排名不注册")
    a = ap.parse_args()

    symbols = _pick_symbols(a.symbols, a.days)
    print(f"标的 {len(symbols)} 只 × {a.days} 交易日 × 全部因子…")
    df = load_panel(symbols, a.days)
    fam = {}
    for c in df.columns:
        if c in BASE_COLS or c == "fwd_ret_5":
            continue
        pre = c.split("_")[0] if c.startswith(("a158_", "a101_", "gtja_")) else \
            c.split("_")[0] if c.startswith(("micro_", "flow_", "vol_", "turn_", "mom_",
                                             "tech_", "chip_", "style_", "ind_", "concept_",
                                             "fun_", "amt_")) else "other"
        fam[pre] = fam.get(pre, 0) + 1
    print(f"面板 {len(df)} 行 · 因子 {sum(fam.values())} 个 · 族分布 {fam} · "
          f"标签覆盖 {df['fwd_ret_5'].notna().mean():.0%}")
    res, ev_all = screen(df, wf_window=a.wf_window, ic_min=a.ic_min,
                         icir_min=a.icir_min)
    if res.empty:
        print("无因子达到门槛（ic-min/icir-min 过严或样本异常）")
        return 0
    print(f"过门槛 {len(res)} 个；方向配额（偏多/偏空各半）正交去重后取 ≤{a.top_k} 个：")
    # 方向配额：偏多(IC>0)/偏空(IC<0)分池各半——否则强偏空因子会挤掉全部偏多名额
    half = max(a.top_k // 2, 1)
    pos = orthogonal_select(res[res["ic"] > 0], ev_all, max_keep=half)
    neg = orthogonal_select(res[res["ic"] <= 0], ev_all, max_keep=a.top_k - half)
    res = pd.concat([pos, neg]).sort_values("rank_key", ascending=False)
    print(res.to_string(index=False))
    out = ROOT / "logs" / "factor_screen"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{datetime.now():%Y%m%d-%H%M%S}.json").write_text(
        json.dumps(res.to_dict(orient="records"), ensure_ascii=False, indent=1),
        encoding="utf-8")
    if a.dry:
        print("dry-run：未注册假设库")
        return 0
    n = register(res, ev_all)
    n_inv = register_inverse(res, inv_top_k=a.top_k // 2, ev_all=ev_all)
    print(f"✅ 注册 {n} 条新假设 + {n_inv} 条反向假设（AI_*）→ configs/hypotheses.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
