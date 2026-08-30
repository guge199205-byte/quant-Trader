#!/usr/bin/env python3
"""
期权策略损益分析

功能：对自定义或多腿期权组合进行损益分析；返回组合级 bid1/ask1（摆盘价）、最大盈亏、盈亏平衡点等
用法：python get_option_strategy_analysis.py '[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0},{"code":"HK.TCH260522C330000","action":"BUY","quantity":1.0}]'

Agent 指引：
- 组合期权摆盘价（bid1/ask1）与 place_combo_order / comboorder_tradinginfo_query 的定价应优先使用本脚本
- 禁止对各腿 get_snapshot 后手动加减买卖价

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
    from futu import OptionStrategyLeg
    items = json.loads(legs_json)
    legs = []
    for item in items:
        leg = OptionStrategyLeg()
        leg.code = item["code"]
        leg.action = item.get("action", "BUY")
        leg.quantity = float(item.get("quantity", 1.0))
        legs.append(leg)
    return legs


def get_option_strategy_analysis(legs_json, output_json=False):
    ctx = None
    try:
        legs = parse_legs(legs_json)
        ctx = create_quote_context()
        ret, data = ctx.get_option_strategy_analysis(legs)
        check_ret(ret, data, ctx, "期权策略损益分析")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无损益分析数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("期权策略损益分析")
            print("=" * 70)
            cols = [c for c in [
                "code", "name", "option_strategy",
                "bid1", "ask1", "max_profit", "max_loss",
                "breakeven_points", "prob_of_profit", "delta", "theta",
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
    parser = argparse.ArgumentParser(description="期权策略损益分析")
    parser.add_argument(
        "legs",
        help='组合腿列表 JSON，如 \'[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0}]\''
    )
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_strategy_analysis(args.legs, output_json=args.output_json)
