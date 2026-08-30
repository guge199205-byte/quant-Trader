#!/usr/bin/env python3
"""
获取热力图数据

功能：获取指定市场的板块热力图数据，支持排序、板块类型筛选和自动分页
用法：python get_heat_map_data.py --market US [--sort-field CHANGE_RATE] [--ascend] [--count 30] [--plate-type INDUSTRY] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场（HK/US/CN）
- --sort-field: 排序字段（CHANGE_RATE/MARKET_VAL/TURNOVER/HOT）
- --ascend: 升序排列（默认降序）
- --count: 每页数量 [1,200]，默认 30
- --plate-type: 板块类型（INDUSTRY/CONCEPT/THEME）

返回字段说明：
- plate, plate_name, cur_price, change_rate, turnover, volume, market_val,
  pe_avg, rise_count, fall_count, equal_count, leader_stock, description
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
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
    "MY": futu.Market.MY,
    "AU": futu.Market.AU,
    "CA": futu.Market.CA,
}

SORT_FIELD_MAP = {
    "CHANGE_RATE": futu.HeatMapSortField.CHANGE_RATE,
    "MARKET_VAL": futu.HeatMapSortField.MARKET_VAL,
    "TURNOVER": futu.HeatMapSortField.TURNOVER,
    "HOT": futu.HeatMapSortField.HOT,
}

PLATE_TYPE_MAP = {
    "INDUSTRY": futu.HeatMapPlateType.INDUSTRY,
    "CONCEPT": futu.HeatMapPlateType.CONCEPT,
    "THEME": futu.HeatMapPlateType.THEME,
}


def get_heat_map_data(market_str, sort_field_str=None, ascend=False, count=30,
                      plate_type_str=None, output_json=False, no_page=True):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    sort_field = SORT_FIELD_MAP.get(sort_field_str.upper()) if sort_field_str else None
    plate_type = PLATE_TYPE_MAP.get(plate_type_str.upper()) if plate_type_str else None

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        all_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_heat_map_data(
                market=market, sort_field=sort_field, ascend=ascend,
                count=count, page=page, plate_type=plate_type
            )
            check_ret(ret, data, ctx, "获取热力图数据")
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
    parser = argparse.ArgumentParser(description="获取热力图数据")
    parser.add_argument("--market", required=True, choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()), help="排序字段")
    parser.add_argument("--ascend", action="store_true", help="升序排列（默认降序）")
    parser.add_argument("--count", type=int, default=30, help="每页数量 [1,200]")
    parser.add_argument("--plate-type", choices=list(PLATE_TYPE_MAP.keys()), help="板块类型")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_heat_map_data(args.market, sort_field_str=args.sort_field, ascend=args.ascend,
                     count=args.count, plate_type_str=args.plate_type, output_json=args.output_json, no_page=args.no_page)
