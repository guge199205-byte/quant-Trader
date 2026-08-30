#!/usr/bin/env python3
"""
获取内部人交易列表

功能：获取美股股票内部人（高管/董事/大股东）的交易记录列表，支持按持有人过滤和分页续拉
用法：python get_insider_trade_list.py [-h] [--holder-id HOLDER_ID] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持美股正股及基金

参数说明：
- code: 股票代码，如 US.AAPL
- --holder-id: 持有人对象 ID，不传则查询全部内部人（可选）；可取自 GetInsiderHolderList（3241）或本协议返回的 holder_id
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.all_count: 总条数
- data.next_key:  分页标识，"-1" 表示无更多数据
- data.items[]:   内部人交易列表，每项含 holder_id/name/title/transaction_type/trade_shares/min_price/max_price/min_trade_date/min_trade_date_str/max_trade_date/max_trade_date_str/security_holder_quantity/is_proposed_sale_of_securities/security_description/source_group_name 等
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
    any_large_shares = any(
        (r.get("trade_shares") is not None and not (isinstance(r.get("trade_shares"), float) and pd.isna(r.get("trade_shares")))
         and abs(float(r.get("trade_shares") or 0)) >= 10000)
        for _, r in data.iterrows()
    )
    any_large_qty = any(
        (r.get("security_holder_quantity") is not None and not (isinstance(r.get("security_holder_quantity"), float) and pd.isna(r.get("security_holder_quantity")))
         and abs(float(r.get("security_holder_quantity") or 0)) >= 10000)
        for _, r in data.iterrows()
    )
    for _, row in data.iterrows():
        # 价格区间（已为实际值）
        minp_raw = row.get("min_price")
        maxp_raw = row.get("max_price")
        minp_null = minp_raw is None or (isinstance(minp_raw, float) and pd.isna(minp_raw))
        maxp_null = maxp_raw is None or (isinstance(maxp_raw, float) and pd.isna(maxp_raw))
        minp = float(minp_raw) if not minp_null else 0.0
        maxp = float(maxp_raw) if not maxp_null else 0.0

        if minp <= 0 and maxp <= 0:
            price_range = "未透露"
        elif minp == maxp or maxp <= 0:
            price_range = f"{minp:.3f}"
        elif minp <= 0:
            price_range = f"{maxp:.3f}"
        else:
            price_range = f"{minp:.3f}~{maxp:.3f}"

        # 日期区间：仅展示字符串（去冗余）
        mind = str(row.get("min_trade_date_str") or "").strip()
        maxd = str(row.get("max_trade_date_str") or "").strip()
        if not mind and not maxd:
            date_range = "未透露"
        elif mind == maxd or not maxd:
            date_range = mind
        elif not mind:
            date_range = maxd
        else:
            date_range = f"{mind}~{maxd}"

        is_plan = row.get("is_proposed_sale_of_securities")
        is_plan_nan = is_plan is None or (isinstance(is_plan, float) and pd.isna(is_plan))

        shares_raw = row.get("trade_shares")
        shares_nan = shares_raw is None or (isinstance(shares_raw, float) and pd.isna(shares_raw))
        shares_display = (
            "-" if shares_nan
            else (format_big_number(int(shares_raw)) if any_large_shares else str(int(shares_raw)))
        )

        qty_raw = row.get("security_holder_quantity")
        qty_nan = qty_raw is None or (isinstance(qty_raw, float) and pd.isna(qty_raw))
        qty_display = (
            "-" if qty_nan
            else (format_big_number(int(qty_raw)) if any_large_qty else str(int(qty_raw)))
        )

        rows.append({
            "对象ID": int(row.get("holder_id") or 0) if not (isinstance(row.get("holder_id"), float) and pd.isna(row.get("holder_id") or 0)) else "-",
            "姓名": str(row.get("name") or "") or "-",
            "职位": str(row.get("title") or "") or "-",
            "交易类型": str(row.get("transaction_type") or "") or "-",
            "股数": shares_display,
            "持股总数": qty_display,
            "价格区间": price_range,
            "日期区间": date_range,
            "计划卖出": ("是" if is_plan else "否") if not is_plan_nan else "-",
            "证券描述": str(row.get("security_description") or "") or "-",
            "数据来源": str(row.get("source_group_name") or "") or "-",
        })
    return pd.DataFrame(rows)


def get_insider_trade_list(code, holder_id=None, num=None, next_key=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_insider_trade_list(code, holder_id=holder_id, num=num, next_key=next_key)
        check_ret(ret, data, ctx, "获取内部人交易列表")

        all_count = data.attrs.get("all_count") if hasattr(data, "attrs") else None
        ret_next_key = str(data.attrs.get("next_key", "-1")) if hasattr(data, "attrs") else "-1"
        all_count = int(all_count) if all_count is not None else 0
        row_count = len(data) if not is_empty(data) else 0
        next_key_display = "已结束(-1)" if ret_next_key == "-1" else ret_next_key

        if output_json:
            if is_empty(data):
                records = []
            else:
                records = df_to_records(data)
            print(json.dumps({
                "code": code,
                "data": {
                    "all_count": all_count,
                    "next_key": ret_next_key,
                    "items": records,
                },
            }, ensure_ascii=False))
        else:
            if is_empty(data):
                print("无数据")
            else:
                print(SEP64)
                print(f"内部人交易列表  标的：{code}")
                print(DASH64)
                view = _build_display_df(data)
                print_display_df(view, max_colwidth=22)
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
    parser = argparse.ArgumentParser(description="获取内部人交易列表（仅美股），支持分页拉取")
    parser.add_argument("code", help="股票代码，如 US.AAPL")
    parser.add_argument("--holder-id", type=int, default=None, dest="holder_id",
                        help="持有人对象 ID，不传则查询全部内部人（可选）；可取自 GetInsiderHolderList（3241）或本协议返回的 holder_id")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_insider_trade_list(
        args.code, holder_id=args.holder_id,
        num=args.num, next_key=args.next_key, output_json=args.output_json,
    )
