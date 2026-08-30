#!/usr/bin/env python3
"""
获取分红派息

功能：获取股票的分红派息记录
用法：python get_corporate_actions_dividends.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- data[]:        分红派息列表，按公告日倒序，每项含 pub_date/statement/process（事件进展，仅港股/A股正股与信托有值）/record_date（股权登记日，ETF无此数据）/ex_date/dividend_payable_date/fiscal_year（财政年度，仅ETF）
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
)

SEP = "=" * 64
SEP2 = "-" * 64


def _opt(val):
    if val is None or val == "" or val == "--" or (isinstance(val, float) and val != val):
        return "-"
    return str(val)


def _dw(s):
    """计算字符串在终端的显示宽度（CJK 字符按 2 计）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)


def _trunc(s, max_chars):
    """按字符数截断，超出时末尾加省略号。"""
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 1] + "…"


def _print_left_table(columns, rows):
    """CJK-aware 左对齐表格输出，columns 为列名列表，rows 为 list[dict]。"""
    widths = {col: _dw(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], _dw(row.get(col, "-")))
    header = "  ".join(s + " " * max(0, widths[col] - _dw(s)) for col, s in
                       ((col, col) for col in columns))
    print(header)
    for row in rows:
        cells = []
        for col in columns:
            s = row.get(col, "-")
            cells.append(s + " " * max(0, widths[col] - _dw(s)))
        print("  ".join(cells))


def _build_display_rows(items):
    candidate_cols = ["公告日", "分配方案", "事件进展", "股权登记日", "除权除息日", "派息日", "财政年度"]
    rows = []
    for item in items:
        row = {
            "公告日":    _opt(item.get("pub_date")),
            "分配方案":  _trunc(_opt(item.get("statement", "")).strip() or "-", 50),
            "事件进展":  _opt(item.get("process")),
            "股权登记日": _opt(item.get("record_date")),
            "除权除息日": _opt(item.get("ex_date")),
            "派息日":    _opt(item.get("dividend_payable_date")),
            "财政年度":  _opt(item.get("fiscal_year")),
        }
        rows.append(row)
    cols = [c for c in candidate_cols if any(r.get(c, "-") != "-" for r in rows)]
    return cols, rows


def get_corporate_actions_dividends(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_corporate_actions_dividends(code)
        check_ret(ret, data, ctx, "获取分红派息")

        dividend_list = data.get("dividend_list", []) if isinstance(data, dict) else None
        no_data = is_empty(data) or not dividend_list
        if no_data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": dividend_list}, ensure_ascii=False))
        else:
            print(SEP)
            print(f"分红派息  标的：{code}")
            print(SEP2)
            cols, rows = _build_display_rows(dividend_list)
            _print_left_table(cols, rows)
            print(SEP2)
            print(f"返回条数：{len(dividend_list)}")
            print(SEP)

    except SystemExit:
        raise
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="获取股票分红派息历史",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "code",
        help="股票代码，如 HK.00700",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()
    get_corporate_actions_dividends(args.code, output_json=args.output_json)
