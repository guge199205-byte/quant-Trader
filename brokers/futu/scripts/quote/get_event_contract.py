#!/usr/bin/env python3
"""
获取预测市场 Contract 列表

功能：按 Event 代码获取合约（Contract）列表，含合约类型、时间、状态、结果、交易属性等，无需订阅
用法：python get_event_contract.py EC.KXUFCVICROUND-26JUL11SAIPIM.EVENT [--count 20] [--next-page KEY] [--json]

接口：OpenQuoteContext.get_event_contract(event_code, next_page=None, count=None)
返回：(ret, data, next_page)；data 为 dict {'contract_list': DataFrame, 'recommend_contracts': list[dict]}
      contract_list 字段：contract_code / event_code / series_code / contract_type / title /
      yes_sub_title / open_time / close_time / determination_time / settled_time /
      latest_expiration_time / status / result / settlement_value / expiration_value /
      volume / can_close_early / tick_size / category / tag

分页：默认只取第一页；续拉将上次返回的 next_page 作为 --next-page 传入
备注：contract_code 即 EC.xxx，可作为快照/盘口/K线/逐笔等接口的 code
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


def get_event_contract(event_code, next_page=None, count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, data, page = ctx.get_event_contract(
            event_code, next_page=next_page, count=count)
        check_ret(ret, data, ctx, "获取预测市场 Contract 列表")

        contract_df = data.get("contract_list") if isinstance(data, dict) else None
        recommends = data.get("recommend_contracts", []) if isinstance(data, dict) else []
        records = [] if is_empty(contract_df) else df_to_records(contract_df)
        recommend_list = [to_jsonable(r) for r in recommends] if recommends else []

        if output_json:
            print(json.dumps({
                "contract_list": records,
                "recommend_contracts": recommend_list,
                "next_page": page or "",
            }, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"预测市场 Contract 列表 - {event_code}")
            print("=" * 70)
            if records:
                cols = [c for c in ['contract_code', 'contract_type', 'title', 'status',
                                    'result', 'volume', 'tick_size']
                        if c in contract_df.columns]
                print_display_df(contract_df[cols], max_colwidth=42)
                print(f"\n共 {len(contract_df)} 个合约")
            else:
                print("无数据")
            if recommend_list:
                print(f"\n推荐合约: {[r.get('contract_code', r) for r in recommend_list]}")
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
    parser = argparse.ArgumentParser(description="获取预测市场 Contract 列表（无需订阅）")
    parser.add_argument("event_code", help="Event 代码，如 EC.KXUFCVICROUND-26JUL11SAIPIM.EVENT")
    parser.add_argument("--next-page", default=None, help="翻页标记，首页不传，续拉传上次返回的 next_page")
    parser.add_argument("--count", type=int, default=None, help="单页最大返回数，默认 100，最大 1000")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract(
        event_code=args.event_code, next_page=args.next_page,
        count=args.count, output_json=args.output_json)
