#!/usr/bin/env python3
"""
赛事筛选

功能：按一级分类（category）与二级分类（tag）获取可用赛事名称列表与玩法全集，无需订阅
用法：python filter_competition.py --category Sports [--tag Baseball] [--json]

接口：OpenQuoteContext.filter_competition(category=None, tag=None)
返回：DataFrame，字段 category / tag / competition（赛事名称列表）/ scope（玩法全集）

备注：competition 列表中的赛事名称可作为 get_event_contract_milestone_list 的 competition 入参
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
    assert_event_contract_support,
)


def filter_competition(category=None, tag=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data = ctx.filter_competition(category=category, tag=tag)
        check_ret(ret, data, ctx, "赛事筛选")

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
            print(f"赛事筛选 - {category or '全部分类'} {('/' + tag) if tag else ''}")
            print("=" * 70)
            for i in range(len(data)):
                row = data.iloc[i]
                comp = row.get("competition")
                scope = row.get("scope")
                print(f"\n  [{row.get('tag', '')}]")
                print(f"    赛事: {comp}")
                print(f"    玩法: {scope}")
            print(f"\n共 {len(data)} 个标签")
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
    parser = argparse.ArgumentParser(description="赛事筛选（获取赛事与玩法，无需订阅）")
    parser.add_argument("--category", default=None, help="一级分类，如 Sports")
    parser.add_argument("--tag", default=None, help="二级分类，如 Baseball")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    filter_competition(category=args.category, tag=args.tag, output_json=args.output_json)
