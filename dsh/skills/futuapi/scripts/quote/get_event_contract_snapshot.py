#!/usr/bin/env python3
"""
获取预测市场快照

功能：批量获取预测市场实时快照（最新价、累计成交量、YES/NO 买卖盘、持仓量等），无需订阅
用法：python get_event_contract_snapshot.py EC.KXODIMATCH-26JUL140600INDENG-IND --json
      python get_event_contract_snapshot.py EC.xxx1 EC.xxx2 [--json]

接口：OpenQuoteContext.get_event_contract_snapshot(code_list)
返回：DataFrame；字段 code / name / event_code / yes_sub_title / no_sub_title / status /
      price / cumulative_volume / yes_bid / yes_bid_size / yes_ask / yes_ask_size /
      no_bid / no_bid_size / no_ask / no_ask_size / last_trade_time / volume_24h / open_interest

备注：快照只返回买卖一档，多档深度盘口用 get_event_contract_order_book.py
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
    print_display_df,
    assert_event_contract_support,
)


def get_event_contract_snapshot(code_list, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        if isinstance(code_list, str):
            code_list = [code_list]

        ret, data = ctx.get_event_contract_snapshot(code_list)
        check_ret(ret, data, ctx, "获取预测市场快照")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("预测市场快照")
            print("=" * 70)
            cols = [c for c in ['code', 'name', 'status', 'price', 'cumulative_volume',
                                'yes_bid', 'yes_ask', 'no_bid', 'no_ask',
                                'last_trade_time', 'volume_24h', 'open_interest']
                    if c in data.columns]
            print_display_df(data[cols], max_colwidth=30)
            print(f"\n共 {len(data)} 个合约")
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
    parser = argparse.ArgumentParser(description="获取预测市场快照（无需订阅）")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.KXODIMATCH-26JUL140600INDENG-IND")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_snapshot(args.codes, args.output_json)
