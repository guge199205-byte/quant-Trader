#!/usr/bin/env python3
"""
获取主营构成

功能：获取指定股票的主营构成数据，返回产品、行业、地区、业务各维度数据
用法：python get_financials_revenue_breakdown.py [-h] [--date DATE] [--financial-type FINANCIAL_TYPE] [--currency-code CURRENCY_CODE] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --date: 筛选时间戳；从输出 screen_date_list 取 date 值可查历史；不填返回最新一期
- --financial-type: 财报类型：1=Q1单季报 2=Q2单季报 3=Q3单季报 4=Q4单季报 5=半年报 6=Q9累计报 7=年报 9=聚合季报
- --currency-code: 币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据

返回字段说明：
- period:           财报周期，如 "2024/Q3"
- breakdown_list:   各维度数据列表，每个维度含 type（维度类型）和 item_list（主营构成列表）
  - type:           维度类型（1=产品 2=行业 4=地区 8=业务）
  - item_list:      每项含 name/main_oper_income（营收）/ratio（占比，百分号前的值）
- currency_code:    货币代码（ISO 4217）
- screen_date_list: 可选历史日期列表（仅 date 与 financial_type 均未填时返回），每项含 date/period_text/financial_type
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
    print_display_df,
)

import math
import pandas as pd

SEP64  = "=" * 64
DASH64 = "-" * 64

_TYPE_NAMES = {1: "产品", 2: "行业", 4: "地区", 8: "业务"}
_FINANCIAL_TYPE_NAMES = {
    1: "Q1单季报",
    2: "Q2单季报",
    3: "Q3单季报",
    4: "Q4单季报",
    5: "半年报",
    6: "Q9累计报",
    7: "年报",
    9: "聚合季报",
}


def _fmt_income(val):
    """将营业收入格式化为可读字符串，自动选择万/亿单位。"""
    if val is None:
        return "—"
    try:
        f = float(val)
        if math.isnan(f):
            return "—"
        abs_f = abs(f)
        if abs_f >= 1e8:
            return f"{f / 1e8:.2f}亿"
        if abs_f >= 1e4:
            return f"{f / 1e4:.2f}万"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return "—"


def get_financials_revenue_breakdown(code, date=None, financial_type=None,
                                     currency_code=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()

        ret, data = ctx.get_financials_revenue_breakdown(
            code,
            date=date,
            financial_type=financial_type,
            currency_code=currency_code,
        )
        check_ret(ret, data, ctx, "获取主营构成")

        breakdown_list = data.get("breakdown_list", []) if isinstance(data, dict) else []
        no_data = is_empty(data) or not breakdown_list

        if no_data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        period = data.get("period", "")
        currency = data.get("currency_code", "")
        screen_dates = data.get("screen_date_list", [])

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False, default=str))
        else:
            print(SEP64)
            print(f"主营构成  标的：{code}  周期：{period}  货币：{currency}")

            for group in breakdown_list:
                group_type = group.get("type", 0)
                type_name = _TYPE_NAMES.get(group_type, str(group_type))
                item_list = group.get("item_list", [])
                if not item_list:
                    continue
                print(DASH64)
                print(f"【{type_name}维度】")
                rows = []
                for item in item_list:
                    income_val = item.get("main_oper_income")
                    ratio_val = item.get("ratio")
                    try:
                        ratio_str = f"{float(ratio_val):.2f}%" if (ratio_val is not None and not math.isnan(float(ratio_val))) else "—"
                    except (TypeError, ValueError):
                        ratio_str = "—"
                    rows.append({
                        "名称": item.get("name", ""),
                        "营业收入": _fmt_income(income_val),
                        "占比": ratio_str,
                    })
                print_display_df(pd.DataFrame(rows), max_colwidth=32)

            if screen_dates:
                sd_rows = []
                for sd in screen_dates:
                    ft = sd.get("financial_type", 0)
                    ft_name = _FINANCIAL_TYPE_NAMES.get(ft, str(ft))
                    sd_rows.append({
                        "财报期": sd.get("period_text", ""),
                        "date(回传型)": sd.get("date", 0),
                        "财报类型": f"{ft}({ft_name})",
                    })
                print(f"\n可选日期（共 {len(screen_dates)} 个）:")
                print_display_df(pd.DataFrame(sd_rows), max_colwidth=24)

            print(SEP64)

    except SystemExit:
        raise
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误：{e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取公司主营收入构成")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument(
        "--date", type=int, default=None,
        help="筛选时间戳；从输出 screen_date_list 取 date 值可查历史；不填返回最新一期",
    )
    parser.add_argument(
        "--financial-type", type=int, default=None,
        help=(
            "财报类型："
            "1=Q1单季报  2=Q2单季报  3=Q3单季报  4=Q4单季报  "
            "5=半年报  6=Q9累计报  7=年报  9=聚合季报"
        ),
    )
    parser.add_argument(
        "--currency-code", type=str, default=None,
        help="币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据",
    )
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    get_financials_revenue_breakdown(
        args.code,
        date=args.date,
        financial_type=args.financial_type,
        currency_code=args.currency_code,
        output_json=args.output_json,
    )
