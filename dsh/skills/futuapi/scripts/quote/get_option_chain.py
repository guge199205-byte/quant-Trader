#!/usr/bin/env python3
"""
获取期权链

功能：获取指定正股的期权链数据
用法：python get_option_chain.py HK.00700 --start 2026-06-01 --end 2026-06-30

接口限制：
- 每 30 秒内最多请求 60 次
- **start ~ end 时间跨度不能超过 30 天**，否则返回错误码 -1 "获取期权链接口，请求的时间跨度不能超过30天"；
  如需查询更长区间，请分批调用（先用 get_option_expiration_date 拿到所有到期日，再按需 30 天滑窗）
- 标的仅支持港美正股 / ETF / 港指 / 美指；日股 / 新股 / 马股 / A 股不支持
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


def get_option_chain(code, start=None, end=None, option_type=None, option_cond_type=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end
        if option_type:
            kwargs["option_type"] = option_type
        if option_cond_type:
            kwargs["option_cond_type"] = option_cond_type

        ret, data = ctx.get_option_chain(code, **kwargs)
        check_ret(ret, data, ctx, "获取期权链")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": []}))
            else:
                print("无期权链数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"期权链 - {code}")
            print("=" * 70)
            cols = [c for c in ['code', 'name', 'option_type', 'strike_price',
                                'strike_time', 'last_price'] if c in data.columns]
            print(data[cols].to_string(index=False))
            print(f"\n共 {len(data)} 条记录")
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
    parser = argparse.ArgumentParser(description="获取期权链")
    parser.add_argument("code", help="正股代码，如 HK.00700 或 US.AAPL")
    parser.add_argument("--start", default=None, help="起始日期 yyyy-MM-dd（与 --end 跨度不超过 30 天）")
    parser.add_argument("--end", default=None, help="结束日期 yyyy-MM-dd（与 --start 跨度不超过 30 天）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_chain(args.code, start=args.start, end=args.end, output_json=args.output_json)
