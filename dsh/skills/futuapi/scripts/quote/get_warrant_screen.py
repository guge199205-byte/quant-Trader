#!/usr/bin/env python3
"""
筛选窝轮 V2（get_warrant_screen）— 协议号 3254

返回三元组：data = (last_page, all_count, DataFrame)
DataFrame 共 43 列：
  stock_id / stock_owner / issuer_id / warrant_type / strike_price / maturity_date /
  last_trade_date / conversion_ratio / last_close_price / recovery_price / stock_owner_price /
  current_price / volume / turnover / sell_vol / buy_vol / sell_price / buy_price /
  street_rate / high_price / low_price / implied_volatility / delta / status / street_rate_new /
  score / premium / leverage / effective_leverage / break_even_point / ipop / amplitude /
  fx_score / ipo_time / street_vol / lot_size / issue_size / ipo_price / upper_strike_price /
  lower_strike_price / iw_price_status / sensitivity / price_recovery_ratio

接口限制：
- 必传 warrant_market：HK=1、SG=4、MY=15
- only_count=True 时返回的 DataFrame 为空，仅 all_count 有效
- 每页最多 200
- add_interval_filter 中 min_val/max_val 都不传则该条件无效（不报错）
- field_id 来自 WarrantField；STOCK_OWNER(5) 既支持 stock_id(int) 也支持 code(str, 如 "HK.00700")

WarrantField 常用字段（直接传 enum 名或数字）：
  ISSUER_ID=4  STOCK_OWNER=5  WARRANT_TYPE=6  CURRENT_PRICE=8
  STREET_RATIO=9  VOLUME=10  LEVERAGE_RATIO=16  STATUS=19  EFFECTIVE_LEVERAGE=23

WarrantType（int）：CALL=1, PUT=2, BULL=3, BEAR=4, IW=5（界内证）
WarrantStatus（int）：0=正常 1=终止交易 2=待上市

数值单位：lower/upper 直接传原始值，OpenD 负责倍率换算。

JSON 配置：
{
  "interval_filters": [
    {"field_id": "CURRENT_PRICE", "min_val": 0.1, "max_val": 5.0,
     "min_included": true, "max_included": true}
  ],
  "choice_filters": [
    {"field_id": "WARRANT_TYPE", "choices": ["CALL", "PUT"]},
    {"field_id": "STOCK_OWNER", "choices": ["HK.00700"]}
  ],
  "sorts": [
    {"field_id": "EFFECTIVE_LEVERAGE", "desc": true}
  ]
}

用法：
  # 列出某市场全部窝轮
  python get_warrant_screen.py --market HK

  # 仅统计数量
  python get_warrant_screen.py --market HK --only-count

  # 简易过滤
  python get_warrant_screen.py --market HK --stock-owner HK.00700 --warrant-type CALL

  # 复杂条件
  python get_warrant_screen.py --market HK --config config.json
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


def _build_request(market_str, only_count, is_delay, page_from, page_count, spec, simple_args):
    try:
        from futu import WarrantScreenRequest
        from futu.quote.stock_screen_const import WarrantMarket, WarrantField, WarrantType
    except ImportError as e:
        print(f"错误: 当前 futu-api 未提供 WarrantScreenRequest 或相关枚举（{e}）。请升级 SDK。")
        sys.exit(1)

    market_enum = _resolve(WarrantMarket, market_str.upper())
    if market_enum is None:
        raise ValueError(f"不支持的 warrant_market: {market_str}（支持 HK/SG/MY）")
    req = WarrantScreenRequest(warrant_market=market_enum)
    req.is_delay = is_delay
    req.only_count = only_count
    req.page_from = page_from
    req.page_count = page_count

    # 简易 CLI 入参 -> filter
    if simple_args.get("stock_owner"):
        req.add_choice_filter(field_id=int(WarrantField.STOCK_OWNER),
                              choices=[simple_args["stock_owner"]])
    if simple_args.get("warrant_type"):
        wt_val = _resolve(WarrantType, simple_args["warrant_type"].upper()
                          if isinstance(simple_args["warrant_type"], str)
                          else simple_args["warrant_type"])
        req.add_choice_filter(field_id=int(WarrantField.WARRANT_TYPE), choices=[int(wt_val)])
    if simple_args.get("issuer_id") is not None:
        req.add_choice_filter(field_id=int(WarrantField.ISSUER_ID),
                              choices=[int(simple_args["issuer_id"])])
    if simple_args.get("min_price") is not None or simple_args.get("max_price") is not None:
        req.add_interval_filter(field_id=int(WarrantField.CURRENT_PRICE),
                                min_val=simple_args.get("min_price"),
                                max_val=simple_args.get("max_price"))
    if simple_args.get("min_volume") is not None:
        req.add_interval_filter(field_id=int(WarrantField.VOLUME),
                                min_val=simple_args["min_volume"])

    # JSON 配置
    for f in (spec.get("interval_filters") or []):
        req.add_interval_filter(
            field_id=_resolve(WarrantField, f["field_id"]),
            min_val=f.get("min_val"),
            max_val=f.get("max_val"),
            min_included=f.get("min_included", True),
            max_included=f.get("max_included", True),
        )
    for f in (spec.get("choice_filters") or []):
        field_int = _resolve(WarrantField, f["field_id"])
        choices = []
        for c in (f.get("choices") or []):
            if isinstance(c, str):
                # WarrantField=6(WARRANT_TYPE) 时尝试解析为 WarrantType
                if field_int == int(WarrantField.WARRANT_TYPE) and hasattr(WarrantType, c):
                    choices.append(int(getattr(WarrantType, c)))
                else:
                    choices.append(c)
            else:
                choices.append(int(c))
        req.add_choice_filter(field_id=field_int, choices=choices)
    for s in (spec.get("sorts") or []):
        req.add_sort(field_id=_resolve(WarrantField, s["field_id"]),
                     desc=s.get("desc", False))
    return req


def get_warrant_screen(market, only_count, is_delay, page_from, page_count,
                       config_path, output_json, **simple_args):
    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        if not hasattr(ctx, "get_warrant_screen"):
            print("错误: 当前 OpenD/SDK 未提供 get_warrant_screen，请升级到支持该接口的版本")
            sys.exit(1)

        req = _build_request(market, only_count, is_delay, page_from, page_count, spec, simple_args)
        ret, data = ctx.get_warrant_screen(req)
        check_ret(ret, data, ctx, "筛选窝轮 V2")
        last_page, all_count, df = data

        if only_count:
            payload = {"last_page": bool(last_page), "all_count": int(all_count)}
            if output_json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"all_count = {all_count}, last_page = {last_page}")
            return

        if is_empty(df):
            if output_json:
                print(json.dumps({"market": market, "last_page": bool(last_page),
                                  "all_count": int(all_count), "data": []}))
            else:
                print(f"无数据 (all_count={all_count})")
            return

        if output_json:
            print(json.dumps({"market": market, "last_page": bool(last_page),
                              "all_count": int(all_count),
                              "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"筛选窝轮 V2 结果 - 市场 {market}（共 {all_count} 条，last_page={last_page}）")
            print("=" * 70)
            print(df)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="筛选窝轮 V2 (get_warrant_screen)")
    parser.add_argument("--market", required=True, choices=["HK", "SG", "MY"], help="窝轮市场（必填）")
    parser.add_argument("--only-count", action="store_true", help="仅返回 all_count，不返回数据")
    parser.add_argument("--is-delay", action="store_true", help="使用延时行情")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-count", type=int, default=200)
    parser.add_argument("--stock-owner", help="正股代码，如 HK.00700")
    parser.add_argument("--warrant-type", help="CALL / PUT / BULL / BEAR / IW")
    parser.add_argument("--issuer-id", type=int, help="发行商 ID")
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-volume", type=float)
    parser.add_argument("--config", help="JSON 配置（interval_filters/choice_filters/sorts）")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    get_warrant_screen(
        market=args.market,
        only_count=args.only_count,
        is_delay=args.is_delay,
        page_from=args.page_from,
        page_count=args.page_count,
        config_path=args.config,
        output_json=args.output_json,
        stock_owner=args.stock_owner,
        warrant_type=args.warrant_type,
        issuer_id=args.issuer_id,
        min_price=args.min_price,
        max_price=args.max_price,
        min_volume=args.min_volume,
    )
