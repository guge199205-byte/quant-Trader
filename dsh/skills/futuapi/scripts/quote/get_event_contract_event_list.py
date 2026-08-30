#!/usr/bin/env python3
"""
获取预测市场 Event 列表

功能：按 Series 代码获取 Event 列表，支持按状态过滤与分页，无需订阅
用法：python get_event_contract_event_list.py EC.KXUFCVICROUND.SERIES [--count 20] [--status EVENT_ACTIVE] [--next-page KEY] [--json]

接口：OpenQuoteContract.get_event_contract_event_list(series_code, status=None, next_page=None, count=None)
返回：(ret, DataFrame, next_page)；DataFrame 字段 event_code / event_name / event_sub_name /
      status / series_code / start_date / end_date / category / tags / mutually_exclusive /
      competition / competition_scope

分页：默认只取第一页；如需续拉，将上次返回的 next_page 作为 --next-page 传入（空串表示无更多）
状态：--status 仅取事件级状态（EVENT_INITIALIZED/EVENT_ACTIVE/EVENT_CLOSED/EVENT_SETTLED/EVENT_CANCELED/EVENT_FINALIZED/EVENT_ABNORMAL）
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
    parse_ec_status,
    assert_event_contract_support,
    ECStatus,
)

# 事件级状态（合约级状态不含 EVENT_ 前缀，此处仅供 choices 校验展示）
_EVENT_STATUS_CHOICES = [
    "EVENT_INITIALIZED", "EVENT_ACTIVE", "EVENT_CLOSED", "EVENT_SETTLED",
    "EVENT_CANCELED", "EVENT_FINALIZED", "EVENT_ABNORMAL",
]


def get_event_contract_event_list(series_code, status=None, next_page=None, count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        status_enum = parse_ec_status(status) if status else None

        ret, data, page = ctx.get_event_contract_event_list(
            series_code, status=status_enum, next_page=next_page, count=count)
        check_ret(ret, data, ctx, "获取预测市场 Event 列表")

        records = [] if is_empty(data) else df_to_records(data)

        if output_json:
            print(json.dumps({"data": records, "next_page": page or ""}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"预测市场 Event 列表 - {series_code}")
            print("=" * 70)
            if records:
                cols = [c for c in ['event_code', 'event_name', 'status', 'start_date',
                                    'end_date', 'competition', 'competition_scope']
                        if c in data.columns]
                print_display_df(data[cols], max_colwidth=42)
                print(f"\n共 {len(data)} 个 Event")
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
    parser = argparse.ArgumentParser(description="获取预测市场 Event 列表（无需订阅）")
    parser.add_argument("series_code", help="Series 代码，如 EC.KXUFCVICROUND.SERIES")
    parser.add_argument("--status", default=None, choices=_EVENT_STATUS_CHOICES,
                        help="事件状态过滤，如 EVENT_ACTIVE")
    parser.add_argument("--next-page", default=None, help="翻页标记，首页不传，续拉传上次返回的 next_page")
    parser.add_argument("--count", type=int, default=None, help="返回数量上限，默认 200")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_event_list(
        series_code=args.series_code, status=args.status,
        next_page=args.next_page, count=args.count, output_json=args.output_json)
