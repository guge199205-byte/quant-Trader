#!/usr/bin/env python3
"""
接收摆盘推送

功能：订阅股票摆盘（买卖盘）并通过 Handler 接收实时推送数据
用法：python push_orderbook.py HK.00700 --duration 60

接口限制：
- 需先订阅 ORDER_BOOK 类型，受订阅额度限制
"""
import argparse
import json
import time
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    SubType,
    RET_OK,
    OrderBookType,
)

from futu import OrderBookHandlerBase, RET_ERROR


class OrderBookHandler(OrderBookHandlerBase):
    """摆盘推送回调处理类"""
    def __init__(self, output_json=False):
        super().__init__()
        self.output_json = output_json

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            if self.output_json:
                print(json.dumps({"error": str(data)}, ensure_ascii=False), flush=True)
            else:
                print(f"推送错误: {data}", flush=True)
            return RET_ERROR, data

        if self.output_json:
            print(json.dumps({"type": "ORDER_BOOK", "code": data.get("code", ""), "data": data}, ensure_ascii=False, default=str), flush=True)
        else:
            print(f"\n[摆盘推送] {time.strftime('%H:%M:%S')} - {data.get('code', '')}")
            bid_list = data.get("Bid", [])
            ask_list = data.get("Ask", [])
            print("  买盘:")
            for item in bid_list[:5]:
                print(f"    {item}")
            print("  卖盘:")
            for item in ask_list[:5]:
                print(f"    {item}")

        return RET_OK, data


_ODD_LOT_MARKETS = ("MY", "SG")


def push_orderbook(codes, duration=60, output_json=False, order_book_type=None):
    # 碎股盘仅支持 MY/SG 市场
    if order_book_type == OrderBookType.ODD:
        for code in codes:
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
        handler = OrderBookHandler(output_json=output_json)
        ctx.set_handler(handler)

        sub_type = SubType.ORDER_BOOK_ODD if order_book_type == OrderBookType.ODD else SubType.ORDER_BOOK
        ret, msg = ctx.subscribe(codes, [sub_type], subscribe_push=True)
        check_ret(ret, msg, ctx, "订阅摆盘推送")

        if not output_json:
            print(f"已订阅摆盘推送: {', '.join(codes)}")
            print(f"等待推送 {duration} 秒...")

        time.sleep(duration)

    except KeyboardInterrupt:
        if not output_json:
            print("\n已停止接收推送")
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="接收摆盘推送")
    parser.add_argument("codes", nargs="+", help="股票代码，如 HK.00700")
    parser.add_argument("--duration", type=int, default=60, help="持续接收时间（秒，默认: 60）")
    parser.add_argument("--type", choices=["NORMAL", "ODD"], default=None, help="摆盘类型: NORMAL=整股盘, ODD=碎股盘。碎股盘仅支持 MY/SG 市场")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    ob_type = getattr(OrderBookType, args.type) if args.type else None
    push_orderbook(args.codes, args.duration, args.output_json, ob_type)
