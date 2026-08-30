#!/usr/bin/env python3
"""
获取宏观指标历史数据

功能：根据指标 ID 获取宏观指标历史数据
用法：python get_macro_indicator_history.py --indicator-id 123 [--time 2026-06-01] [--max-count 100] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --indicator-id: 宏观指标 ID（必填，来自 get_macro_indicator_list 返回）
- --time: 时间节点 yyyy-MM-dd，从该时间往前拉取，默认当前时间
- --max-count: 拉取条数，默认 100，上限 1000

返回字段说明：
- data_time, release_time, value, predict_value, previous_value, unit_type
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu


def get_macro_indicator_history(indicator_id, time=None, max_count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_macro_indicator_history(
            indicator_id=indicator_id, time=time, max_count=max_count
        )
        check_ret(ret, data, ctx, "获取宏观指标历史数据")

        if is_empty(data):
            print("无数据")
            return

        if output_json:
            print(json.dumps({"records": df_to_records(data)}, ensure_ascii=False, indent=2))
        else:
            print(data.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取宏观指标历史数据")
    parser.add_argument("--indicator-id", type=int, required=True, help="宏观指标 ID")
    parser.add_argument("--time", help="时间节点 yyyy-MM-dd")
    parser.add_argument("--max-count", type=int, help="拉取条数，默认 100，上限 1000")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_macro_indicator_history(args.indicator_id, time=args.time, max_count=args.max_count,
                               output_json=args.output_json)
