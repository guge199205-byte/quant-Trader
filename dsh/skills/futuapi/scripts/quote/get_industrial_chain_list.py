#!/usr/bin/env python3
"""
获取产业链列表

功能：获取指定市场的产业链列表，支持关键字搜索和自动分页
用法：python get_industrial_chain_list.py --market HK [--keyword 芯片] [--count 20] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场（HK/US/CN/JP/SG/MY）
- --keyword: 搜索关键字（可选）
- --count: 每页数量 [1,50]，默认 20

返回字段说明：
- chain_id, chain_type, name, detail, market_cap, stocks_num, relation_security_list
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
    "CN": futu.Market.SH,
    "JP": futu.Market.JP,
    "SG": futu.Market.SG,
    "MY": futu.Market.MY,
    "AU": futu.Market.AU,
    "CA": futu.Market.CA,
}


def get_industrial_chain_list(market_str, keyword=None, count=20, output_json=False, no_page=True):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        while True:
            ret, data, next_page, all_count = ctx.get_industrial_chain_list(
                market=market, keyword=keyword, count=count, page=page
            )
            check_ret(ret, data, ctx, "获取产业链列表")
            if not is_empty(data):
                all_rows.append(data)
            if no_page or not next_page:
                break
            page = next_page

        if not all_rows:
            print("无数据")
            return

        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({"all_count": all_count, "records": df_to_records(df)},
                             ensure_ascii=False, indent=2))
        else:
            print(f"总数据量: {all_count}")
            print(df.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取产业链列表")
    parser.add_argument("--market", required=True, choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--keyword", help="搜索关键字")
    parser.add_argument("--count", type=int, default=20, help="每页数量 [1,50]")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_industrial_chain_list(args.market, keyword=args.keyword, count=args.count,
                             output_json=args.output_json, no_page=args.no_page)
