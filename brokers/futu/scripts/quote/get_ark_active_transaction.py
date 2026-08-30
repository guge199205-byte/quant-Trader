#!/usr/bin/env python3
"""
获取 ARK 主动交易聚合

功能：获取 ARK 主动交易聚合数据，支持自动分页
用法：python get_ark_active_transaction.py [--holding-type INCREASE] [--cycle ONE_DAY] [--sort-field CHANGE_AMOUNT] [--sort-dir 0] [--count 50] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --holding-type: 持仓类型（INCREASE/DECREASE/NEW/SOLD_OUT）
- --cycle: 周期（ONE_DAY/FIVE_DAY/TEN_DAY/THIRTY_DAY/SIXTY_DAY）
- --sort-field: 排序字段（CHANGE_AMOUNT/CHANGE_SHARES）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 每页返回数量，默认 50

返回字段说明：
- security, name, change_amount, change_shares
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu
import pandas as pd

HOLDING_TYPE_MAP = {
    "INCREASE": futu.ArkActiveTransactionHoldingType.INCREASE,
    "DECREASE": futu.ArkActiveTransactionHoldingType.DECREASE,
    "NEW": futu.ArkActiveTransactionHoldingType.NEW,
    "SOLD_OUT": futu.ArkActiveTransactionHoldingType.SOLD_OUT,
}

CYCLE_MAP = {
    "ONE_DAY": futu.ArkCycleType.ONE_DAY,
    "FIVE_DAY": futu.ArkCycleType.FIVE_DAY,
    "TEN_DAY": futu.ArkCycleType.TEN_DAY,
    "THIRTY_DAY": futu.ArkCycleType.THIRTY_DAY,
    "SIXTY_DAY": futu.ArkCycleType.SIXTY_DAY,
}

SORT_FIELD_MAP = {
    "CHANGE_AMOUNT": futu.ArkActiveTransactionSortField.CHANGE_AMOUNT,
    "CHANGE_SHARES": futu.ArkActiveTransactionSortField.CHANGE_SHARES,
}

SORT_DIR_MAP = {
    0: futu.RankSortDir.DESCENDING,
    1: futu.RankSortDir.ASCENDING,
}


def get_ark_active_transaction(holding_type=None, cycle=None, sort_field=None, sort_dir=0,
                                count=50, output_json=False, no_page=True):
    holding_type_enum = HOLDING_TYPE_MAP.get(holding_type.upper()) if holding_type else None
    cycle_enum = CYCLE_MAP.get(cycle.upper()) if cycle else None
    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    sort_dir_enum = SORT_DIR_MAP.get(sort_dir)

    ctx = None
    try:
        ctx = create_quote_context()
        all_rows = []
        page = None
        total_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_ark_active_transaction(
                holding_type=holding_type_enum, cycle_type=cycle_enum,
                sort_field=sort_field_enum, sort_dir=sort_dir_enum,
                count=count, page=page
            )
            check_ret(ret, data, ctx, "获取ARK主动交易聚合")
            total_count = all_count
            if not is_empty(data):
                all_rows.append(data)
            if no_page or not next_page:
                break
            page = next_page

        if not all_rows:
            if output_json:
                print(json.dumps({"all_count": 0, "data": []}))
            else:
                print("无数据")
            return

        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({"all_count": total_count, "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"ARK 主动交易聚合（共 {total_count} 条）")
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
    parser = argparse.ArgumentParser(description="获取 ARK 主动交易聚合")
    parser.add_argument("--holding-type", choices=list(HOLDING_TYPE_MAP.keys()),
                        default=None, help="持仓类型")
    parser.add_argument("--cycle", choices=list(CYCLE_MAP.keys()),
                        default=None, help="周期")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()),
                        default=None, help="排序字段")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], default=0,
                        help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=50, help="每页返回数量")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_ark_active_transaction(holding_type=args.holding_type, cycle=args.cycle,
                                sort_field=args.sort_field, sort_dir=args.sort_dir,
                                count=args.count, output_json=args.output_json, no_page=args.no_page)
