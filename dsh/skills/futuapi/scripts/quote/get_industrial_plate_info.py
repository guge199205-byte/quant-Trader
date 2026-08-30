#!/usr/bin/env python3
"""
获取产业板块信息

功能：根据板块 ID 获取产业板块详细信息
用法：python get_industrial_plate_info.py --plate-id 123 [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --plate-id: 产业板块 ID（必填）

返回字段说明：
- 返回 dict，包含: plate_id, summary
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close

import futu


def get_industrial_plate_info(plate_id, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_industrial_plate_info(plate_id=plate_id)
        check_ret(ret, data, ctx, "获取产业板块信息")

        if output_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取产业板块信息")
    parser.add_argument("--plate-id", type=int, required=True, help="产业板块 ID")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_industrial_plate_info(args.plate_id, output_json=args.output_json)
