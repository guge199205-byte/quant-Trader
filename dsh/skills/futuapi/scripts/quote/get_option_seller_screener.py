#!/usr/bin/env python3
"""
获取期权卖方策略列表（get_option_seller_screener）

功能：获取期权卖方筛选列表，支持 Covered Call 和 Cash Secured Put 两种策略，可按多维度筛选与排序。
用法：
  python get_option_seller_screener.py --market US_SECURITY --seller-type COVERED_CALL --sort-type ANNUALIZED_RETURN
  python get_option_seller_screener.py --market US_SECURITY --seller-type CASH_SECURED_PUT --config filters.json

JSON 配置示例（filters.json）：
{
  "filters": [
    {"indicator_type": "OWNER_LIST", "security_list": ["US.TSLA", "US.AAPL"]},
    {"indicator_type": "ANNUALIZED_RETURN", "interval_min": 20.0},
    {"indicator_type": "LEFT_DAYS", "interval_min": 1, "interval_max": 30},
    {"indicator_type": "STOCK_CATEGORY", "value_list": ["ETF"]}
  ]
}

接口限制：
- 支持 4 种排序字段 + 26 种筛选因子（标的级13种+期权级13种）
- 无分页
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


def _resolve_seller_type(seller_str):
    from futu import SellerType
    key = str(seller_str).strip().upper()
    if hasattr(SellerType, key):
        return getattr(SellerType, key)
    raise ValueError(f"无效的 SellerType: {seller_str}，可选值: COVERED_CALL, CASH_SECURED_PUT")


def _resolve_sort_type(sort_str):
    if not sort_str:
        return None
    from futu import SellerSortType
    key = str(sort_str).strip().upper()
    if hasattr(SellerSortType, key):
        return getattr(SellerSortType, key)
    raise ValueError(f"无效的 SellerSortType: {sort_str}")


def _build_filters(spec):
    if not spec or not spec.get("filters"):
        return None
    from futu import SellerIndicatorType, StockCategory
    from futu.quote.quote_option_event_info import SellerFilter

    filters = []
    for f in spec["filters"]:
        indicator_str = f.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        indicator_type = getattr(SellerIndicatorType, str(indicator_str).upper())

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

        filters.append(SellerFilter(**kwargs))
    return filters


def get_option_seller_screener(market, seller_type, sort_type=None, is_asc=None,
                                config_path=None, output_json=False):
    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        option_market = _resolve_option_market(market)
        seller_t = _resolve_seller_type(seller_type)
        sort_t = _resolve_sort_type(sort_type)
        filter_list = _build_filters(spec)

        kwargs = {
            "market": option_market,
            "seller_type": seller_t,
        }
        if sort_t is not None:
            kwargs["sort_type"] = sort_t
        if is_asc is not None:
            kwargs["is_asc"] = is_asc
        if filter_list:
            kwargs["filter_list"] = filter_list

        ret, data = ctx.get_option_seller_screener(**kwargs)
        check_ret(ret, data, ctx, "获取期权卖方策略列表")

        if is_empty(data):
            if output_json:
                print(json.dumps({"market": market, "seller_type": seller_type, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({
                "market": market,
                "seller_type": seller_type,
                "count": len(data),
                "data": df_to_records(data),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权卖方策略列表 - market={market} seller_type={seller_type} 共 {len(data)} 条")
            print("=" * 70)
            print(data.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权卖方策略列表 (get_option_seller_screener)")
    parser.add_argument("--market", required=True, help="期权市场: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX")
    parser.add_argument("--seller-type", required=True, help="卖方策略: COVERED_CALL, CASH_SECURED_PUT")
    parser.add_argument("--sort-type", help="排序类型: ANNUALIZED_RETURN, INTERVAL_RETURN, ITM_PROBABILITY, PREMIUM")
    parser.add_argument("--asc", action="store_true", dest="is_asc", help="升序排列")
    parser.add_argument("--config", help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_seller_screener(args.market, args.seller_type, args.sort_type,
                                args.is_asc if args.is_asc else None, args.config, args.output_json)
