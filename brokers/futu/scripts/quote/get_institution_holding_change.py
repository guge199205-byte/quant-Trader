#!/usr/bin/env python3
"""
获取机构持仓变动

功能：获取指定机构的持仓变动明细，支持变动类型筛选、排序和自动分页
用法：python get_institution_holding_change.py --market US --institution-id 123 [--change-type NEW] [--sort-field CHANGE_PCT] [--sort-dir 0] [--count 20] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场（HK/US）
- --institution-id: 机构 ID（必填）
- --change-type: 变动类型（NEW/SOLD_OUT/INCREASE/DECREASE），默认建仓
- --sort-field: 排序字段（CHANGE_PCT/CHANGE_SHARES/HOLDING_DATE）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 每页数量 [1,200]，默认 20

返回字段说明：
- security, name, portfolio_pct, change_shares, change_pct, holding_date, source
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
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
    "MY": futu.Market.MY,
}

CHANGE_TYPE_MAP = {
    "NEW": futu.InstitutionHoldingChangeType.NEW,
    "SOLD_OUT": futu.InstitutionHoldingChangeType.SOLD_OUT,
    "INCREASE": futu.InstitutionHoldingChangeType.INCREASE,
    "DECREASE": futu.InstitutionHoldingChangeType.DECREASE,
}

SORT_FIELD_MAP = {
    "CHANGE_PCT": futu.InstitutionHoldingChangeSortField.CHANGE_PCT,
    "CHANGE_SHARES": futu.InstitutionHoldingChangeSortField.CHANGE_SHARES,
    "HOLDING_DATE": futu.InstitutionHoldingChangeSortField.HOLDING_DATE,
}

SORT_DIR_MAP = {
    "0": futu.RankSortDir.DESCENDING,
    "1": futu.RankSortDir.ASCENDING,
}


def get_institution_holding_change(market_str, institution_id, change_type_str=None,
                                   sort_field_str=None, sort_dir_str=None, count=20,
                                   output_json=False, no_page=True):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    change_type = CHANGE_TYPE_MAP.get(change_type_str.upper()) if change_type_str else None
    sort_field = SORT_FIELD_MAP.get(sort_field_str.upper()) if sort_field_str else None
    sort_dir = SORT_DIR_MAP.get(sort_dir_str) if sort_dir_str else None

    ctx = None
    try:
        ctx = create_quote_context()

        all_rows = []
        page = None
        all_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_institution_holding_change(
                market=market, institution_id=institution_id, change_type=change_type,
                sort_field=sort_field, sort_dir=sort_dir, count=count, page=page
            )
            check_ret(ret, data, ctx, "获取机构持仓变动")
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
    parser = argparse.ArgumentParser(description="获取机构持仓变动")
    parser.add_argument("--market", required=True, choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--institution-id", type=int, required=True, help="机构 ID")
    parser.add_argument("--change-type", choices=list(CHANGE_TYPE_MAP.keys()), help="变动类型")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()), help="排序字段")
    parser.add_argument("--sort-dir", choices=["0", "1"], help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=20, help="每页数量 [1,200]")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_institution_holding_change(args.market, args.institution_id,
                                  change_type_str=args.change_type, sort_field_str=args.sort_field,
                                  sort_dir_str=args.sort_dir, count=args.count,
                                  output_json=args.output_json, no_page=args.no_page)
