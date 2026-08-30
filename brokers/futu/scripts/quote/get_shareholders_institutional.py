#!/usr/bin/env python3
"""
获取机构持股

功能：获取股票的机构持股人数及持股量历史（支持分页）
用法：python get_shareholders_institutional.py [-h] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.update_time / data.update_time_str: 数据更新时间戳及日期（YYYY-MM-DD HH:MM:SS）
- data.next_key:   分页标识，"-1" 表示无更多数据
- data.items[]:    机构持股列表，每项含 period_text/institution_quantity/institution_quantity_change/holder_quantity/holder_quantity_change/holder_pct/holder_pct_change 等
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
    safe_int,
    format_big_number,
)

import pandas as pd


SEP64 = "=" * 64
DIV64 = "-" * 64


def _fmt_pct(val):
    if val is None:
        return "-"
    try:
        return f"{float(val):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _build_institution_display_df(items):
    rows = []
    for _, row in items.iterrows():
        rows.append({
            "报告期": str(row.get("period_text") or "") or "-",
            "机构数(家)": safe_int(row.get("institution_quantity")),
            "机构变动(家)": safe_int(row.get("institution_quantity_change")),
            "持股量(股)": format_big_number(safe_int(row.get("holder_quantity"))),
            "持股变动(股)": format_big_number(safe_int(row.get("holder_quantity_change"))),
            "持股比例(%)": _fmt_pct(row.get("holder_pct")),
            "变动比例(%)": _fmt_pct(row.get("holder_pct_change")),
        })
    return pd.DataFrame(rows)


def get_shareholders_institutional(code, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_shareholders_institutional(code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取机构持股")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": {"next_key": "-1",
                                   "update_time": 0, "update_time_str": "", "items": []}},
                                  ensure_ascii=False))
            else:
                print("无数据")
            return

        # 提取外层 S2C 字段（每行相同，取第一行）
        first = data.iloc[0]
        next_key_out = str(first.get("next_key") or "-1")
        _ut = first.get("update_time")
        update_time = int(_ut) if _ut is not None and not (isinstance(_ut, float) and pd.isna(_ut)) else 0
        update_time_str = str(first.get("update_time_str") or "")

        # 仅保留 item 级别字段
        item_cols = [c for c in data.columns if c not in ("next_key", "update_time", "update_time_str")]
        items = data[item_cols]
        count = len(items)

        if output_json:
            print(json.dumps({
                "code": code,
                "data": {
                    "next_key": next_key_out,
                    "update_time": update_time,
                    "update_time_str": update_time_str,
                    "items": df_to_records(items),
                },
            }, ensure_ascii=False))
        else:
            print(SEP64)
            print(f"机构持股  标的：{code}" + (f"  更新：{update_time_str}" if update_time_str else ""))
            print(DIV64)
            print_display_df(_build_institution_display_df(items), max_colwidth=24)
            print(DIV64)
            nk_display = f"已结束(-1)" if next_key_out == "-1" else next_key_out
            print(f"返回条数：{count}   --next-key：{nk_display}")
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
    parser = argparse.ArgumentParser(description="获取机构持股统计，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()
    get_shareholders_institutional(args.code, next_key=args.next_key, num=args.num,
                                   output_json=args.output_json)
