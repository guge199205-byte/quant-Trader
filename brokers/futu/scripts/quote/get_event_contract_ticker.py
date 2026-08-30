#!/usr/bin/env python3
"""
获取预测市场实时逐笔

功能：获取预测市场实时逐笔成交数据（YES/NO 成交价、成交量、成交方向、逐笔序号），需先订阅 TICKER 类型
用法：python get_event_contract_ticker.py EC.KXODIMATCH-26JUL140600INDENG-IND [--count 30] [--no-auto-subscribe] [--json]

接口：OpenQuoteContext.get_event_contract_ticker(code, count=30)
返回：DataFrame；字段 code / time / yes_price / no_price / volume / side / sequence

接口限制：
- 查询前必须先订阅 SubType.TICKER，否则报错"请求获取事件合约逐笔接口前，请先订阅Ticker数据"
- count 默认 30，最大 1000
- side 为成交方向（YES/NO），区别于交易买卖方向
- sequence 为后台逐笔序号，大整数
- 脚本默认自动订阅；已订阅场景用 --no-auto-subscribe 跳过
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
    SubType,
    assert_event_contract_support,
    ensure_event_contract_subscribed,
)


def get_event_contract_ticker(code, count=30, auto_subscribe=True, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        # 查询前自动订阅 TICKER（已订阅静默跳过，其他失败退出）
        if auto_subscribe:
            ensure_event_contract_subscribed(ctx, code, SubType.TICKER,
                                             output_json=output_json, action="订阅预测市场逐笔")

        ret, data = ctx.get_event_contract_ticker(code, count=count)
        check_ret(ret, data, ctx, "获取预测市场逐笔")

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
            print(f"预测市场逐笔 - {code}")
            print("=" * 70)
            cols = [c for c in ['code', 'time', 'yes_price', 'no_price',
                                'volume', 'side', 'sequence']
                    if c in data.columns]
            print_display_df(data[cols], max_colwidth=28)
            print(f"\n共 {len(data)} 笔成交")
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
    parser = argparse.ArgumentParser(description="获取预测市场实时逐笔（需先订阅 TICKER）")
    parser.add_argument("code", help="预测市场合约代码，如 EC.KXODIMATCH-26JUL140600INDENG-IND")
    parser.add_argument("--count", type=int, default=30, help="返回条数，默认 30，最大 1000")
    parser.add_argument("--no-auto-subscribe", action="store_true",
                        help="不自动订阅（适用于已订阅场景）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_ticker(
        code=args.code, count=args.count,
        auto_subscribe=not args.no_auto_subscribe, output_json=args.output_json)
