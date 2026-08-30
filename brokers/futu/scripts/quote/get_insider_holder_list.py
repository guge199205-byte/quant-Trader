#!/usr/bin/env python3
"""
获取内部人持股列表

功能：获取美股股票内部人（高管/董事/大股东）的持股情况列表，同时返回内部人统计摘要
用法：python get_insider_holder_list.py [-h] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持美股正股及基金
- 首页额外返回内部人统计摘要（总人数/增持数/减持数），续页无此摘要

参数说明：
- code: 股票代码，如 US.AAPL
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~20

返回字段说明：
- data.all_count:            总条数
- data.next_key:             分页标识，"-1" 表示无更多数据
- data.insider_total_count:  内部人总人数（仅首页返回）
- data.insider_bought_count: 内部人买入总人数（仅首页返回）
- data.insider_sold_count:   内部人卖出总人数（仅首页返回）
- data.items[]:              内部人持股列表，每项含 holder_id/name/title/holder_quantity/holder_pct
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


def _build_display_df(data):
    rows = []
    any_large_qty = any(
        (r.get("holder_quantity") is not None and not pd.isna(r.get("holder_quantity"))
         and abs(float(r.get("holder_quantity") or 0)) >= 10000)
        for _, r in data.iterrows()
    )
    for _, row in data.iterrows():
        qty_raw = row.get("holder_quantity")
        pct_raw = row.get("holder_pct")
        qty_display = (
            "-" if qty_raw is None or (isinstance(qty_raw, float) and pd.isna(qty_raw))
            else (format_big_number(qty_raw) if any_large_qty else str(int(qty_raw)))
        )
        pct_display = (
            "-" if pct_raw is None or (isinstance(pct_raw, float) and pd.isna(pct_raw))
            else f"{float(pct_raw):.2f}%"
        )
        _hid = row.get("holder_id")
        rows.append({
            "股东ID": int(_hid) if _hid is not None and not pd.isna(_hid) else 0,
            "姓名": str(row.get("name") or "") or "-",
            "职位": str(row.get("title") or "") or "-",
            "持股量": qty_display,
            "持股比例(%)": pct_display,
        })
    return pd.DataFrame(rows)


def get_insider_holder_list(code, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_insider_holder_list(code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取内部人持股列表")

        first = data.iloc[0] if (not is_empty(data)) else None
        _ac = first.get("all_count") if first is not None else None
        all_count = int(_ac) if _ac is not None and not (isinstance(_ac, float) and pd.isna(_ac)) else 0
        ret_next_key = str(first.get("next_key", -1)) if first is not None else "-1"
        insider_total = None
        insider_bought = None
        insider_sold = None
        if first is not None:
            _rt = first.get("insider_total_count")
            if _rt is not None and not (isinstance(_rt, float) and pd.isna(_rt)):
                insider_total = int(_rt)
            _rb = first.get("insider_bought_count")
            if _rb is not None and not (isinstance(_rb, float) and pd.isna(_rb)):
                insider_bought = int(_rb)
            _rs = first.get("insider_sold_count")
            if _rs is not None and not (isinstance(_rs, float) and pd.isna(_rs)):
                insider_sold = int(_rs)

        row_count = len(data) if not is_empty(data) else 0
        next_key_display = "已结束(-1)" if ret_next_key == "-1" else ret_next_key

        if output_json:
            if is_empty(data):
                records = []
            else:
                records = df_to_records(data)
                top_fields = {"all_count", "next_key", "insider_total_count",
                              "insider_bought_count", "insider_sold_count"}
                for r in records:
                    for f in top_fields:
                        r.pop(f, None)
            inner = {
                "all_count": all_count,
                "next_key": ret_next_key,
                "items": records,
            }
            if insider_total is not None:
                inner["insider_total_count"] = insider_total
                inner["insider_bought_count"] = insider_bought
                inner["insider_sold_count"] = insider_sold
            print(json.dumps({"code": code, "data": inner}, ensure_ascii=False))
        else:
            if is_empty(data) and insider_total is None:
                print("无数据")
            else:
                print(SEP64)
                print(f"内部人持股列表  标的：{code}")
                print(DASH64)
                if insider_total is not None:
                    print(f"内部人统计(首页): 总人数={insider_total}  买入人数={insider_bought}  卖出人数={insider_sold}")
                    print(DASH64)
                if is_empty(data):
                    print("无数据")
                else:
                    view = _build_display_df(data)
                    print_display_df(view, max_colwidth=30)
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
    parser = argparse.ArgumentParser(description="获取内部人持股列表（仅美股），支持分页拉取")
    parser.add_argument("code", help="股票代码，如 US.AAPL")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~20")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_insider_holder_list(args.code, next_key=args.next_key, num=args.num, output_json=args.output_json)
