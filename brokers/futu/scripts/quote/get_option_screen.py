#!/usr/bin/env python3
"""
筛选期权（get_option_screen）— 协议号 3253

返回三元组：data = (last_page, all_count, DataFrame)

混合使用「标的属性 (underlying)」+「期权属性 (option)」筛选：
- 后端禁止同组混用 underlying + option，SDK 自动按需开新筛选组
- 默认 AND（开新组）；同 indicator_type 显式 or_with_previous=True 时与上一条件 OR（同组）

请求参数：
- market_categories（必传）— OptMarketCategory 列表：
    US_STOCK=0, US_INDEX=1, US_FUTURE=2, HK_STOCK=3, HK_INDEX=4, JP_STOCK=5, JP_INDEX=6
    （US_FUTURE / JP_STOCK / JP_INDEX 后续支持，目前结果通常为空）
- page_from / page_count

Builder：
- add_underlying_filter(indicator_type, values, lower, upper, plate_list, parent_plate_id, or_with_previous)
- add_option_filter(indicator_type, values, lower, upper, or_with_previous)
- new_filter_group()                     手动开新组（组间 AND，组内 OR）
- add_sort(indicator_type, desc=False)
- add_option_retrieve(indicator_type)    声明返回的期权字段
- add_underlying_retrieve(indicator_type) 声明返回的标的字段（不调用则 underlying dict 不被填充）

数值单位：
- 所有 lower/upper 直接传原始值，OpenD 负责倍率换算
- IV / HV / IV_RANK / IV_PERCENTILE：传百分比原始数（30% → 30.0，**不是 0.3**）
- DELTA / GAMMA / VEGA / THETA / RHO / 各类概率（如 ITM_PROBABILITY）：直接传原始数
- PLATE(103)：传入会报错，禁用
- OptIndicator.PREMIUM(2021)：仅支持 sort / retrieve；作为 filter 会报错
- BUY_BREAK_EVEN_POINT(3023)：已废弃，新代码用 BUY_TO_BEP(3011)

OptUnderlyingIndicator（实测枚举名）：
  STOCK_LIST=101, PLATE=103(禁用), INDEX_LIST=106,
  VOLUME=201, OPEN_INTEREST=202, IV=203, HV=204, IV_RANK=205, IV_PERCENTILE=206,
  IV_CHANGE=207, IV_CHANGE_RATIO=208, IV_HV_RATIO=209, IV_HV_SPREAD=210,
  MARKET_CAP=401, STOCK_PRICE=402, CHANGE_RATIO=403

返回 DataFrame（默认 47 列，含 underlying dict）：
  code / option_name / strike_price / strike_date / option_type / exercise_type /
  expiration_type / in_the_money / left_day / price / mid_price / bid_price / ask_price /
  bid_ask_spread / bid_volume / ask_volume / bid_ask_volume_ratio / change_ratio /
  volume / turnover / open_interest / open_interest_market_cap / vol_oi_ratio / premium /
  implied_volatility / history_volatility / iv_hv_ratio / delta / gamma / vega / theta / rho /
  leverage_ratio / effective_gearing / itm_probability / buy_to_bep / sell_to_bep /
  buy_profit_probability / sell_profit_probability / intrinsic_value_per / time_value_per /
  itm_degree / otm_degree / otm_probability / sell_annualized_return / interval_return /
  underlying

underlying dict（仅在调用 add_underlying_retrieve 后填充，未声明字段为 'N/A'）：
  stock_id / iv / hv / iv_rank / iv_percentile / market_cap / price / change_ratio

JSON 配置示例（CALL OR PUT，IV>30%，按 OPEN_INTEREST 降序）：
{
  "filters": [
    {"kind": "option", "indicator_type": "OPTION_TYPE", "values": [1]},
    {"kind": "option", "indicator_type": "OPTION_TYPE", "values": [2], "or_with_previous": true},
    {"kind": "underlying", "indicator_type": "IV", "lower": 30.0}
  ],
  "sorts": [
    {"indicator_type": "OPEN_INTEREST", "desc": true}
  ],
  "option_retrieves": ["OPTION_TYPE", "STRIKE_PRICE", "OPEN_INTEREST", "IMPLIED_VOLATILITY"],
  "underlying_retrieves": ["STOCK_PRICE", "IV", "MARKET_CAP"]
}

用法：
  python get_option_screen.py --markets US_STOCK HK_STOCK --page-count 50
  python get_option_screen.py --markets US_STOCK --config config.json
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def _resolve(enum_cls, value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and enum_cls is not None and hasattr(enum_cls, value):
        return int(getattr(enum_cls, value))
    return value


def _resolve_market_categories(markets):
    try:
        from futu import OptMarketCategory
    except ImportError:
        return list(markets)
    return [_resolve(OptMarketCategory, m) for m in markets]


def _build_request(markets, page_from, page_count, spec):
    try:
        from futu import OptionScreenRequest, OptUnderlyingIndicator, OptIndicator
    except ImportError:
        print("错误: 当前 futu-api 未提供 OptionScreenRequest，请升级 SDK")
        sys.exit(1)

    market_categories = _resolve_market_categories(markets)
    req = OptionScreenRequest(market_categories=market_categories)
    req.page_from = page_from
    req.page_count = page_count

    for f in (spec.get("filters") or []):
        kind = f.get("kind")
        if kind == "underlying":
            req.add_underlying_filter(
                indicator_type=_resolve(OptUnderlyingIndicator, f["indicator_type"]),
                values=f.get("values"),
                lower=f.get("lower"),
                upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
                plate_list=f.get("plate_list"),
                parent_plate_id=f.get("parent_plate_id"),
                or_with_previous=f.get("or_with_previous", False),
            )
        elif kind == "option":
            req.add_option_filter(
                indicator_type=_resolve(OptIndicator, f["indicator_type"]),
                values=f.get("values"),
                lower=f.get("lower"),
                upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
                or_with_previous=f.get("or_with_previous", False),
            )
        elif kind == "new_group":
            req.new_filter_group()
        else:
            raise ValueError(f"未知 filter kind: {kind}（应为 underlying / option / new_group）")

    for s in (spec.get("sorts") or []):
        req.add_sort(
            indicator_type=_resolve(OptIndicator, s["indicator_type"]),
            desc=s.get("desc", False),
        )

    for r in (spec.get("option_retrieves") or []):
        req.add_option_retrieve(_resolve(OptIndicator, r))
    for r in (spec.get("underlying_retrieves") or []):
        req.add_underlying_retrieve(_resolve(OptUnderlyingIndicator, r))
    return req


def get_option_screen(markets, page_from, page_count, config_path, output_json):
    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        if not hasattr(ctx, "get_option_screen"):
            print("错误: 当前 OpenD/SDK 未提供 get_option_screen，请升级到支持该接口的版本")
            sys.exit(1)

        req = _build_request(markets, page_from, page_count, spec)
        ret, data = ctx.get_option_screen(req)
        check_ret(ret, data, ctx, "筛选期权")
        last_page, all_count, df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"markets": markets, "last_page": bool(last_page),
                                  "all_count": int(all_count), "data": []}))
            else:
                print(f"无数据 (all_count={all_count})")
            return

        if output_json:
            print(json.dumps({"markets": markets, "last_page": bool(last_page),
                              "all_count": int(all_count),
                              "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"筛选期权结果 - markets={markets} 共 {all_count} 条 (last_page={last_page})")
            print("=" * 70)
            print(df)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="筛选期权 (get_option_screen)")
    parser.add_argument("--markets", nargs="+", required=True,
                        help="OptMarketCategory 列表，如 US_STOCK HK_STOCK")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-count", type=int, default=200)
    parser.add_argument("--config", help="JSON 配置（filters/sorts/option_retrieves/underlying_retrieves）")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()
    get_option_screen(args.markets, args.page_from, args.page_count, args.config, args.output_json)
