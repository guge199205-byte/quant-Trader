#!/usr/bin/env python3
"""
获取可 Combo（组合）事件列表

功能：获取可组合的事件列表，返回每个可组合事件下的可组合合约列表及 MVC 标的（询价需透传），无需订阅
用法：python get_valid_combo_list.py [--category Sports] [--count 20] [--next-page KEY] [--json]

接口：OpenQuoteContext.get_valid_combo_list(category=None, competition=None, series=None,
      next_page=None, count=None)
返回：(ret, data, mvc, next_page)；data 为 DataFrame（event_code / event_name / combo_contracts /
      series_code / category / competition / competition_scope），mvc 为 MVC 标的字符串

分页：默认只取第一页；续拉传 --next-page
备注：mvc 必须透传给 request_combo_quotes.py 进行 Combo 询价
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
    to_jsonable,
    assert_event_contract_support,
)


def get_valid_combo_list(category=None, competition=None, series=None,
                         next_page=None, count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data, mvc, page = ctx.get_valid_combo_list(
            category=category, competition=competition, series=series,
            next_page=next_page, count=count)
        check_ret(ret, data, ctx, "获取可 Combo 事件列表")

        records = [] if is_empty(data) else df_to_records(data)

        if output_json:
            print(json.dumps({
                "data": records,
                "mvc": to_jsonable(mvc, default=""),
                "next_page": page or "",
            }, ensure_ascii=False))
        else:
            print("=" * 70)
            print("可 Combo 事件列表")
            print("=" * 70)
            if records:
                cols = [c for c in ['event_code', 'event_name', 'combo_contracts',
                                    'series_code', 'competition', 'competition_scope']
                        if c in data.columns]
                print_display_df(data[cols], max_colwidth=40)
                print(f"\n共 {len(data)} 个可组合事件")
            else:
                print("无数据")
            print(f"mvc: {mvc or '(无)'}  ← 透传给 request_combo_quotes")
            print(f"next_page: {page or '(无更多)'}")
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
    parser = argparse.ArgumentParser(description="获取可 Combo 事件列表与 mvc（无需订阅）")
    parser.add_argument("--category", default=None, help="一级分类，如 Sports")
    parser.add_argument("--competition", default=None, help="赛事过滤")
    parser.add_argument("--series", default=None, help="Series 标的过滤，如 EC.xxx")
    parser.add_argument("--next-page", default=None, help="翻页标记，首页不传，续拉传上次返回的 next_page")
    parser.add_argument("--count", type=int, default=None, help="单页最大返回数，默认 200")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_valid_combo_list(
        category=args.category, competition=args.competition, series=args.series,
        next_page=args.next_page, count=args.count, output_json=args.output_json)
