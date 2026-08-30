#!/usr/bin/env python3
"""
获取经济事件日历

功能：获取指定日期范围的经济事件日历数据，支持自动分页
用法：python get_economic_calendar.py --begin-date 2026-06-23 [--end-date 2026-06-25] [--markets US,HK] [--importance HIGH] [--count 50] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --begin-date: 开始日期（yyyy-MM-dd）
- --end-date: 结束日期（yyyy-MM-dd）
- --markets: 市场列表，逗号分隔（HK/US/SH/SG/JP/AU/MY/CA）
- --importance: 重要性（ALL/LOW/MEDIUM/HIGH）
- --count: 每页数量，默认 50，最大 100

返回字段说明：
- title, timestamp, country, star, previous, consensus, actual
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu
import pandas as pd

MARKET_MAP = {
    "HK": futu.Market.HK,
    "US": futu.Market.US,
    "SH": futu.Market.SH,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
    "AU": futu.Market.AU,
    "MY": futu.Market.MY,
    "CA": futu.Market.CA,
}

IMPORTANCE_MAP = {
    "ALL": futu.EconomicImportance.ALL,
    "LOW": futu.EconomicImportance.LOW,
    "MEDIUM": futu.EconomicImportance.MEDIUM,
    "HIGH": futu.EconomicImportance.HIGH,
}


def get_economic_calendar(begin_date, end_date=None, markets_str=None,
                          importance_str=None, count=50, output_json=False, no_page=True):
    market_list = None
    if markets_str:
        market_list = []
        for m in markets_str.split(","):
            m = m.strip().upper()
            market_enum = MARKET_MAP.get(m)
            if market_enum is None:
                raise ValueError(f"不支持的市场: {m}，可选: {list(MARKET_MAP.keys())}")
            market_list.append(market_enum)

    importance = IMPORTANCE_MAP.get(importance_str.upper()) if importance_str else None

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        while True:
            ret, data, next_page, has_more = ctx.get_economic_calendar(
                begin_date=begin_date, end_date=end_date,
                market_list=market_list, importance=importance,
                count=count, next_page=page
            )
            check_ret(ret, data, ctx, "获取经济事件日历")
            if not is_empty(data):
                all_rows.append(data)
            if no_page or not next_page:
                break
            page = next_page

        if not all_rows:
            if output_json:
                print(json.dumps({"begin_date": begin_date, "data": []}))
            else:
                print("无数据")
            return

        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({"begin_date": begin_date, "end_date": end_date,
                              "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"经济事件日历 - {begin_date} ~ {end_date or begin_date}（共 {len(df)} 条）")
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
    parser = argparse.ArgumentParser(description="获取经济事件日历")
    parser.add_argument("--begin-date", required=True, help="开始日期（yyyy-MM-dd）")
    parser.add_argument("--end-date", default=None, help="结束日期（yyyy-MM-dd）")
    parser.add_argument("--markets", default=None,
                        help="市场列表，逗号分隔（HK/US/SH/SG/JP/AU/MY/CA）")
    parser.add_argument("--importance", choices=["ALL", "LOW", "MEDIUM", "HIGH"],
                        default=None, help="重要性")
    parser.add_argument("--count", type=int, default=50, help="每页数量，默认 50，最大 100")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_economic_calendar(args.begin_date, end_date=args.end_date,
                          markets_str=args.markets, importance_str=args.importance,
                          count=args.count, output_json=args.output_json, no_page=args.no_page)
