#!/usr/bin/env python3
"""
获取预测市场里程碑列表

功能：获取赛事类预测市场的重要时间节点（如某场比赛），支持分类/赛事/关联事件过滤与分页，无需订阅
用法：python get_event_contract_milestone_list.py [--category Sports] [--competition "FIFA World Cup"] [--related-event EC.xxx] [--count 20] [--next-page KEY] [--json]

接口：OpenQuoteContext.get_event_contract_milestone_list(category=None, competition=None,
      related_event=None, next_page=None, count=None)
返回：(ret, DataFrame, next_page)；字段 milestone_code / title / category / type / start_date /
      end_date / primary_event_code / related_events / notification_message

分页：默认只取第一页；续拉传 --next-page
备注：competition 取值需先用 filter_competition.py 查询可用赛事名称
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


def get_event_contract_milestone_list(category=None, competition=None, related_event=None,
                                      next_page=None, count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data, page = ctx.get_event_contract_milestone_list(
            category=category, competition=competition, related_event=related_event,
            next_page=next_page, count=count)
        check_ret(ret, data, ctx, "获取预测市场里程碑列表")

        records = [] if is_empty(data) else df_to_records(data)

        if output_json:
            print(json.dumps({"data": records, "next_page": page or ""}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("预测市场里程碑列表")
            print("=" * 70)
            if records:
                cols = [c for c in ['milestone_code', 'title', 'type', 'start_date',
                                    'end_date', 'primary_event_code', 'notification_message']
                        if c in data.columns]
                print_display_df(data[cols], max_colwidth=38)
                print(f"\n共 {len(data)} 个里程碑")
            else:
                print("无数据")
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
    parser = argparse.ArgumentParser(description="获取预测市场里程碑列表（无需订阅）")
    parser.add_argument("--category", default=None, help="一级分类，如 Sports")
    parser.add_argument("--competition", default=None, help="赛事名称（来自 filter_competition），如 FIFA World Cup")
    parser.add_argument("--related-event", default=None, help="关联事件代码，如 EC.xxx")
    parser.add_argument("--next-page", default=None, help="翻页标记，首页不传，续拉传上次返回的 next_page")
    parser.add_argument("--count", type=int, default=None, help="返回数量上限，默认 200")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_milestone_list(
        category=args.category, competition=args.competition,
        related_event=args.related_event, next_page=args.next_page,
        count=args.count, output_json=args.output_json)
