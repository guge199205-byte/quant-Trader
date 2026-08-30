#!/usr/bin/env python3
"""
获取期权策略有效价差

功能：获取指定期权策略在当前标的、到期日条件下可用的有效价差列表
用法：python get_option_strategy_spread.py HK.00700 STRANGLE 2026-05-22

接口限制：
- 每 30 秒内最多请求 30 次
- option_strategy 仅支持 SPREAD / STRANGLE / COLLAR / BUTTERFLY / CONDOR / IRON_BUTTERFLY / IRON_CONDOR / DIAGONAL_SPREAD
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


def get_option_strategy_spread(code, option_strategy, expire_time,
                                far_expire_time=None, index_option_type=None,
                                output_json=False):
    from futu import OptionStrategyType, IndexOptionType
    ctx = None
    try:
        ctx = create_quote_context()

        strategy_enum = getattr(OptionStrategyType, option_strategy, None)
        if strategy_enum is None:
            raise ValueError(f"未知期权策略类型: {option_strategy}")

        kwargs = {}
        if far_expire_time:
            kwargs["far_expire_time"] = far_expire_time
        if index_option_type:
            kwargs["index_option_type"] = getattr(IndexOptionType, index_option_type, IndexOptionType.NORMAL)

        ret, data = ctx.get_option_strategy_spread(code, strategy_enum, expire_time, **kwargs)
        check_ret(ret, data, ctx, "获取期权策略有效价差")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": []}))
            else:
                print("无有效价差数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"有效价差 - {code}  策略: {option_strategy}  到期日: {expire_time}")
            print("=" * 70)
            print(data.to_string(index=False))
            print(f"\n共 {len(data)} 条价差")
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
    parser = argparse.ArgumentParser(description="获取期权策略有效价差列表")
    parser.add_argument("code", help="标的股票代码，如 HK.00700 或 US.AAPL")
    parser.add_argument("option_strategy", help="期权策略类型，如 STRANGLE / SPREAD / BUTTERFLY")
    parser.add_argument("expire_time", help="到期日，格式 yyyy-MM-dd")
    parser.add_argument("--far-expire-time", default=None, dest="far_expire_time", help="远端到期日，格式 yyyy-MM-dd")
    parser.add_argument("--index-option-type", default=None, dest="index_option_type", help="指数期权类型，如 NORMAL")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_strategy_spread(
        args.code, args.option_strategy, args.expire_time,
        far_expire_time=args.far_expire_time,
        index_option_type=args.index_option_type,
        output_json=args.output_json,
    )
