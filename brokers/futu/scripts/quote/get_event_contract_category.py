#!/usr/bin/env python3
"""
获取预测市场分类列表

功能：获取预测市场一级分类及其下属二级分类（tags），无需订阅
用法：python get_event_contract_category.py [--category Sports] [--json]

接口：OpenQuoteContext.get_event_contract_category(category=None)
返回：DataFrame，字段 category / category_name / tags（tags 为二级分类标识字符串列表）

调用链：分类(get_event_contract_category) → 赛事筛选(filter_competition)
        → Series(get_event_contract_series_list) → Event → Contract → 快照/盘口/K线/逐笔
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


def get_event_contract_category(category=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data = ctx.get_event_contract_category(category=category)
        check_ret(ret, data, ctx, "获取预测市场分类")

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
            print("预测市场分类")
            print("=" * 70)
            print_display_df(data, max_colwidth=60)
            print(f"\n共 {len(data)} 个分类")
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
    parser = argparse.ArgumentParser(description="获取预测市场分类列表（无需订阅）")
    parser.add_argument("--category", default=None, help="一级分类标识，如 Sports；不填返回全部分类")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_category(category=args.category, output_json=args.output_json)
