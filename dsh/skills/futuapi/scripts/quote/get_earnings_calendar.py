#!/usr/bin/env python3
"""
获取财报日历

功能：获取指定市场的财报日历数据
用法：python get_earnings_calendar.py --market US [--sort-type HOT] [--begin-date 2026-06-23] [--end-date 2026-06-25] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US/SH/SZ/SG/JP/AU/CA）
- --sort-type: 排序类型（HOT/MARKET_CAP/OPTION_VOLUME/IV/IV_RANK/IV_PERCENTILE/RT_MARKET_CAP）
- --begin-date: 开始日期（yyyy-MM-dd）
- --end-date: 结束日期（yyyy-MM-dd）
- --config: JSON 筛选配置文件路径（EarningsCalendarFilter）

返回字段说明：
- security, name, earnings_date, earnings_timestamp, pub_type, period_text
- eps_actual, eps_predict, revenue_actual, revenue_predict
- ebit_actual, ebit_predict, option_volume, iv, iv_rank, iv_percentile
- market_cap, price
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
    "SH": futu.Market.SH,
    "SZ": futu.Market.SZ,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
}

SORT_MAP = {
    "HOT": futu.EarningsCalendarSortType.HOT,
    "MARKET_CAP": futu.EarningsCalendarSortType.MARKET_CAP,
    "OPTION_VOLUME": futu.EarningsCalendarSortType.OPTION_VOLUME,
    "IV": futu.EarningsCalendarSortType.IV,
    "IV_RANK": futu.EarningsCalendarSortType.IV_RANK,
    "IV_PERCENTILE": futu.EarningsCalendarSortType.IV_PERCENTILE,
    "RT_MARKET_CAP": futu.EarningsCalendarSortType.RT_MARKET_CAP,
}


def _build_filters(config_path):
    from futu import EarningsCalendarFilter, EarningsCalendarIndicatorType
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(EarningsCalendarIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        if "min_inclusive" in s:
            kwargs["min_inclusive"] = s["min_inclusive"]
        if "max_inclusive" in s:
            kwargs["max_inclusive"] = s["max_inclusive"]
        if "value_list" in s:
            vl = []
            for v in s["value_list"]:
                for cls_name in ["EarningsCalendarPubType", "EarningsCalendarEstimateType", "EarningsCalendarStockListType"]:
                    cls = getattr(futu, cls_name, None)
                    if cls and hasattr(cls, v.upper()):
                        vl.append(getattr(cls, v.upper()))
                        break
                else:
                    vl.append(v)
            kwargs["value_list"] = vl
        filters.append(EarningsCalendarFilter(**kwargs))
    return filters


def get_earnings_calendar(market_str, sort_type=None, begin_date=None, end_date=None,
                          config_path=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    sort_type_enum = SORT_MAP.get(sort_type.upper()) if sort_type else None
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_earnings_calendar(market, sort_type=sort_type_enum,
                                              begin_date=begin_date, end_date=end_date,
                                              filter_list=filter_list)
        check_ret(ret, data, ctx, "获取财报日历")

        df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"market": market_str, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"market": market_str, "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"财报日历 - {market_str}（共 {len(df)} 条）")
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
    parser = argparse.ArgumentParser(description="获取财报日历")
    parser.add_argument("--market", required=True,
                        choices=["HK", "US", "SH", "SZ", "SG", "JP", "AU", "CA"],
                        help="市场")
    parser.add_argument("--sort-type",
                        choices=["HOT", "MARKET_CAP", "OPTION_VOLUME", "IV", "IV_RANK", "IV_PERCENTILE", "RT_MARKET_CAP"],
                        default=None, help="排序类型")
    parser.add_argument("--begin-date", default=None, help="开始日期（yyyy-MM-dd）")
    parser.add_argument("--end-date", default=None, help="结束日期（yyyy-MM-dd）")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_earnings_calendar(args.market, sort_type=args.sort_type,
                          begin_date=args.begin_date, end_date=args.end_date,
                          config_path=args.config, output_json=args.output_json)
