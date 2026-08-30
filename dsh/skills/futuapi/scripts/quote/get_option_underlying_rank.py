#!/usr/bin/env python3
"""
获取标的排行（get_option_underlying_rank）

功能：获取期权热门标的排行，按指定维度对期权标的（正股/ETF/指数）进行排名，支持多维度筛选与分页。
用法：
  python get_option_underlying_rank.py --market US_SECURITY --sort-type VOLUME --count 20
  python get_option_underlying_rank.py --market US_SECURITY --sort-type IV --config filters.json

JSON 配置示例（filters.json）：
{
  "filters": [
    {"indicator_type": "STOCK_CATEGORY", "value_list": ["ETF"]},
    {"indicator_type": "IV", "interval_min": 30.0},
    {"indicator_type": "OWNER_LIST", "security_list": ["US.TSLA", "US.AAPL"]}
  ]
}

接口限制：
- count 范围 [1, 200]
- 支持 13 种排序字段 + 13 种筛选因子
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
    raise ValueError(f"无效的 OptionMarket: {market_str}")


def _resolve_sort_type(sort_str):
    from futu import UnderlyingRankSortType
    key = str(sort_str).strip().upper()
    if hasattr(UnderlyingRankSortType, key):
        return getattr(UnderlyingRankSortType, key)
    raise ValueError(f"无效的 UnderlyingRankSortType: {sort_str}")


def _build_filters(spec):
    if not spec or not spec.get("filters"):
        return None
    from futu import UnderlyingRankIndicatorType, StockCategory
    from futu.quote.quote_option_event_info import UnderlyingRankFilter

    filters = []
    for f in spec["filters"]:
        indicator_str = f.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        indicator_type = getattr(UnderlyingRankIndicatorType, str(indicator_str).upper())

        kwargs = {"indicator_type": indicator_type}
        if "value_list" in f:
            vl = f["value_list"]
            if indicator_str.upper() == "STOCK_CATEGORY":
                vl = [getattr(StockCategory, str(v).upper()) if isinstance(v, str) else v for v in vl]
            kwargs["value_list"] = vl
        if "security_list" in f:
            kwargs["security_list"] = f["security_list"]
        if "interval_min" in f:
            kwargs["interval_min"] = f["interval_min"]
        if "interval_max" in f:
            kwargs["interval_max"] = f["interval_max"]
        if "min_inclusive" in f:
            kwargs["min_inclusive"] = f["min_inclusive"]
        if "max_inclusive" in f:
            kwargs["max_inclusive"] = f["max_inclusive"]

        filters.append(UnderlyingRankFilter(**kwargs))
    return filters


def get_option_underlying_rank(market, sort_type, sort_direction=None, count=None,
                                trading_date=None, config_path=None, output_json=False, no_page=True):
    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        option_market = _resolve_option_market(market)
        sort_t = _resolve_sort_type(sort_type)
        filter_list = _build_filters(spec)

        all_rows = []
        page = None
        total_count = 0
        while True:
            ret, data, next_page, all_count = ctx.get_option_underlying_rank(
                option_market=option_market,
                sort_type=sort_t,
                sort_direction=sort_direction,
                count=count,
                trading_date=trading_date,
                filter_list=filter_list,
                page=page,
            )
            check_ret(ret, data, ctx, "获取标的排行")
            total_count = all_count or 0
            if not is_empty(data):
                all_rows.append(data)
            if no_page or not next_page:
                break
            page = next_page

        if not all_rows:
            if output_json:
                print(json.dumps({"market": market, "all_count": 0, "data": []}))
            else:
                print("无数据")
            return

        import pandas as pd
        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({
                "market": market,
                "sort_type": sort_type,
                "all_count": total_count,
                "data": df_to_records(df),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权标的排行 - market={market} sort={sort_type} 共 {total_count} 条")
            print("=" * 70)
            print(df.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取标的排行 (get_option_underlying_rank)")
    parser.add_argument("--market", required=True, help="期权市场: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX")
    parser.add_argument("--sort-type", required=True,
                        help="排序字段: VOLUME, VOLUME_RATIO, OPEN_INTEREST, OPEN_INTEREST_RATIO, PRICE, PRICE_CHANGE, IV, IV_CHANGE, HV, HV_CHANGE, IV_RANK, IV_PERCENTILE, MARKET_CAP")
    parser.add_argument("--sort-direction", type=int, choices=[0, 1], help="0=降序(默认), 1=升序")
    parser.add_argument("--count", type=int, help="每页数量 [1,200]")
    parser.add_argument("--trading-date", help="交易日 YYYY-MM-DD")
    parser.add_argument("--config", help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    parser.add_argument("--no-page", action="store_true", default=True, help="不自动翻页，仅返回第一页（默认）")
    parser.add_argument("--all-pages", action="store_false", dest="no_page", help="自动翻页获取全部数据")
    args = parser.parse_args()
    get_option_underlying_rank(args.market, args.sort_type, args.sort_direction, args.count,
                                args.trading_date, args.config, args.output_json, args.no_page)
