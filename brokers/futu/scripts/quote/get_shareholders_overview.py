#!/usr/bin/env python3
"""
获取持股统计

功能：一次请求同时返回指定股票的主要股东（main_holder）和持股类型（holder_type）两组数据

用法：python get_shareholders_overview.py [-h] [--period-id PERIOD_ID] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金
- period_id 为 0 或不传时，同一次响应中额外返回可用报告期列表（holding_period 子表）

参数说明：
- code: 股票代码，如 HK.00700
- --period-id: 报告期 ID；传 0 或不传则返回最新数据，并额外返回可用报告期列表

返回字段说明：
- data.main_holder:     主要股东列表，每项含 static_date_str/name/holder_pct/holder_id
- data.holder_type:     持股类型列表，结构同上（holder_id 为 0）
- data.holding_period:  可用报告期列表（仅首次请求返回），每项含 period_text/period_id
"""
import argparse
import json
import sys
import os as _os

import pandas as pd

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
_repo_root = _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "..", ".."))
_ftapi_local = _os.path.join(_repo_root, "ftapi4python")
if _os.path.isdir(_ftapi_local):
    sys.path.insert(0, _ftapi_local)
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
    print_display_df,
)

SEP64 = "=" * 64
DIV64 = "-" * 64


def _fmt_pct(val):
    try:
        return f"{float(val):.2f}%"
    except (TypeError, ValueError):
        return str(val) if val is not None else ""


def get_shareholders_overview(code, period_id=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_shareholders_overview(code, period_id=period_id)
        check_ret(ret, data, ctx, "获取持股统计")

        main_holder_df = data.get("main_holder") if isinstance(data, dict) else None
        holder_type_df = data.get("holder_type") if isinstance(data, dict) else None
        hp_df = data.get("holding_period") if isinstance(data, dict) else None

        no_data = is_empty(data) or (
            is_empty(main_holder_df) and is_empty(holder_type_df) and is_empty(hp_df)
        )
        if no_data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print("无数据")
            return

        mh_rows = 0 if is_empty(main_holder_df) else len(main_holder_df)
        ht_rows = 0 if is_empty(holder_type_df) else len(holder_type_df)
        hp_rows = 0 if is_empty(hp_df) else len(hp_df)

        if output_json:
            print(json.dumps({"code": code, "data": {
                "main_holder": df_to_records(main_holder_df) if not is_empty(main_holder_df) else [],
                "holder_type": df_to_records(holder_type_df) if not is_empty(holder_type_df) else [],
                "holding_period": df_to_records(hp_df) if not is_empty(hp_df) else [],
            }}, ensure_ascii=False))
        else:
            print(SEP64)
            print(f"持股统计  标的：{code}")

            print(DIV64)
            print("【主要股东】")
            if not is_empty(main_holder_df):
                rows = []
                for _, row in main_holder_df.iterrows():
                    holder_id_val = row.get("holder_id")
                    if holder_id_val is None or (isinstance(holder_id_val, float) and pd.isna(holder_id_val)) or holder_id_val == 0:
                        hid_str = "-"
                    else:
                        hid_str = str(int(holder_id_val))
                    rows.append({
                        "统计日期": str(row.get("static_date_str", "") or ""),
                        "名称": str(row.get("name", "") or ""),
                        "持股占比(%)": _fmt_pct(row.get("holder_pct")),
                        "股东ID": hid_str,
                    })
                print_display_df(pd.DataFrame(rows), max_colwidth=30)
            else:
                print("(无数据)")

            print(DIV64)
            print("【持股类型】")
            if not is_empty(holder_type_df):
                rows = []
                for _, row in holder_type_df.iterrows():
                    rows.append({
                        "统计日期": str(row.get("static_date_str", "") or ""),
                        "名称": str(row.get("name", "") or ""),
                        "持股占比(%)": _fmt_pct(row.get("holder_pct")),
                    })
                print_display_df(pd.DataFrame(rows), max_colwidth=30)
            else:
                print("(无数据)")

            if hp_df is not None and not is_empty(hp_df):
                print(DIV64)
                print("可用报告期列表（holding_period）：")
                h_rows = []
                for _, row in hp_df.iterrows():
                    h_rows.append({
                        "报告期": str(row.get("period_text", "") or ""),
                        "period_id（回传）": str(row.get("period_id", "") or ""),
                    })
                print_display_df(pd.DataFrame(h_rows), max_colwidth=20)

            print(DIV64)
            print(f"返回条数：主要股东 {mh_rows} 条，持股类型 {ht_rows} 条，报告期 {hp_rows} 条")
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
    parser = argparse.ArgumentParser(description="获取股东持股概览")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--period-id", type=int, default=None, dest="period_id",
                        help="报告期 ID；传 0 或不传则返回最新数据，并额外返回可用报告期列表")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    get_shareholders_overview(
        args.code,
        period_id=args.period_id,
        output_json=args.output_json,
    )
