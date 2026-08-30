#!/usr/bin/env python3
"""
获取期权快照

功能：根据组合腿列表获取期权实时快照行情（最新价、Greeks 等；不含组合级 bid1/ask1）
用法：python get_option_quote.py '[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0},{"code":"HK.TCH260522C330000","action":"BUY","quantity":1.0}]'

注意：组合摆盘价请用 get_option_strategy_analysis.py，勿用本脚本替代

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


def parse_legs(legs_json):
    from futu import OptionStrategyLeg, StrategyLegAction
    items = json.loads(legs_json)
    legs = []
    for item in items:
        leg = OptionStrategyLeg()
        leg.code = item["code"]
        leg.action = item.get("action", "BUY")
        leg.quantity = float(item.get("quantity", 1.0))
        legs.append(leg)
    return legs


def get_option_quote(legs_json, output_json=False):
    ctx = None
    try:
        legs = parse_legs(legs_json)
        ctx = create_quote_context()
        ret, data = ctx.get_option_quote(legs)
        check_ret(ret, data, ctx, "获取期权快照")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无期权快照数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("期权快照")
            print("=" * 70)
            cols = [c for c in [
                "price", "change_val", "change_rate", "volume", "turnover",
                "high_price", "low_price", "option_type", "strike_price",
                "expire_time", "delta", "gamma", "vega", "theta", "rho",
                "implied_volatility", "open_interest", "prob_of_profit",
                "max_profit", "max_loss",
            ] if c in data.columns]
            print(data[cols].to_string(index=False))
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
    parser = argparse.ArgumentParser(description="获取期权快照行情")
    parser.add_argument(
        "legs",
        help='组合腿列表 JSON，如 \'[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0}]\''
    )
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_quote(args.legs, output_json=args.output_json)
