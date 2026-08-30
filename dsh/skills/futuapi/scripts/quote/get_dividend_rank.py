#!/usr/bin/env python3
"""
获取股息排行

功能：获取指定市场的股息排行数据
用法：python get_dividend_rank.py --market US --rank-type HIGH_YIELD [--count 10] [--sort-field DIVIDEND_YIELD_TTM] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US/MY/SG/JP）
- --rank-type: 排行类型（HIGH_YIELD/DIVIDEND_GROWTH）
- --count: 返回数量，范围 [1,300]，默认 10
- --sort-field: 排序字段（DIVIDEND_YIELD_TTM/AVG_DIVIDEND_YIELD_5Y/DISTRIBUTION_FREQUENCY/DIVIDEND_GROW_YEAR/DIVIDENDS_TTM/PAYOUT_RATIO_LFY/PRICE/MARKET_CAP/CHANGE_RATE/CHANGE_AMOUNT）
- --config: JSON 筛选配置文件路径（DividendRankFilter）

返回字段说明：
- security, name, industry, cur_price, change_rate, change_amount, market_cap
- dividend_yield_ttm, avg_dividend_yield_5y, distribution_frequency, dividend_grow_year
- dividends_ttm, payout_ratio_lfy, next_payable_date
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
    "MY": futu.Market.MY,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
}

RANK_TYPE_MAP = {
    "HIGH_YIELD": futu.DividendRankType.HIGH_YIELD,
    "DIVIDEND_GROWTH": futu.DividendRankType.DIVIDEND_GROWTH,
}

SORT_FIELD_MAP = {
    "DIVIDEND_YIELD_TTM": futu.DividendRankSortField.DIVIDEND_YIELD_TTM,
    "AVG_DIVIDEND_YIELD_5Y": futu.DividendRankSortField.AVG_DIVIDEND_YIELD_5Y,
    "DISTRIBUTION_FREQUENCY": futu.DividendRankSortField.DISTRIBUTION_FREQUENCY,
    "DIVIDEND_GROW_YEAR": futu.DividendRankSortField.DIVIDEND_GROW_YEAR,
    "DIVIDENDS_TTM": futu.DividendRankSortField.DIVIDENDS_TTM,
    "PAYOUT_RATIO_LFY": futu.DividendRankSortField.PAYOUT_RATIO_LFY,
    "PRICE": futu.DividendRankSortField.PRICE,
    "MARKET_CAP": futu.DividendRankSortField.MARKET_CAP,
    "CHANGE_RATE": futu.DividendRankSortField.CHANGE_RATE,
    "CHANGE_AMOUNT": futu.DividendRankSortField.CHANGE_AMOUNT,
}


def _build_filters(config_path):
    from futu import DividendRankFilter, DividendRankIndicatorType, DistributionFrequency
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(DividendRankIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        if "value_list" in s:
            kwargs["value_list"] = [getattr(DistributionFrequency, v.upper()) for v in s["value_list"]]
        filters.append(DividendRankFilter(**kwargs))
    return filters


def get_dividend_rank(market_str, rank_type_str, count=10, sort_field=None,
                      config_path=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    rank_type = RANK_TYPE_MAP.get(rank_type_str.upper())
    if rank_type is None:
        raise ValueError(f"不支持的排行类型: {rank_type_str}，可选: {list(RANK_TYPE_MAP.keys())}")

    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_dividend_rank(market, rank_type, count=count,
                                          filter_list=filter_list, sort_field=sort_field_enum)
        check_ret(ret, data, ctx, "获取股息排行")

        df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"股息排行 - {market_str} {rank_type_str}")
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
    parser = argparse.ArgumentParser(description="获取股息排行")
    parser.add_argument("--market", required=True, choices=["HK", "US", "MY", "SG", "JP"],
                        help="市场")
    parser.add_argument("--rank-type", required=True, choices=["HIGH_YIELD", "DIVIDEND_GROWTH"],
                        help="排行类型")
    parser.add_argument("--count", type=int, default=10, help="返回数量，范围 [1,300]")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()),
                        default=None, help="排序字段")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_dividend_rank(args.market, args.rank_type, count=args.count,
                      sort_field=args.sort_field, config_path=args.config,
                      output_json=args.output_json)
