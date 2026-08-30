#!/usr/bin/env python3
"""
获取卖空异动榜

功能：获取指定市场的卖空异动排行数据
用法：python get_short_selling_rank.py [--market US] [--sort-field SHORT_NUMBER_CHANGE] [--sort-dir 0] [--count 10] [--offset 0] [--plates US.BK2024] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US），默认 US
- --sort-field: 排序字段（SHORT_NUMBER_CHANGE/SHORT_RATIO_CHANGE/SHORT_NUMBER/SHORT_RATIO/VOLUME/POSITION_VOLUME/POSITION_RATIO/DAYS_TO_COVER/WEEK_AVG_VOLUME/WEEK_AVG_SHORT_NUMBER/WEEK_AVG_SHORT_RATIO/MONTH_AVG_VOLUME/MONTH_AVG_SHORT_NUMBER/MONTH_AVG_SHORT_RATIO）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量，范围 [1,35]，默认 10
- --offset: 起始偏移
- --plates: 板块代码列表，逗号分隔（如 "US.BK2024,US.BK2025"）

返回字段说明：
- security: 股票代码
- name: 名称
- close_price: 收盘价
- change_ratio: 涨跌幅
- change_ratio_5d/10d: 5日/10日涨跌幅
- volume: 成交量
- short_number: 卖空股数
- short_number_change: 卖空股数变化
- short_ratio: 卖空比例
- short_ratio_change: 卖空比例变化
- short_position_volume: 空头持仓量
- short_position_ratio: 空头持仓占比
- days_to_cover: 回补天数
- week_avg_short_number/ratio: 周均卖空股数/比例
- month_avg_short_number/ratio: 月均卖空股数/比例
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
}

SORT_FIELD_MAP = {
    "SHORT_NUMBER_CHANGE": futu.ShortSellingSortField.SHORT_NUMBER_CHANGE,
    "SHORT_RATIO_CHANGE": futu.ShortSellingSortField.SHORT_RATIO_CHANGE,
    "SHORT_NUMBER": futu.ShortSellingSortField.SHORT_NUMBER,
    "SHORT_RATIO": futu.ShortSellingSortField.SHORT_RATIO,
    "VOLUME": futu.ShortSellingSortField.VOLUME,
    "POSITION_VOLUME": futu.ShortSellingSortField.POSITION_VOLUME,
    "POSITION_RATIO": futu.ShortSellingSortField.POSITION_RATIO,
    "DAYS_TO_COVER": futu.ShortSellingSortField.DAYS_TO_COVER,
    "WEEK_AVG_VOLUME": futu.ShortSellingSortField.WEEK_AVG_VOLUME,
    "WEEK_AVG_SHORT_NUMBER": futu.ShortSellingSortField.WEEK_AVG_SHORT_NUMBER,
    "WEEK_AVG_SHORT_RATIO": futu.ShortSellingSortField.WEEK_AVG_SHORT_RATIO,
    "MONTH_AVG_VOLUME": futu.ShortSellingSortField.MONTH_AVG_VOLUME,
    "MONTH_AVG_SHORT_NUMBER": futu.ShortSellingSortField.MONTH_AVG_SHORT_NUMBER,
    "MONTH_AVG_SHORT_RATIO": futu.ShortSellingSortField.MONTH_AVG_SHORT_RATIO,
}

SORT_DIR_MAP = {
    0: futu.RankSortDir.DESCENDING,
    1: futu.RankSortDir.ASCENDING,
}


def get_short_selling_rank(market_str=None, sort_field=None, sort_dir=0, count=10,
                           offset=None, plates=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper()) if market_str else None
    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    sort_dir_enum = SORT_DIR_MAP.get(sort_dir)
    plate_list = plates.split(",") if plates else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_short_selling_rank(market=market, sort_field=sort_field_enum,
                                               sort_dir=sort_dir_enum, count=count,
                                               offset=offset, plate_list=plate_list)
        check_ret(ret, data, ctx, "获取卖空异动榜")

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
            print(f"卖空异动榜 - {market_str or 'ALL'}（共 {all_count} 条）")
            print("=" * 70)
            print(df.to_string(index=False))
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
    parser = argparse.ArgumentParser(description="获取卖空异动榜")
    parser.add_argument("--market", choices=["HK", "US"], default="US", help="市场（默认 US）")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()),
                        default=None, help="排序字段")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], default=0,
                        help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=10, help="返回数量，范围 [1,35]")
    parser.add_argument("--offset", type=int, default=None, help="起始偏移")
    parser.add_argument("--plates", default=None,
                        help="板块代码列表，逗号分隔（如 US.BK2024,US.BK2025）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_short_selling_rank(market_str=args.market, sort_field=args.sort_field,
                           sort_dir=args.sort_dir, count=args.count,
                           offset=args.offset, plates=args.plates,
                           output_json=args.output_json)
