#!/usr/bin/env python3
"""
获取期权标的历史统计（get_option_underlying_his_statistic）

功能：获取期权标的历史统计数据，按交易日返回该标的对应期权的成交量、持仓量及 Put/Call 比率时间序列。
用法：python get_option_underlying_his_statistic.py US.AAPL --begin 2025-01-01 --end 2025-06-01

接口限制：
- begin_time 与 end_time 跨度最多 364 天
- 持仓量数据有 T-1 日延迟
- 分页通过 page_req_key 实现
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


def get_option_underlying_his_statistic(code, index_option_type=None, begin_time=None, end_time=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        iot = _resolve_index_option_type(index_option_type)
        if iot is not None:
            kwargs["index_option_type"] = iot
        if begin_time:
            kwargs["begin_time"] = begin_time
        if end_time:
            kwargs["end_time"] = end_time

        all_rows = []
        page_req_key = None
        while True:
            ret, data, page_req_key = ctx.get_option_underlying_his_statistic(
                code, page_req_key=page_req_key, **kwargs
            )
            check_ret(ret, data, ctx, "获取期权标的历史统计")
            if not is_empty(data):
                all_rows.append(data)
            if page_req_key is None:
                break

        if not all_rows:
            if output_json:
                print(json.dumps({"code": code, "data": []}))
            else:
                print("无数据")
            return

        import pandas as pd
        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({
                "code": code,
                "count": len(df),
                "data": df_to_records(df),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权标的历史统计 - {code} 共 {len(df)} 条")
            print("=" * 70)
            print(df.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权标的历史统计 (get_option_underlying_his_statistic)")
    parser.add_argument("code", help="标的股票代码，如 US.AAPL")
    parser.add_argument("--index-option-type", help="指数期权类型: NORMAL, SMALL")
    parser.add_argument("--begin", dest="begin_time", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", dest="end_time", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_underlying_his_statistic(args.code, args.index_option_type, args.begin_time, args.end_time, args.output_json)
