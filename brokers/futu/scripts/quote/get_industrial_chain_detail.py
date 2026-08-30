#!/usr/bin/env python3
"""
获取产业链详情

功能：根据产业链 ID 获取产业链详情（上中下游结构）
用法：python get_industrial_chain_detail.py --chain-id 123 [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --chain-id: 产业链 ID（必填）

返回字段说明：
- 返回 dict，包含产业链上中下游详细结构
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close

import futu


def get_industrial_chain_detail(chain_id, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_industrial_chain_detail(chain_id=chain_id)
        check_ret(ret, data, ctx, "获取产业链详情")

        if output_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取产业链详情")
    parser.add_argument("--chain-id", type=int, required=True, help="产业链 ID")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_industrial_chain_detail(args.chain_id, output_json=args.output_json)
