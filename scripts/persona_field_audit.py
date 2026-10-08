#!/usr/bin/env python3
"""人设字段可用性审计：persona 广告给 agent 的字段，真能查到吗？

为什么需要（2026-09-13 实measured）：persona 里写着

    主表 stock_daily_latest：全 A 日线+估值+均线+行业
    （trade_date/open/high/low/close/volume/amount/pe_ttm/pb/roe/total_mv/
     turnover_rate/pct_change/is_st/ma5-60/beta_20/industry/stock_name …）

而实测 22 个广告字段里 **6 个不可用**：

    roe · turnover_rate · is_st · industry · stock_name   ← 整列全 NULL
    ma30                                                  ← 列不存在

**这比"注释写错单位"严重**：注释错了只是误导人，而**广告字段查不到会直接
把 agent 的能力悄悄削掉**——它按提示去查 `is_st`（ST 股标志），拿回 NULL，
既没有报错、也没有降级说明，只能自行发挥。

本脚本把这条约束**代码化**：persona 里出现的字段名，必须在数据源里真的可查。
与 test_config_integrity 同一思路——**约定要能自动校验，不能靠人记得**。

用法：
    python scripts/persona_field_audit.py            # 审计 persona 中的字段
    python scripts/persona_field_audit.py --json     # 输出 JSON
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONA_FILES = [
    ROOT / "dsh" / "baymax.trading.cordis.yml",
    ROOT / "dsh" / "baymax.cordis.yml",
]

#: 已知可用但**不在** stock_daily_latest 的字段 → 真实来源。
#: 用途：审计报出"不可用"时，直接告诉人该去哪找，而不是只说"坏了"。
KNOWN_ELSEWHERE = {
    "stock_name": "2_base_sector/instrument_detail.Name",
    "is_st": "2_base_sector/instrument_detail.IsSTGP",
    "industry": "2_base_sector/instrument_detail.tdx_dyname / rs_hyname",
    "roe": "3_financial_data/income（待核）",
    "turnover_rate": "5_technical_derived（待核）",
}


def extract_persona_fields(files: list[Path] | None = None) -> set[str]:
    """从 persona 文件里抽出字段名。

    **只取「斜杠分隔的字段清单」里的词**（如 `pe_ttm/pb/roe/total_mv/...`）——
    全文扫标识符会把 system/config/persona 这类英文词一并算成"字段"，
    报出一堆假阳性，那比不报还糟（今天已反复栽在"未验证就下结论"上）。
    """
    fields: set[str] = set()
    for f in (files or PERSONA_FILES):
        if not f.is_file():
            continue
        # ⚠️ 先合并换行：persona 的字段清单是**跨行**的（第 31 行以 `/` 结尾，
        #    下一行接着写 industry/stock_name）。按行匹配会断在换行处漏掉字段——
        #    而审计漏报比不报更糟（会造成"都查过了"的假信心）。
        # 合并换行 **并归一化斜杠两侧空白**：续行有缩进，`beta_20/` 与下一行的
        # `industry` 之间隔着空格，只合并换行仍会断在空白处。
        text = re.sub(r"\s*/\s*", "/", f.read_text(encoding="utf-8"))
        for run in re.findall(r"[a-z][a-z0-9_]{1,}(?:/[a-z][a-z0-9_]{1,}){2,}", text):
            for tok in run.split("/"):
                # `ma5-60` 这类是区间不是列名（列是 ma5/ma10/…/ma60）→ 跳过，不当成列
                if "-" in tok:
                    continue
                fields.add(tok)
    return fields


def audit(columns: dict[str, int], files: list[Path] | None = None) -> dict:
    """columns: {列名: 非空行数}（0 表示整列空）。"""
    advertised = extract_persona_fields(files)
    usable = {c for c, n in columns.items() if n > 0}
    missing_col = sorted(c for c in advertised if c not in columns)
    empty_col = sorted(c for c in advertised if columns.get(c, 1) == 0)
    bad = sorted(set(missing_col) | set(empty_col))
    return {
        "advertised_n": len(advertised),
        "empty_columns": empty_col,
        "missing_columns": missing_col,
        "unusable": bad,
        "hints": {c: KNOWN_ELSEWHERE[c] for c in bad if c in KNOWN_ELSEWHERE},
    }


def _probe_columns() -> dict[str, int]:
    """连 quantdb 读 stock_daily_latest 的列 + 最新日非空计数。"""
    import os

    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("QM_PG_HOST", "172.17.0.1"),
        port=int(os.getenv("QM_PG_PORT", "5432")),
        dbname=os.getenv("QM_PG_DB", "quantdb"),
        user=os.getenv("QM_PG_USER"), password=os.getenv("QM_PG_PASSWORD"))
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM stock_daily_latest")
    d = cur.fetchone()[0]
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='stock_daily_latest'""")
    cols = [r[0] for r in cur.fetchall()]
    out = {}
    for c in cols:
        try:
            cur.execute(f"SELECT COUNT({c}) FROM stock_daily_latest WHERE trade_date=%s", (d,))
            out[c] = cur.fetchone()[0]
        except Exception:  # noqa: BLE001
            conn.rollback()
    cur.close()
    conn.close()
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="persona 字段可用性审计")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--persona", action="append", default=[],
                    help="persona 文件路径（可重复；默认取仓库内两份）")
    a = ap.parse_args(argv)

    cols = _probe_columns()
    if a.persona:
        files = [Path(x) for x in a.persona]
    else:
        files = [f for f in PERSONA_FILES if f.is_file()]
    rep = audit(cols, files)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0

    print(f"persona 广告的字段标识符：{rep['advertised_n']} 个（含无关词，只看下面命中的）")
    if not rep["unusable"]:
        print("✅ 广告字段全部可查")
        return 0
    print(f"⚠️ 不可用 {len(rep['unusable'])} 个：")
    for c in rep["empty_columns"]:
        print(f"   {c:16s} 整列全 NULL")
    for c in rep["missing_columns"]:
        print(f"   {c:16s} 列不存在")
    if rep["hints"]:
        print("\n   真实来源（已知的）：")
        for c, where in rep["hints"].items():
            print(f"   {c:16s} → {where}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
