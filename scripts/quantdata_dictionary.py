#!/usr/bin/env python3
"""QuantData 家族数据字典生成器（quantus / quanthk / quantfutures / quantbc）。

为什么需要：agent 直接查这些数据集，但**字段单位无从得知**。
实测（2026-09-13）：quantdb 的 `volume` 实际是**股**、`amount` 是**万元**，
而 PG 表注释写的是「手」和「元」——**差 100× 与 10000×**，而注释就在库里、
任何人查表结构都会信它。

三级单位来源，**必须标清楚是哪一级**（字典的价值全在可信度分级）：
  * `official`  —— 官方数据字典（quantdb.cn/docs/fields.html）明写
  * `verified`  —— 用实际数据反算验证过（如 amount×10000/volume ≈ close）
  * `inferred`  —— 仅按字段名推断，**未经证实，用前需自行验算**

quantdb 有官方字典 → `official`；**quantus / quanthk 没有官方字典**，
但其 K 线核心字段（amount/volume）已用实际数据反算证实 → `verified`；
其余多数列只能 `inferred`，**不要把推断当事实用**。

用法：
    python scripts/quantdata_dictionary.py --root <数据根> --market us \
        --out configs/quantdata_fields_us.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: ⚠️ **按市场分化的口径** —— 实测（2026-09-13，用 `amount/volume` 反算对照 `close`）：
#:
#:   A 股 quantdb:  amount 是【万元】   （amount×10000/volume ≈ close ✓）
#:   美股 quantus:  amount 是【原币】   （amount/volume ≈ close ✓，ABNB 167.65=167.65）
#:   港股 quanthk:  amount 是【原币】   （amount/volume ≈ close ✓，0696.HK 8.667≈8.67）
#:
#: **三代数据口径不同，不要把 A 股的约定套到美/港股上（差 10000 倍）。**
#: 本生成器第一版就犯了这个错——给美股标了「万元 [official]」，
#: 而那份"官方字典"是 A 股 quantdb 的。**跨市场套用约定 = 把推断伪装成事实。**
AMOUNT_UNIT: dict[str, tuple[str, str]] = {
    "cn": ("万元", "official"),
    "us": ("原币（美元）", "verified"),
    "hk": ("原币（港元）", "verified"),
}
#: volume 三市场均为【股】（均由 amount/volume ≈ close 反算证实）。
#: 「1 手 = 100 股」是 **A 股专属**约定，美/港股无"手"——故只对 cn 标注。
VOLUME_UNIT: dict[str, tuple[str, str]] = {
    "cn": ("股（1 手已统一为 100 股）", "official"),
    "us": ("股", "verified"),
    "hk": ("股", "verified"),
}


#: 与市场无关的字段名 → (单位, 来源)。
UNIT_RULES: list[tuple[str, str, str]] = [
    # —— official：官方字典明写（价格单位见官方「单位归一化」表）——
    (r"^(open|high|low|close|pre_close|settle|last|price)$", "报价币种", "official"),
    (r"^(total_mv|float_mv|market_cap|circ_mv)$", "报价币种", "official"),
    (r"^(dt|date|trade_date|.*_date|.*_dt)$", "日期", "official"),
    (r"^(symbol|code|ticker|ts_code)$", "代码", "official"),
    # —— inferred：仅按命名推断，**未经证实** ——
    (r".*(_pct|pct|percent|rate)$", "%", "inferred"),
    (r"^(pe|pb|ps|peg|beta|vol_std|vol_atr|sharpe|alpha|beta_\d+).*$", "倍/无量纲", "inferred"),
    (r"^(roe|roa|roic|gross_margin|net_margin).*$", "%", "inferred"),
    (r"^(ma|ema|boll|macd|rsi|kdj|atr|cci).*\d*$", "报价币种", "inferred"),
    (r".*(turnover|holding|hold_?ratio).*$", "%", "inferred"),
    (r".*(count|num|n_|qty|volume_).*$", "个/股", "inferred"),
]


def guess_unit(field: str, market: str = "cn") -> tuple[str | None, str | None]:
    """→ (单位, 来源)；无匹配返回 (None, None)，不臆造。

    amount / volume 走**按市场分化**的口径表（见模块头实测依据），
    不套用 A 股约定。
    """
    name = field.lower()
    if name == "amount":
        return AMOUNT_UNIT.get(market, (None, None))
    if name == "volume":
        return VOLUME_UNIT.get(market, (None, None))
    for pat, unit, src in UNIT_RULES:
        if re.match(pat, name):
            return unit, src
    return None, None


def _sample_columns(path: Path, market: str = "cn", n: int = 3) -> tuple[list[dict], int]:
    """读一个 parquet 的列 + 样例值。"""
    import pandas as pd

    df = pd.read_parquet(path).head(n)
    cols = []
    for c in df.columns:
        vals = df[c].dropna().head(2).tolist()
        unit, src = guess_unit(str(c), market)
        cols.append({
            "name": str(c),
            "dtype": str(df[c].dtype),
            "sample": [str(v) for v in vals],
            "unit": unit,
            "unit_source": src,
        })
    return cols, len(df.columns)


def _pick_sample(ds_dir: Path) -> Path | None:
    """优先取最新分区，退而取任意 parquet。"""
    parts = sorted(ds_dir.glob("dt=*/*.parquet"))
    if parts:
        return parts[-1]
    flat = sorted(ds_dir.glob("*.parquet"))
    if flat:
        return flat[0]
    nested = sorted(ds_dir.glob("*/*.parquet"))[:1]
    return nested[0] if nested else None


def build(root: Path, market: str) -> dict:
    datasets = []
    for group in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        for ds in sorted(p for p in group.iterdir() if p.is_dir()):
            sample = _pick_sample(ds)
            if sample is None:
                continue
            try:
                cols, n_col = _sample_columns(sample, market)
            except Exception as exc:  # noqa: BLE001 —— 单个数据集读不了不许拖垮整份字典
                datasets.append({"path": f"{group.name}/{ds.name}", "error": str(exc)[:120]})
                continue
            parts = list(ds.glob("dt=*"))
            datasets.append({
                "path": f"{group.name}/{ds.name}",
                "columns_n": n_col,
                "partitions": len(parts) if parts else None,
                "sample_file": str(sample.relative_to(root)),
                "columns": cols,
            })
    covered = sum(1 for d in datasets if "columns" in d)
    inferred = sum(1 for d in datasets if "columns" in d
                   for c in d["columns"] if c.get("unit_source") == "inferred")
    unknown = sum(1 for d in datasets if "columns" in d
                  for c in d["columns"] if c.get("unit") is None)
    return {
        "market": market,
        "root": str(root),
        "datasets_n": covered,
        "summary": {"columns_inferred": inferred, "columns_unknown": unknown,
                    "note": "unit_source 三级：official/verified 可采信，"
                            "inferred 仅按命名推断**未经证实**，unknown 未给单位。"},
        "datasets": datasets,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="QuantData 数据字典生成器")
    ap.add_argument("--root", required=True)
    ap.add_argument("--market", required=True, help="us / hk / futures / bc")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    root = Path(a.root)
    if not root.is_dir():
        print(f"❌ 数据根不存在: {root}", file=sys.stderr)
        return 2

    doc = build(root, a.market)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(out)

    s = doc["summary"]
    print(f"✅ {a.market}: {doc['datasets_n']} 个数据集 → {out}")
    print(f"   单位：推断 {s['columns_inferred']} 列、未给 {s['columns_unknown']} 列"
          f"（inferred 未经证实，用前需自行验算）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
