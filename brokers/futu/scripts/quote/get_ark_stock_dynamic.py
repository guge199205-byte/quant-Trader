#!/usr/bin/env python3
"""
获取 ARK 个股交易动态

功能：获取指定股票的 ARK 交易动态数据
用法：python get_ark_stock_dynamic.py --code US.TSLA [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --code: 股票代码（如 US.TSLA）

返回说明：
- 返回 dict，包含该股票的 ARK 交易动态信息
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close


def get_ark_stock_dynamic(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_ark_stock_dynamic(security=code)
        check_ret(ret, data, ctx, "获取ARK个股交易动态")

        if output_json:
            print(json.dumps(data, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"ARK 个股交易动态 - {code}")
            print("=" * 70)
            for k, v in data.items():
                print(f"  {k}: {v}")
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
    parser = argparse.ArgumentParser(description="获取 ARK 个股交易动态")
    parser.add_argument("--code", required=True, help="股票代码（如 US.TSLA）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_ark_stock_dynamic(args.code, output_json=args.output_json)
