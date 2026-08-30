#!/usr/bin/env python3
"""
获取预测市场实时摆盘

功能：获取预测市场 YES/NO 双向多档买卖盘，需先订阅 ORDER_BOOK 类型
用法：python get_event_contract_order_book.py EC.KXODIMATCH-26JUL140600INDENG-IND [--num 5] [--no-auto-subscribe] [--json]

接口：OpenQuoteContext.get_event_contract_order_book(code, num=10)
返回：dict {'code', 'yes_bids': [(price,size),...], 'yes_asks', 'no_bids', 'no_asks'}

接口限制：
- 查询前必须先订阅 SubType.ORDER_BOOK，否则报错"请求获取事件合约摆盘接口前，请先订阅OrderBook数据"
- num 需大于 0，默认 10；实际返回档数按后台数据收敛
- 脚本默认自动订阅；已订阅场景可用 --no-auto-subscribe 跳过
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
    SubType,
    assert_event_contract_support,
    ensure_event_contract_subscribed,
)


def _format_book_side(levels, n_show=None):
    """格式化单边盘口 -> [(price, size), ...]，转为可序列化 list"""
    out = []
    for lv in levels:
        if isinstance(lv, (list, tuple)) and len(lv) >= 2:
            out.append([float(lv[0]), float(lv[1])])
        else:
            out.append(lv)
    if n_show:
        out = out[:n_show]
    return out


def get_event_contract_order_book(code, num=10, auto_subscribe=True, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        # 查询前自动订阅 ORDER_BOOK（已订阅静默跳过，其他失败退出）
        if auto_subscribe:
            ensure_event_contract_subscribed(ctx, code, SubType.ORDER_BOOK,
                                             output_json=output_json, action="订阅预测市场摆盘")

        ret, data = ctx.get_event_contract_order_book(code, num=num)
        check_ret(ret, data, ctx, "获取预测市场摆盘")

        result = {
            "code": data.get("code", code),
            "yes_bids": _format_book_side(data.get("yes_bids", [])),
            "yes_asks": _format_book_side(data.get("yes_asks", [])),
            "no_bids": _format_book_side(data.get("no_bids", [])),
            "no_asks": _format_book_side(data.get("no_asks", [])),
        }

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 64)
            print(f"预测市场摆盘 - {result['code']}")
            print("=" * 64)
            for label, side in [("YES 买盘 (bid)", "yes_bids"), ("YES 卖盘 (ask)", "yes_asks")]:
                levels = result[side]
                print(f"  {label}:")
                for i, lv in enumerate(levels, 1):
                    if isinstance(lv, list) and len(lv) >= 2:
                        print(f"    {i}: {lv[0]:>8.3f} x {lv[1]:>10.2f}")
                    else:
                        print(f"    {i}: {lv}")
            for label, side in [("NO 买盘 (bid)", "no_bids"), ("NO 卖盘 (ask)", "no_asks")]:
                levels = result[side]
                print(f"  {label}:")
                for i, lv in enumerate(levels, 1):
                    if isinstance(lv, list) and len(lv) >= 2:
                        print(f"    {i}: {lv[0]:>8.3f} x {lv[1]:>10.2f}")
                    else:
                        print(f"    {i}: {lv}")
            print("=" * 64)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取预测市场实时摆盘（需先订阅 ORDER_BOOK）")
    parser.add_argument("code", help="预测市场合约代码，如 EC.KXODIMATCH-26JUL140600INDENG-IND")
    parser.add_argument("--num", type=int, default=10, help="请求摆盘档数，默认 10，需大于 0")
    parser.add_argument("--no-auto-subscribe", action="store_true",
                        help="不自动订阅（适用于已订阅场景）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_order_book(
        code=args.code, num=args.num,
        auto_subscribe=not args.no_auto_subscribe, output_json=args.output_json)
