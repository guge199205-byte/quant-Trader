#!/usr/bin/env python3
"""
获取板块关联产业链

功能：根据板块 ID 获取关联的产业链列表
用法：python get_industrial_chain_by_plate.py --plate-id 123 [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --plate-id: 产业板块 ID（必填）

返回字段说明：
- 返回 list[dict]，每个 dict 包含: chain_id, chain_type, name, market_cap, stocks_num
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close

import futu


def get_industrial_chain_by_plate(plate_id, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_industrial_chain_by_plate(plate_id=plate_id)
        check_ret(ret, data, ctx, "获取板块关联产业链")

        if output_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            if not data:
                print("无数据")
            else:
                for item in data:
                    print(json.dumps(item, ensure_ascii=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取板块关联产业链")
    parser.add_argument("--plate-id", type=int, required=True, help="产业板块 ID")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_industrial_chain_by_plate(args.plate_id, output_json=args.output_json)
