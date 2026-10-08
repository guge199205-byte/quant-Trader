"""persona 字段可用性审计（scripts/persona_field_audit.py）。

背景：persona 广告给 agent 18 个字段，实测 5 个整列全 NULL
（industry/is_st/roe/stock_name/turnover_rate）——**广告字段查不到会悄悄削掉
agent 的能力**，它按提示去查 is_st（ST 股标志）拿回 NULL，无报错也无降级说明。

最要紧的是**抽取的完整性**：审计漏报比不报更糟（造成"都查过了"的假信心）。
本文件里两条回归都来自实现时的真实漏报。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_persona_field_audit.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import persona_field_audit as A  # noqa: E402


def _persona(tmp_path, text):
    f = tmp_path / "p.yml"
    f.write_text(text, encoding="utf-8")
    return [f]


def test_extracts_slash_field_list(tmp_path):
    fs = A.extract_persona_fields(_persona(tmp_path, "主表：close/volume/amount/pe_ttm\n"))
    assert {"close", "volume", "amount", "pe_ttm"} <= fs


def test_extracts_across_line_break(tmp_path):
    """★ 回归：persona 的字段清单跨行（`beta_20/` 结尾，下行接着写）。
    按行匹配会断在换行处漏字段——实现第一版就漏了。"""
    fs = A.extract_persona_fields(_persona(
        tmp_path, "（pe_ttm/pb/roe/total_mv/turnover_rate/pct_change/is_st/ma5-60/beta_20/\n"
                  "        industry/stock_name，symbol 前缀式如 SH603018）\n"))
    assert "industry" in fs and "stock_name" in fs


def test_ignores_range_tokens(tmp_path):
    """`ma5-60` 是区间不是列名（列是 ma5/ma10/…/ma60），不得当成列。"""
    fs = A.extract_persona_fields(_persona(tmp_path, "a/b/ma5-60/c\n"))
    assert "ma5-60" not in fs


def test_does_not_scan_plain_english(tmp_path):
    """回归：全文扫标识符会把 system/config/persona 也算成字段，报一堆假阳性。"""
    fs = A.extract_persona_fields(_persona(tmp_path, "The system config uses persona mode.\n"))
    assert fs == set()


def test_audit_flags_empty_and_missing_with_hints():
    rep = A.audit({"close": 5558, "roe": 0}, _persona(Path("/tmp"), "x/y/close/roe/nosuchcol\n"))

    assert "roe" in rep["empty_columns"]          # 整列 NULL
    assert "nosuchcol" in rep["missing_columns"]  # 列不存在
    assert "is_st" in A.KNOWN_ELSEWHERE           # 报出时带真实来源
