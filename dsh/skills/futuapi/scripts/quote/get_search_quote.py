#!/usr/bin/env python3
"""
搜索行情标的

功能：按关键词搜索股票、ETF、板块等行情标的
用法：python get_search_quote.py aapl [--max-count 10] [--json]

接口限制：
- 每 30 秒内最多请求 10 次

参数说明：
- keyword: 搜索词（必填）
- --max-count: 最大返回条数（默认 10，最大 100）

返回字段：
- market: 市场类型
- code: 股票代码
- name: 股票名称
- sec_type: 股票类型（STOCK/ETF/PLATE 等）
- is_watched: 是否已在自选
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
    print_display_df,
)


def get_search_quote(keyword, max_count=10, output_json=False):
    if not keyword or not str(keyword).strip():
        raise ValueError("搜索词 keyword 不能为空")
    max_count = max(1, min(int(max_count), 100))

    ctx = None
    try:
        ctx = create_quote_context()
        if not hasattr(ctx, "get_search_quote"):
            raise RuntimeError("当前 OpenD/SDK 未提供 get_search_quote，请升级到支持该接口的版本")

        ret, data = ctx.get_search_quote(keyword.strip(), max_count)
        check_ret(ret, data, ctx, "搜索行情标的")

        if is_empty(data):
            if output_json:
                print(json.dumps({"keyword": keyword, "data": []}, ensure_ascii=False))
            else:
                print("无匹配结果")
            return

        records = df_to_records(data)
        if output_json:
            print(json.dumps({"keyword": keyword, "count": len(records), "data": records}, ensure_ascii=False))
        else:
            print("=" * 90)
            print(f"搜索行情标的: {keyword} (共 {len(records)} 条)")
            print("=" * 90)
            print_display_df(data)
            print("=" * 90)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="搜索行情标的")
    parser.add_argument("keyword", help="搜索词")
    parser.add_argument("--max-count", type=int, default=10, help="最大返回条数（默认 10，最大 100）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_search_quote(args.keyword, args.max_count, args.output_json)
