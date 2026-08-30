#!/usr/bin/env python3
"""
获取期权市场统计（get_option_market_statistic）

功能：获取期权市场统计数据（成交量/持仓量），按交易日粒度返回看涨、看跌及合计值，支持分页拉取。
用法：python get_option_market_statistic.py --market US_SECURITY --data-type VOLUME --begin 2024-01-01 --end 2024-06-01

接口限制：
- begin_time 与 end_time 跨度不超过一年；不传时默认取近一年数据
- 分页通过 page_req_key 实现，自动翻页拉取全部数据
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def _resolve_option_market(market_str):
    from futu import OptionMarket
    key = str(market_str).strip().upper()
    if hasattr(OptionMarket, key):
        return getattr(OptionMarket, key)
    raise ValueError(f"无效的 OptionMarket: {market_str}，可选值: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX")


def _resolve_data_type(data_type_str):
    from futu import OptionStatisticDataType
    key = str(data_type_str).strip().upper()
    if hasattr(OptionStatisticDataType, key):
        return getattr(OptionStatisticDataType, key)
    raise ValueError(f"无效的 OptionStatisticDataType: {data_type_str}，可选值: VOLUME, OPEN_INTEREST")


def get_option_market_statistic(market, data_type, begin_time=None, end_time=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        option_market = _resolve_option_market(market)
        stat_data_type = _resolve_data_type(data_type)

        all_rows = []
        page_req_key = None
        while True:
            ret, data, page_req_key = ctx.get_option_market_statistic(
                option_market=option_market,
                data_type=stat_data_type,
                begin_time=begin_time,
                end_time=end_time,
                page_req_key=page_req_key,
            )
            check_ret(ret, data, ctx, "获取期权市场统计")
            if not is_empty(data):
                all_rows.append(data)
            if page_req_key is None:
                break

        if not all_rows:
            if output_json:
                print(json.dumps({"market": market, "data_type": data_type, "data": []}))
            else:
                print("无数据")
            return

        import pandas as pd
        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({
                "market": market,
                "data_type": data_type,
                "count": len(df),
                "data": df_to_records(df),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权市场统计 - market={market} data_type={data_type} 共 {len(df)} 条")
            print("=" * 70)
            print(df.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权市场统计 (get_option_market_statistic)")
    parser.add_argument("--market", required=True,
                        help="期权市场: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX")
    parser.add_argument("--data-type", required=True,
                        help="数据类型: VOLUME(成交量), OPEN_INTEREST(持仓量)")
    parser.add_argument("--begin", dest="begin_time", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", dest="end_time", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_market_statistic(args.market, args.data_type, args.begin_time, args.end_time, args.output_json)
