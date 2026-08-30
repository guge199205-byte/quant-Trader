#!/usr/bin/env python3
"""
获取派息日历

功能：获取指定市场和日期的派息日历数据
用法：python get_dividend_calendar.py --market US --date 2026-06-23 [--count 50] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US/MY/SG/JP）
- --date: 日期（yyyy-MM-dd）
- --offset: 起始位置（data_from 参数）
- --count: 返回数量

返回字段说明：
- security, name, statement, record_date, ex_date, dividend_payable_date
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu

MARKET_MAP = {
    "HK": futu.Market.HK,
    "US": futu.Market.US,
    "MY": futu.Market.MY,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
}


def get_dividend_calendar(market_str, date, offset=None, count=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_dividend_calendar(market, date, data_from=offset, count=count)
        check_ret(ret, data, ctx, "获取派息日历")

        all_count, df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"all_count": 0, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"all_count": all_count, "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"派息日历 - {market_str} {date}（共 {all_count} 条）")
            print("=" * 70)
            print(df.to_string(index=False))
            print(f"\n共 {len(df)} 条记录")
            print("=" * 70)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取派息日历")
    parser.add_argument("--market", required=True, choices=["HK", "US", "MY", "SG", "JP"],
                        help="市场")
    parser.add_argument("--date", required=True, help="日期（yyyy-MM-dd）")
    parser.add_argument("--offset", type=int, default=None, help="起始位置")
    parser.add_argument("--count", type=int, default=None, help="返回数量")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_dividend_calendar(args.market, args.date, offset=args.offset,
                          count=args.count, output_json=args.output_json)
