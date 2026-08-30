#!/usr/bin/env python3
"""
获取盈利超预期排名

功能：获取指定市场的盈利超预期排名数据
用法：python get_earnings_beat_rank.py --market US --beat-type EPS [--count 30] [--term ALL] [--sort-field BEAT_RATIO] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US/SG/JP）
- --beat-type: 超预期类型（EPS/REVENUE/EBIT）
- --count: 返回数量，范围 [1,300]，默认 30
- --term: 时间范围（LATEST/LATEST_QUARTER/LATEST_HALF/LATEST_ANNUAL/ALL）
- --sort-field: 排序字段（BEAT_RATIO/EARNING_DAY_CHG/RELEASED_DATE/ACTUAL/ESTIMATE/YOY/YOY_GROWTH/PE_TTM/DIVIDENDS_TTM/PRICE/CHANGE_RATE）
- --config: JSON 筛选配置文件路径（EarningsBeatRankFilter）

返回字段说明：
- security, name, industry, cur_price, last_close_price, change_rate, market_cap
- pe_ttm, dividends_ttm, released_date, beat_ratio, actual, estimate
- yoy, yoy_growth, earning_day_chg, term, detail_post_period
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
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
}

BEAT_TYPE_MAP = {
    "EPS": futu.BeatType.EPS,
    "REVENUE": futu.BeatType.REVENUE,
    "EBIT": futu.BeatType.EBIT,
}

TERM_MAP = {
    "LATEST": futu.BeatTerm.LATEST,
    "LATEST_QUARTER": futu.BeatTerm.LATEST_QUARTER,
    "LATEST_HALF": futu.BeatTerm.LATEST_HALF,
    "LATEST_ANNUAL": futu.BeatTerm.LATEST_ANNUAL,
    "ALL": futu.BeatTerm.ALL,
}

SORT_FIELD_MAP = {
    "BEAT_RATIO": futu.EarningsBeatSortField.BEAT_RATIO,
    "EARNING_DAY_CHG": futu.EarningsBeatSortField.EARNING_DAY_CHG,
    "RELEASED_DATE": futu.EarningsBeatSortField.RELEASED_DATE,
    "ACTUAL": futu.EarningsBeatSortField.ACTUAL,
    "ESTIMATE": futu.EarningsBeatSortField.ESTIMATE,
    "YOY": futu.EarningsBeatSortField.YOY,
    "YOY_GROWTH": futu.EarningsBeatSortField.YOY_GROWTH,
    "PE_TTM": futu.EarningsBeatSortField.PE_TTM,
    "DIVIDENDS_TTM": futu.EarningsBeatSortField.DIVIDENDS_TTM,
    "PRICE": futu.EarningsBeatSortField.PRICE,
    "CHANGE_RATE": futu.EarningsBeatSortField.CHANGE_RATE,
}


def _build_filters(config_path):
    from futu import EarningsBeatRankFilter, EarningsBeatIndicatorType
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(EarningsBeatIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        filters.append(EarningsBeatRankFilter(**kwargs))
    return filters


def get_earnings_beat_rank(market_str, beat_type_str, count=30, term=None,
                           sort_field=None, config_path=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    beat_type = BEAT_TYPE_MAP.get(beat_type_str.upper())
    if beat_type is None:
        raise ValueError(f"不支持的超预期类型: {beat_type_str}，可选: {list(BEAT_TYPE_MAP.keys())}")

    term_enum = TERM_MAP.get(term.upper()) if term else None
    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_earnings_beat_rank(market, beat_type, count=count,
                                               term=term_enum, filter_list=filter_list,
                                               sort_field=sort_field_enum)
        check_ret(ret, data, ctx, "获取盈利超预期排名")

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
            print(f"盈利超预期排名 - {market_str} {beat_type_str}（共 {all_count} 条）")
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
    parser = argparse.ArgumentParser(description="获取盈利超预期排名")
    parser.add_argument("--market", required=True, choices=["HK", "US", "SG", "JP"],
                        help="市场")
    parser.add_argument("--beat-type", required=True, choices=["EPS", "REVENUE", "EBIT"],
                        help="超预期类型")
    parser.add_argument("--count", type=int, default=30, help="返回数量，范围 [1,300]")
    parser.add_argument("--term",
                        choices=["LATEST", "LATEST_QUARTER", "LATEST_HALF", "LATEST_ANNUAL", "ALL"],
                        default=None, help="时间范围")
    parser.add_argument("--sort-field",
                        choices=["BEAT_RATIO", "EARNING_DAY_CHG", "RELEASED_DATE", "ACTUAL",
                                 "ESTIMATE", "YOY", "YOY_GROWTH", "PE_TTM", "DIVIDENDS_TTM",
                                 "PRICE", "CHANGE_RATE"],
                        default=None, help="排序字段")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_earnings_beat_rank(args.market, args.beat_type, count=args.count,
                           term=args.term, sort_field=args.sort_field,
                           config_path=args.config, output_json=args.output_json)
