#!/usr/bin/env python3
"""
获取期权策略

功能：按期权策略类型查询组合腿对应的期权链数据
用法：python get_option_strategy.py HK.00700 STRADDLE 2026-05-22
      python get_option_strategy.py HK.00700 SPREAD 2026-05-22 --spread 10.0

接口限制：
- 每 30 秒内最多请求 30 次
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
)


def get_option_strategy(code, option_strategy, expire_time, spread=None,
                        far_expire_time=None, index_option_type=None,
                        option_type=None, strike_price=None, output_json=False):
    from futu import OptionStrategyType, IndexOptionType, OptionType
    ctx = None
    try:
        ctx = create_quote_context()

        strategy_enum = getattr(OptionStrategyType, option_strategy, None)
        if strategy_enum is None:
            raise ValueError(f"未知期权策略类型: {option_strategy}")

        kwargs = {}
        if spread is not None:
            kwargs["spread"] = spread
        if far_expire_time:
            kwargs["far_expire_time"] = far_expire_time
        if index_option_type:
            kwargs["index_option_type"] = getattr(IndexOptionType, index_option_type, IndexOptionType.NORMAL)
        if option_type:
            kwargs["option_type"] = getattr(OptionType, option_type, OptionType.ALL)
        if strike_price is not None:
            kwargs["strike_price"] = strike_price

        ret, data = ctx.get_option_strategy(code, strategy_enum, expire_time, **kwargs)
        check_ret(ret, data, ctx, "获取期权策略")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": []}))
            else:
                print("无期权策略数据")
            return

        if output_json:
            records = df_to_records(data)
            for r in records:
                if "legs" in r:
                    r["legs"] = [str(leg) for leg in r["legs"]]
            print(json.dumps({"code": code, "data": records}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"期权策略 - {code}  策略: {option_strategy}  到期日: {expire_time}")
            print("=" * 70)
            cols = [c for c in ["code", "name", "option_strategy", "stock_owner", "legs"] if c in data.columns]
            print(data[cols].to_string(index=False))
            print(f"\n共 {len(data)} 条记录")
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
    parser = argparse.ArgumentParser(description="获取期权策略组合腿列表")
    parser.add_argument("code", help="标的股票代码，如 HK.00700 或 US.AAPL")
    parser.add_argument("option_strategy", help="期权策略类型，如 STRADDLE / SPREAD / STRANGLE")
    parser.add_argument("expire_time", help="到期日，格式 yyyy-MM-dd")
    parser.add_argument("--spread", type=float, default=None, help="价差（垂直策略等必传）")
    parser.add_argument("--far-expire-time", default=None, dest="far_expire_time", help="远端到期日，格式 yyyy-MM-dd")
    parser.add_argument("--index-option-type", default=None, dest="index_option_type", help="指数期权类型，如 NORMAL")
    parser.add_argument("--option-type", default=None, dest="option_type", help="看涨/看跌，如 CALL / PUT / ALL")
    parser.add_argument("--strike-price", type=float, default=None, dest="strike_price", help="行权价")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_strategy(
        args.code, args.option_strategy, args.expire_time,
        spread=args.spread, far_expire_time=args.far_expire_time,
        index_option_type=args.index_option_type, option_type=args.option_type,
        strike_price=args.strike_price, output_json=args.output_json,
    )
