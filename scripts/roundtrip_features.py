#!/usr/bin/env python3
"""回合台账 · 价格类特征附件（离线 / 可重跑 / 幂等）。

方案见 docs/ROUNDTRIP_FEATURES_PLAN.md。要点：

  1. **点位时间纪律（最关键）**：买入发生在盘中（如 09:37），那一刻能看到的日线上限是
     **买入日前一交易日收盘**。本脚本一律只取 `dt < 买入日` 的收盘价——
     买入当日及其后**绝不进入特征**。这是防未来信息的唯一防线，且有专门的回归测试。
  2. **用后复权**（daily_backward）：前复权会在分红送股时**回溯改写历史值**，
     用它算历史 RSI 等于让未来信息污染过去；后复权只改未来，历史值固定。
  3. **事实与派生分离**：不修改 logs/live_roundtrips.jsonl，特征单独落
     logs/roundtrip_features.jsonl（按自然键关联）。
  4. **缺数据写占位**（status=insufficient_data），不静默跳过——
     否则"没算"会伪装成"没有这个回合"。
  5. **不降级到桥**：一旦静默走桥，离线数据源缺数就永远发现不了。

用法：
    python scripts/roundtrip_features.py                # 全量重算（幂等）
    python scripts/roundtrip_features.py --stats        # 只看统计不写盘
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
ROUNDTRIP_LOG = ROOT / "logs" / "live_roundtrips.jsonl"
FEATURE_LOG = ROOT / "logs" / "roundtrip_features.jsonl"

#: quantdb 后复权日线（Hive 分区 dt=YYYYMMDD）。见 quantdb_hub 的视图定义。
KLINE_GLOB = (
    "/home/zbox/projects/quantmind/data/quantdb/1_kline_data/daily_backward/dt=*/*.parquet"
)

BJ = timezone(timedelta(hours=8))


# ---------- 特征定义表：唯一真相源 ----------
# 新增特征只需加一条，不必改调用方、不必改落盘格式。
# 每项声明：需要的最少 K 线根数 + 计算函数（入参 bars 为**升序**收盘价，
# 最后一根是买入日的前一交易日）。
def _rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI（A 股常用口径）。需要 period+1 根收盘价。"""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return round(100 - 100 / (1 + avg_g / avg_l), 3)


def _prior_5d_return(closes: list[float]) -> float | None:
    """买入日前 5 个交易日累计涨幅（%）。需要 6 根收盘价。"""
    if len(closes) < 6 or closes[-6] <= 0:
        return None
    return round((closes[-1] / closes[-6] - 1) * 100, 3)


#: name -> (最少 K 线根数, 计算函数)
FEATURES: dict[str, tuple[int, Callable[[list[float]], float | None]]] = {
    "entry_rsi14": (15, _rsi_wilder),      # Wilder RSI 需 period+1 = 15 根
    "prior_5d_return": (6, _prior_5d_return),
}


# ---------- 自然键 ----------
def feature_id(rt: dict) -> str:
    """回合的自然键（回合台账本身没有 id）。

    用业务键而非行号：文件是追加写的，行号不稳定；自然键在重算时仍能对上。
    """
    raw = "|".join(str(rt.get(k) or "") for k in
                   ("agent", "code", "buy_ts", "sell_ts", "volume"))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _buy_date(rt: dict) -> str | None:
    """买入日（北京）YYYYMMDD；解析失败返回 None。"""
    try:
        return datetime.fromisoformat(str(rt.get("buy_ts") or "")).astimezone(BJ).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return None


# ---------- 数据访问 ----------
def load_closes(symbol: str, before_dt: str, n: int) -> list[float]:
    """取 symbol 在 before_dt（YYYYMMDD，不含）之前、最近的 n 根后复权收盘价（升序）。

    严格 `dt < before_dt` —— 见模块头的点位时间纪律。
    """
    import duckdb

    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT dt, close FROM read_parquet(?, hive_partitioning=1, union_by_name=true) "
            "WHERE symbol = ? AND dt < ? AND close IS NOT NULL "
            "ORDER BY dt DESC LIMIT ?",
            [KLINE_GLOB, symbol, before_dt, n],
        ).fetchall()
    finally:
        con.close()
    return [float(c) for _dt, c in reversed(rows)]


# ---------- 计算 ----------
def compute_features(rt: dict) -> dict:
    """一个回合 → 一条特征记录（含占位）。"""
    fid = feature_id(rt)
    base = {"feature_id": fid, "agent": rt.get("agent"), "code": rt.get("code"),
            "buy_ts": rt.get("buy_ts"), "sell_ts": rt.get("sell_ts")}

    bd = _buy_date(rt)
    if bd is None:
        return {**base, "status": "insufficient_data", "reason": "buy_ts 不可解析"}

    need = max(n for n, _fn in FEATURES.values())
    try:
        closes = load_closes(str(rt.get("code") or ""), bd, need)
    except Exception as exc:  # noqa: BLE001 —— 读失败按缺数据记账，不整体崩
        return {**base, "status": "insufficient_data", "reason": f"读行情失败: {str(exc)[:80]}"}

    if not closes:
        return {**base, "status": "insufficient_data",
                "reason": f"买入日 {bd} 之前无后复权行情（停牌/新股/代码口径不符）"}

    out: dict = {}
    for name, (n_bars, fn) in FEATURES.items():
        out[name] = fn(closes) if len(closes) >= n_bars else None

    got = [v for v in out.values() if v is not None]
    if not got:
        return {**base, "status": "insufficient_data",
                "reason": f"仅 {len(closes)} 根 K 线，不足任一特征所需"}

    return {**base, "status": "ok", **out, "window_days": len(closes)}


# ---------- 主流程 ----------
def load_roundtrips() -> list[dict]:
    if not ROUNDTRIP_LOG.is_file():
        return []
    rows = []
    for line in ROUNDTRIP_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write_features(records: list[dict]) -> None:
    """整文件重写，按 feature_id 排序 —— 幂等且逐字节可复现。"""
    FEATURE_LOG.parent.mkdir(exist_ok=True)
    tmp = FEATURE_LOG.with_name(FEATURE_LOG.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in sorted(records, key=lambda x: x["feature_id"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(FEATURE_LOG)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="回合台账价格类特征附件（离线可重跑）")
    ap.add_argument("--stats", action="store_true", help="只统计不写盘")
    args = ap.parse_args(argv)

    rts = load_roundtrips()
    if not rts:
        print("回合台账为空（logs/live_roundtrips.jsonl）——先积累已平仓回合")
        return 0

    records = [compute_features(rt) for rt in rts]
    ok = sum(1 for r in records if r["status"] == "ok")
    short = len(records) - ok
    print(f"回合 {len(records)} 笔：特征就绪 {ok}，数据不足 {short}")
    if short:
        for r in records[:5]:
            if r["status"] != "ok":
                print(f"  · {r['code']} {r.get('buy_ts')}: {r.get('reason')}")

    if not args.stats:
        write_features(records)
        print(f"已写入 {FEATURE_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
