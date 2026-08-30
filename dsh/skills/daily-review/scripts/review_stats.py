"""每日复盘统计核心逻辑（纯函数，可单测，不碰 IO）。

为单一事实源，涨跌停/广度/分布/板块聚合等纯函数统一收归
backend/shared/market_breadth.py，本模块仅转引用，供 daily-review 脚本使用。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "backend" / "main_oss.py").is_file():
            return p
    raise FileNotFoundError("未找到仓库根（含 backend/main_oss.py）")


_REPO_ROOT = _find_repo_root(Path(__file__).resolve())
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from backend.shared.market_breadth import (  # noqa: E402, F401
    CAT_BROKE_UP,
    CAT_CORP_ACTION,
    CAT_DOWN,
    CAT_FLAT,
    CAT_LIMIT_DOWN,
    CAT_LIMIT_UP,
    CAT_NORMAL,
    CAT_UP,
    TOL_BJ,
    TOL_SHSZ,
    breadth_distribution,
    classify_by_pct,
    classify_price,
    compute_limits,
    fmt_yi,
    is_bse_symbol,
    is_corp_action_pct,
    is_ex_div,
    limit_pct,
    market_breadth,
    sector_aggregate,
    streak_from_tail,
    volume_ratio_5,
    wan_to_yi,
)
from backend.shared.stock_utils import StockCodeUtil  # noqa: E402


# ---------- 模型推理信号复盘（纯函数） ----------

def infer_signal_symbol(code: str) -> str:
    """推理信号 symbol（PG 纯数字如 300502 / 前缀 SH600036）→ suffix 600036.SH。"""
    return StockCodeUtil.to_suffix(str(code).strip())


def top_n_signals(rows: list[dict], n: int = 5, score_key: str = "fusion_score") -> list[dict]:
    """按 fusion_score 降序取 top-N 推理信号，symbol 统一归一化为 suffix。"""
    ordered = sorted(rows, key=lambda r: -float(r.get(score_key) or 0.0))
    return [
        {**r, "symbol": infer_signal_symbol(r["symbol"])}
        for r in ordered[:n]
    ]


def inference_hit_rate(
    signals: list[dict],
    pct_series: pd.Series,
    market_avg: float | None = None,
    category_map: dict[str, str] | None = None,
) -> dict:
    """昨日推理 top-N 信号 → 今日实际涨跌的命中率复盘。

    Args:
        signals: top-N 推理信号（已归一化 suffix symbol）
        pct_series: 今日全市场 pct_change（index=symbol）
        market_avg: 今日全市场平均涨幅（用于超额）
        category_map: symbol → 涨跌停分类（limit_up/limit_down）

    Returns:
        {n, avg_pct, up, down, missing, hit_rate, excess_pct, limit_up, limit_down}
        无有效票时 avg_pct/excess_pct=None，hit_rate=0。
    """
    vals = []
    up = down = limit_up = limit_down = 0
    for s in signals:
        sym = s["symbol"]
        if sym not in pct_series.index or pd.isna(pct_series[sym]):
            continue
        p = float(pct_series[sym])
        vals.append((sym, p))
        if p > 0:
            up += 1
        elif p < 0:
            down += 1
        cat = (category_map or {}).get(sym, "")
        if cat == CAT_LIMIT_UP:
            limit_up += 1
        elif cat == CAT_LIMIT_DOWN:
            limit_down += 1
    n = len(vals)
    if n == 0:
        return {"n": 0, "avg_pct": None, "up": 0, "down": 0,
                "missing": len(signals), "hit_rate": 0.0,
                "excess_pct": None, "limit_up": 0, "limit_down": 0}
    avg = sum(v for _, v in vals) / n
    return {
        "n": n,
        "avg_pct": round(avg, 3),
        "up": up,
        "down": down,
        "missing": len(signals) - n,
        "hit_rate": round(up / n, 3),
        "excess_pct": round(avg - market_avg, 3) if market_avg is not None else None,
        "limit_up": limit_up,
        "limit_down": limit_down,
    }
