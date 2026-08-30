#!/usr/bin/env python3
"""
获取持股变动

功能：获取股票持股人的变动记录（增持/减持/新进/清仓等）

用法：python get_shareholders_holding_changes.py [-h] [--next-key NEXT_KEY] [--num NUM] [--sort-type SORT_TYPE] [--sort-column SORT_COLUMN] [--filter-type FILTER_TYPE] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金
- 支持分页，默认每页 10 条，最多 50 条

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-type: 排序方向：1=降序（默认）2=升序
- --sort-column: 排序字段：62=持股变动数（默认）63=持股日期 64=变动比例 65=变动金额 66=持股比例
- --filter-type: 筛选类型：0=全部（默认）1=增持 2=减持 3=建仓 4=清仓

返回字段说明：
- data.next_key:  分页标识，"-1" 表示无更多数据
- data.items[]:   持股变动记录列表，每项含 period_text/name/holder_id/holder_type/holder_type_id/holding_date_str/share_change_num/shares_change_price/share_ratio/share_ratio_change/share_num 等
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
    print_display_df,
    format_big_number,
)

import pandas as pd

SEP64 = "=" * 64
DASH64 = "-" * 64


def _fmt_pct(val):
    try:
        return f"{float(val):.2f}%"
    except (TypeError, ValueError):
        return str(val) if val is not None else "-"


def _build_display_df(data):
    rows = []
    for _, row in data.iterrows():
        period_text = str(row.get("period_text") or "") or "-"
        name = str(row.get("name") or "") or "-"
        holder_id = row.get("holder_id")
        share_change_num = row.get("share_change_num")
        shares_change_price = row.get("shares_change_price")
        share_ratio = row.get("share_ratio")
        holder_type = str(row.get("holder_type") or "") or "-"
        holder_type_id = row.get("holder_type_id")
        holding_date_str = str(row.get("holding_date_str") or "") or "-"
        share_ratio_change = row.get("share_ratio_change")
        share_num = row.get("share_num")

        rows.append({
            "报告期": period_text,
            "股东名称": name,
            "股东ID": str(holder_id) if holder_id is not None else "-",
            "持股变动(股)": format_big_number(share_change_num) if share_change_num is not None else "-",
            "参考变动金额": format_big_number(shares_change_price) if shares_change_price is not None else "-",
            "持股比例(%)": _fmt_pct(share_ratio) if share_ratio is not None else "-",
            "持股性质": f"{holder_type}({holder_type_id})" if holder_type_id is not None else holder_type,
            "报告日期": holding_date_str,
            "变动比例(%)": _fmt_pct(share_ratio_change) if share_ratio_change is not None else "-",
            "持股数(股)": format_big_number(share_num) if share_num is not None else "-",
        })
    return pd.DataFrame(rows)


def get_shareholders_holding_changes(code, next_key=None, num=None, sort_type=None,
                                     sort_column=None, filter_type=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_shareholders_holding_changes(
            code, next_key=next_key, num=num,
            sort_type=sort_type, sort_column=sort_column, filter_type=filter_type
        )
        check_ret(ret, data, ctx, "获取持股变动")

        page_next_key = "-1"
        if not is_empty(data):
            page_next_key = str(data.iloc[0].get("next_key", -1)) if len(data) > 0 else "-1"

        next_key_display = "已结束(-1)" if page_next_key == "-1" else page_next_key
        row_count = len(data) if not is_empty(data) else 0

        if output_json:
            if is_empty(data):
                records = []
            else:
                records = df_to_records(data)
                for r in records:
                    r.pop("next_key", None)
            payload = {"code": code, "data": {"next_key": page_next_key, "items": records}}
            print(json.dumps(payload, ensure_ascii=False))
        else:
            if is_empty(data):
                print("无数据")
            else:
                print(SEP64)
                print(f"持股变动  标的：{code}")
                print(DASH64)
                print_display_df(_build_display_df(data), max_colwidth=30)
                print(DASH64)
                print(f"返回条数：{row_count}   --next-key：{next_key_display}")
                print(SEP64)

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
    parser = argparse.ArgumentParser(description="获取股东持股变动列表，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--sort-type", type=int, default=None, dest="sort_type",
                        help="排序方向：1=降序（默认）2=升序")
    parser.add_argument("--sort-column", type=int, default=None, dest="sort_column",
                        help="排序字段（Qot_Common.SortField）：62=持股变动数（默认）63=持股日期 64=变动比例 65=变动金额 66=持股比例")
    parser.add_argument("--filter-type", type=int, default=None, dest="filter_type",
                        help="筛选类型：0=全部（默认）1=增持 2=减持 3=建仓 4=清仓")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_shareholders_holding_changes(
        args.code,
        next_key=args.next_key,
        num=args.num,
        sort_type=args.sort_type,
        sort_column=args.sort_column,
        filter_type=args.filter_type,
        output_json=args.output_json,
    )
