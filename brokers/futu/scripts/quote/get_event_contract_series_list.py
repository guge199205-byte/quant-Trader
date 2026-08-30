#!/usr/bin/env python3
"""
获取预测市场 Series 列表

功能：按 category/tag 获取 Series 列表（Series 是一组相关 Event 的集合），无需订阅
用法：python get_event_contract_series_list.py --category Sports [--tag Football] [--json]

接口：OpenQuoteContext.get_event_contract_series_list(category=None, tag=None)
返回：DataFrame，字段 series_code / series_name / category / tags / frequency

备注：series_code 可作为 get_event_contract_event_list 的入参
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
    assert_event_contract_support,
)


def get_event_contract_series_list(category=None, tag=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data = ctx.get_event_contract_series_list(category=category, tag=tag)
        check_ret(ret, data, ctx, "获取预测市场 Series 列表")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"预测市场 Series 列表 - {category or '全部分类'} {('/' + tag) if tag else ''}")
            print("=" * 70)
            cols = [c for c in ['series_code', 'series_name', 'category', 'tags', 'frequency']
                    if c in data.columns]
            print_display_df(data[cols], max_colwidth=42)
            print(f"\n共 {len(data)} 个 Series")
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
    parser = argparse.ArgumentParser(description="获取预测市场 Series 列表（无需订阅）")
    parser.add_argument("--category", default=None, help="一级分类，如 Sports")
    parser.add_argument("--tag", default=None, help="二级分类，如 Football")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_series_list(category=args.category, tag=args.tag, output_json=args.output_json)
