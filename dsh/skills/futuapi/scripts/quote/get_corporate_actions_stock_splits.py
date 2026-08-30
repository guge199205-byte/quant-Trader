#!/usr/bin/env python3
"""
获取拆合股

功能：获取股票的拆合股历史记录（港股有额外字段），支持分页
用法：python get_corporate_actions_stock_splits.py [-h] [--next-key KEY] [--num N] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.next_key:   分页标识，"-1" 表示无更多数据
- data.items[]:    拆合股列表，每项含 dir_deci_pub_date_str/reform_type/rate；港股正股与信托额外含 ex_date_str/sm_deci_date_str/temp_trade_begin_date_str/simul_trade_begin_date_str/simul_trade_end_date_str/event_status/new_par_value/temp_share_code/temp_share_abbr_name/new_trade_unit/shares_after_effect 等
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
)

def _reform_type_str(v):
    return str(v) if v else "-"


def _par_value_str(v):
    """新面值（实际值）"""
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f == 0:
        return "-"
    return f"{f:.6f}"


def _shares_after_str(v):
    from common import format_big_number
    return format_big_number(v)


# ─────────────────────────────────────────────────────────────
# 非 JSON 展示
# ─────────────────────────────────────────────────────────────

def _print_header(code):
    print("=" * 64)
    print(f"拆合股  标的：{code}")
    print("-" * 64)


def _print_footer(count, nk):
    print("-" * 64)
    nk_disp = f"已结束(-1)" if nk == "-1" else nk
    print(f"返回条数：{count}   --next-key：{nk_disp}")
    print("=" * 64)


def _col_widths(rows, headers):
    """计算每列的最大显示宽度（支持中英文混排）。"""
    from common import disp_width
    widths = [disp_width(h) for h in headers]
    for row in rows:
        for i, h in enumerate(headers):
            widths[i] = max(widths[i], disp_width(str(row.get(h, "-"))))
    return widths


def _print_table(rows, headers, is_hk):
    """打印格式化表格。"""
    from common import disp_width, pad_disp

    if not rows:
        print("  暂无数据")
        return

    # 候选列（顺序）
    common_cols = ["日期", "方式", "比率"]
    hk_cols = ["除权日", "决议日", "临时买卖日", "并行买卖日", "事件进程",
               "新面值", "临时证券代码", "临时证券简称", "新买卖单位", "生效后股数"]
    candidate_cols = common_cols + (hk_cols if is_hk else [])

    # 构建展示行
    disp_rows = []
    for item in rows:
        begin = item.get("simul_trade_begin_date_str") or ""
        end   = item.get("simul_trade_end_date_str") or ""
        simul = f"{begin}~{end}" if (begin or end) else "-"
        row = {
            "日期":       item.get("dir_deci_pub_date_str") or "-",
            "方式":       _reform_type_str(item.get("reform_type")),
            "比率":       item.get("rate") or "-",
        }
        if is_hk:
            row.update({
                "除权日":        item.get("ex_date_str") or "-",
                "决议日":        item.get("sm_deci_date_str") or "-",
                "临时买卖日":    item.get("temp_trade_begin_date_str") or "-",
                "并行买卖日":    simul,
                "事件进程":      item.get("event_status") or "-",
                "新面值":        _par_value_str(item.get("new_par_value")),
                "临时证券代码":  item.get("temp_share_code") or "-",
                "临时证券简称":  item.get("temp_share_abbr_name") or "-",
                "新买卖单位":    str(item.get("new_trade_unit") or "-"),
                "生效后股数":    _shares_after_str(item.get("shares_after_effect")),
            })
        disp_rows.append(row)

    # 过滤掉所有行都是 "-" 的列
    cols = [c for c in candidate_cols if any(str(r.get(c, "-")) != "-" for r in disp_rows)]

    widths = _col_widths(disp_rows, cols)
    sep = "  "
    header_line = sep.join(pad_disp(h, w) for h, w in zip(cols, widths))
    print(header_line)
    print("-" * min(sum(widths) + len(sep) * (len(cols) - 1), 120))
    for row in disp_rows:
        line = sep.join(pad_disp(str(row.get(c, "-")), w) for c, w in zip(cols, widths))
        print(line)


def display_non_json(code, next_key, num):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_corporate_actions_stock_splits(code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取拆合股")
        items = [] if is_empty(data) else data.get("split_list", [])
        if not items:
            print("无数据")
            return
        nk = data.get("next_key", "-1")
        is_hk = code.upper().startswith("HK.")
        _print_header(code)
        _print_table(items, None, is_hk)
        _print_footer(len(items), nk)
    finally:
        safe_close(ctx)


# ─────────────────────────────────────────────────────────────
# JSON 展示
# ─────────────────────────────────────────────────────────────

def display_json(code, next_key, num):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_corporate_actions_stock_splits(code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取拆合股")
        items = [] if is_empty(data) else data.get("split_list", [])
        if not items:
            print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            return
        nk = data.get("next_key", "-1")
        print(json.dumps({"code": code, "data": {"next_key": nk, "items": items}}, ensure_ascii=False))
    finally:
        safe_close(ctx)


# ─────────────────────────────────────────────────────────────
# argparse 入口
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="获取拆合股事件，支持分页拉取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "code",
        help="股票代码，如 HK.00700",
    )
    parser.add_argument(
        "--next-key",
        default=None,
        dest="next_key",
        metavar="KEY",
        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据",
    )
    parser.add_argument(
        "--num",
        type=int,
        default=None,
        metavar="N",
        help="每页返回数量，默认 10，范围 1~50",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()

    if args.output_json:
        display_json(args.code, args.next_key, args.num)
    else:
        display_non_json(args.code, args.next_key, args.num)


if __name__ == "__main__":
    main()
