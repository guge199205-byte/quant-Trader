#!/usr/bin/env python3
"""
获取 FedWatch 目标利率概率

功能：获取 CME FedWatch 各次会议对应的目标利率区间概率
用法：python get_fed_watch_target_rate.py [--json]

接口限制：
- 每 30 秒内最多请求 60 次

返回字段说明：
- meeting_date, target_range, probability
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu


def get_fed_watch_target_rate(output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_fed_watch_target_rate()
        check_ret(ret, data, ctx, "获取FedWatch目标利率")

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
    parser = argparse.ArgumentParser(description="获取 FedWatch 目标利率概率")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_fed_watch_target_rate(output_json=args.output_json)
