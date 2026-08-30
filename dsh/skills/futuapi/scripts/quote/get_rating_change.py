#!/usr/bin/env python3
"""
获取评级变动

功能：获取美股评级变动数据，支持按变动类型筛选和自动分页
用法：python get_rating_change.py --market US [--change-type UPGRADE] [--count 10] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场（仅 US）
- --change-type: 评级变动类型（UPGRADE/DOWNGRADE/NEW_RATING）
- --count: 每页数量 [1,20]，默认 10

返回字段说明：
- security, name, rating, last_rating, target_price, last_target_price,
  change_type, institution_name, recommendation_date, last_recommendation_date
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
    "US": futu.Market.US,
}

CHANGE_TYPE_MAP = {
    "UPGRADE": futu.RatingChangeType.UPGRADE,
    "DOWNGRADE": futu.RatingChangeType.DOWNGRADE,
    "NEW_RATING": futu.RatingChangeType.NEW_RATING,
}


def get_rating_change(market_str, change_type_str=None, count=10, output_json=False, no_page=True):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    change_type = CHANGE_TYPE_MAP.get(change_type_str.upper()) if change_type_str else None

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        all_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_rating_change(
                market=market, change_type=change_type, count=count, page=page
            )
            check_ret(ret, data, ctx, "获取评级变动")
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
    parser = argparse.ArgumentParser(description="获取评级变动")
    parser.add_argument("--market", required=True, choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--change-type", choices=list(CHANGE_TYPE_MAP.keys()), help="评级变动类型")
    parser.add_argument("--count", type=int, default=10, help="每页数量 [1,20]")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_rating_change(args.market, change_type_str=args.change_type, count=args.count,
                     output_json=args.output_json, no_page=args.no_page)
