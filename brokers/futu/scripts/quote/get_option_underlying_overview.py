#!/usr/bin/env python3
"""
获取批量标的最新数据（get_option_underlying_overview）

功能：批量获取期权标的总览数据，包含成交量、持仓量、IV 及多周期 HV 等核心指标的最新快照。
用法：python get_option_underlying_overview.py US.AAPL US.TSLA US.NVDA

接口限制：
- code_list 最多 500 个标的
- 快照接口，返回当前最新数据
- 持仓量数据有 T-1 日延迟
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def _resolve_index_option_type(type_str):
    if not type_str:
        return None
    from futu import IndexOptionType
    key = str(type_str).strip().upper()
    if hasattr(IndexOptionType, key):
        return getattr(IndexOptionType, key)
    return None


def get_option_underlying_overview(code_list, index_option_type=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        iot = _resolve_index_option_type(index_option_type)
        if iot is not None:
            kwargs["index_option_type"] = iot

        ret, data = ctx.get_option_underlying_overview(code_list, **kwargs)
        check_ret(ret, data, ctx, "获取批量标的最新数据")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code_list": code_list, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({
                "code_list": code_list,
                "count": len(data),
                "data": df_to_records(data),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权标的总览 - 共 {len(data)} 条")
            print("=" * 70)
            print(data.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取批量标的最新数据 (get_option_underlying_overview)")
    parser.add_argument("codes", nargs="+", help="标的股票代码列表，如 US.AAPL US.TSLA")
    parser.add_argument("--index-option-type", help="指数期权类型: NORMAL, SMALL")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_underlying_overview(args.codes, args.index_option_type, args.output_json)
