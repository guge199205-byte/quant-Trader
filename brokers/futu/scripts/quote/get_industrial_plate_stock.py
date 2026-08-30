#!/usr/bin/env python3
"""
获取产业板块成分股

功能：获取产业板块内的成分股列表，支持按市场筛选、排序和自动分页
用法：python get_industrial_plate_stock.py --plate-id 123 [--chain-id 456] [--markets HK,US] [--sort-field MARKET_VAL] [--ascend] [--count 50] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --chain-id: 产业链 ID（与 --plate-id 二选一，plate-id 优先）
- --plate-id: 产业板块 ID（优先使用）
- --markets: 市场筛选，逗号分隔（HK/US/CN/JP/SG/MY）
- --sort-field: 排序字段（CODE/CHANGE_RATE/TURNOVER/VOLUME/MARKET_VAL）
- --ascend: 升序排列（默认降序）
- --count: 每页数量 [1,200]，默认 50

返回字段说明：
- security, name
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
}

SORT_FIELD_MAP = {
    "CODE": futu.PlateStockSortField.CODE,
    "CHANGE_RATE": futu.PlateStockSortField.CHANGE_RATE,
    "TURNOVER": futu.PlateStockSortField.TURNOVER,
    "VOLUME": futu.PlateStockSortField.VOLUME,
    "MARKET_VAL": futu.PlateStockSortField.MARKET_VAL,
}


def get_industrial_plate_stock(chain_id=None, plate_id=None, markets_str=None,
                               sort_field_str=None, ascend=False, count=50,
                               output_json=False, no_page=True):
    if chain_id is None and plate_id is None:
        raise ValueError("必须指定 --chain-id 或 --plate-id 其中之一")

    market_list = None
    if markets_str:
        market_list = []
        for m in markets_str.split(","):
            m = m.strip().upper()
            market_enum = MARKET_MAP.get(m)
            if market_enum is None:
                raise ValueError(f"不支持的市场: {m}，可选: {list(MARKET_MAP.keys())}")
            market_list.append(market_enum)

    sort_field = SORT_FIELD_MAP.get(sort_field_str.upper()) if sort_field_str else None

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        all_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_industrial_plate_stock(
                chain_id=chain_id, plate_id=plate_id, market_list=market_list,
                sort_field=sort_field, ascend=ascend, count=count, page=page
            )
            check_ret(ret, data, ctx, "获取产业板块成分股")
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
    parser = argparse.ArgumentParser(description="获取产业板块成分股")
    parser.add_argument("--chain-id", type=int, help="产业链 ID")
    parser.add_argument("--plate-id", type=int, help="产业板块 ID（优先使用）")
    parser.add_argument("--markets", help="市场筛选，逗号分隔（HK/US/CN/JP/SG/MY）")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()), help="排序字段")
    parser.add_argument("--ascend", action="store_true", help="升序排列（默认降序）")
    parser.add_argument("--count", type=int, default=50, help="每页数量 [1,200]")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_industrial_plate_stock(chain_id=args.chain_id, plate_id=args.plate_id,
                              markets_str=args.markets, sort_field_str=args.sort_field,
                              ascend=args.ascend, count=args.count, output_json=args.output_json, no_page=args.no_page)
