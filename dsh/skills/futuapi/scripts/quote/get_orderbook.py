#!/usr/bin/env python3
"""
获取买卖盘（摆盘）数据

功能：获取指定股票的买卖盘深度数据
用法：python get_orderbook.py HK.00700 --num 10

接口限制：
- 需先订阅 ORDER_BOOK 类型
- 美股会返回当前交易时段的实时摆盘数据，无需设置时段

返回字段说明：
- svr_recv_time_bid/svr_recv_time_ask: 部分数据接收时间为零（如服务器重启或第一次推送缓存数据）
- Bid/Ask 委托明细: 港股 SF 权限下最多 1000 笔，其余权限不支持
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
    SubType,
    RET_OK,
    OrderBookType,
)


_ODD_LOT_MARKETS = ("MY", "SG")


def get_orderbook(code, num=10, output_json=False, order_book_type=None):
    # 碎股盘仅支持 MY 与 SG 市场
    if order_book_type == OrderBookType.ODD:
        prefix = code.split(".")[0].upper() if "." in code else ""
        if prefix not in _ODD_LOT_MARKETS:
            msg = f"碎股盘仅支持 {'/'.join(_ODD_LOT_MARKETS)} 市场，当前代码: {code}"
            if output_json:
                print(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                print(f"错误: {msg}")
            sys.exit(1)

    ctx = None
    try:
        ctx = create_quote_context()
        # 根据摆盘类型选择订阅类型：碎股盘用 ORDER_BOOK_ODD，整股盘用 ORDER_BOOK
        sub_type = SubType.ORDER_BOOK_ODD if order_book_type == OrderBookType.ODD else SubType.ORDER_BOOK
        ret, msg = ctx.subscribe([code], [sub_type])
        if ret != RET_OK:
            print(f"订阅失败: {msg}")
            sys.exit(1)

        ret, data = ctx.get_order_book(code, num=num, order_book_type=order_book_type)
        check_ret(ret, data, ctx, "获取买卖盘")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "Bid": [], "Ask": [], "order_book_type": data.get("order_book_type", "") if isinstance(data, dict) else ""}))
            else:
                print("无数据")
            return

        # data 是 dict，包含 Bid 和 Ask 列表
        bids = data.get("Bid", [])
        asks = data.get("Ask", [])
        ob_type = data.get("order_book_type", "")

        if output_json:
            print(json.dumps({"code": code, "Bid": bids, "Ask": asks, "order_book_type": ob_type}, ensure_ascii=False))
        else:
            type_label = f" [{ob_type}]" if ob_type else ""
            print("=" * 60)
            print(f"买卖盘: {code}{type_label}")
            print("=" * 60)
            print(f"  {'卖盘 (Ask)':^28}  |  {'买盘 (Bid)':^28}")
            print("  " + "-" * 58)
            max_rows = max(len(bids), len(asks))
            for i in range(max_rows):
                ask_str = ""
                bid_str = ""
                if i < len(asks):
                    a = asks[len(asks) - 1 - i]
                    ask_str = f"  卖{len(asks)-i}: {a[0]:>10.3f} x {int(a[1]):>8}"
                if i < len(bids):
                    b = bids[i]
                    bid_str = f"  买{i+1}: {b[0]:>10.3f} x {int(b[1]):>8}"
                print(f"  {ask_str:<28}  |  {bid_str:<28}")
            print("=" * 60)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取买卖盘（摆盘）数据")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--num", type=int, default=10, help="档位数量（默认: 10）")
    parser.add_argument("--type", choices=["NORMAL", "ODD"], default=None, help="摆盘类型: NORMAL=整股盘, ODD=碎股盘（默认: 不传，返回整股盘）。碎股盘仅支持 MY/SG 市场")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    ob_type = getattr(OrderBookType, args.type) if args.type else None
    get_orderbook(args.code, args.num, args.output_json, ob_type)
