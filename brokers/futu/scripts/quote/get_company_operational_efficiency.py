#!/usr/bin/env python3
"""
获取公司经营效率

功能：获取指定股票的公司经营效率数据，包括员工人数、人均营收、人均营业利润、人均净利润等指标
用法：python get_company_operational_efficiency.py [-h] [--next-key NEXT_KEY] [--num NUM] [--currency-code CURRENCY_CODE] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --currency-code: 货币代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不传返回默认货币

返回字段说明：
- item_list:     经营效率列表，每项含 period_text/end_date_str/employee_num/employee_num_yoy/income_per_capita/income_per_capita_yoy/profit_per_capita/profit_per_capita_yoy/net_profit_per_capita/net_profit_per_capita_yoy（各 yoy 为百分号前的值）
- next_key:      分页标识，"-1" 表示无更多数据
- currency_code: 货币代码（ISO 4217）
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, print_display_df, format_big_number

SEP64 = "=" * 64
DIV64 = "-" * 64


def get_company_operational_efficiency(code, num=None, next_key=None, currency_code=None,
                                       output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        if num is not None:
            kwargs["num"] = num
        if next_key is not None:
            kwargs["next_key"] = next_key
        if currency_code is not None:
            kwargs["currency_code"] = currency_code
        ret, data = ctx.get_company_operational_efficiency(code, **kwargs)
        check_ret(ret, data, ctx, "获取公司经营效率")

        item_list = data.get("item_list", []) if isinstance(data, dict) else None
        no_data = is_empty(data) or not item_list
        if no_data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        currency = data.get("currency_code", "")
        nk = data.get("next_key", "")

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False, default=str))
            return

        print(SEP64)
        print("公司经营效率  标的：" + code)
        print(DIV64)
        currency_label = "(" + currency + ")" if currency else ""
        rows = []
        for item in item_list:
            period = item.get("period_text") or str(item.get("fiscal_year", ""))
            end_date = item.get("end_date_str", "")
            emp = item.get("employee_num")
            emp_yoy = item.get("employee_num_yoy")
            inc = item.get("income_per_capita")
            inc_yoy = item.get("income_per_capita_yoy")
            profit = item.get("profit_per_capita")
            profit_yoy = item.get("profit_per_capita_yoy")
            net = item.get("net_profit_per_capita")
            net_yoy = item.get("net_profit_per_capita_yoy")

            def _fmt_yoy(v):
                return "{:.2f}%".format(v) if v is not None else "-"

            rows.append({
                "周期": period,
                "截止日": end_date,
                "员工数": str(emp) if emp is not None else "-",
                "员工同比": _fmt_yoy(emp_yoy),
                "人均营收" + currency_label: format_big_number(inc) if inc is not None else "-",
                "营收同比": _fmt_yoy(inc_yoy),
                "人均营业利润" + currency_label: format_big_number(profit) if profit is not None else "-",
                "利润同比": _fmt_yoy(profit_yoy),
                "人均净利润" + currency_label: format_big_number(net) if net is not None else "-",
                "净利润同比": _fmt_yoy(net_yoy),
            })

        import pandas as pd
        print_display_df(pd.DataFrame(rows), max_colwidth=18)
        print(DIV64)
        nk_display = nk if nk else "-"
        print("返回条数：" + str(len(item_list)) + "   --next-key：" + nk_display)
        print(SEP64)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print("错误：" + str(e))
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取经营效率数据，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--next-key", default=None,
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", "-n", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--currency-code", default=None,
                        help="货币代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不传返回默认货币")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_company_operational_efficiency(
        args.code,
        num=args.num,
        next_key=args.next_key,
        currency_code=args.currency_code,
        output_json=args.output_json,
    )
