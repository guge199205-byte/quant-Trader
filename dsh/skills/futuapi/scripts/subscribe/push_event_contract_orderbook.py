#!/usr/bin/env python3
"""
接收预测市场摆盘推送

功能：订阅预测市场摆盘（YES/NO 买卖盘）并通过 Handler 接收实时推送
用法：python push_event_contract_orderbook.py EC.KXODIMATCH-26JUL140600INDENG-IND --duration 60 [--json]

接口：EventContractOrderBookHandlerBase 推送（需先 set_handler + 订阅 SubType.ORDER_BOOK）
返回：回调 content 为 list[dict]（每只合约一个 dict），字段 code / yes_bids / yes_asks / no_bids / no_asks

接口限制：
- 需先订阅 ORDER_BOOK 类型，受订阅额度限制
- 推送首推只返回首档盘口，后续推送按实际盘口变化返回
- 回调运行在独立子线程，请注意线程安全
- 注：与 get_event_contract_order_book 不同，推送返回 list of dict（每只合约一个 dict）
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
    EventContractOrderBookHandlerBase,
    assert_event_contract_support,
)

from futu import RET_ERROR

# 推送 Handler 基类仅支持预测市场的 SDK 版本提供；旧版本回退为 object 以保证模块可导入
# （-h 仍可用），实际可用性在 main() 中由 assert_event_contract_support() 校验
_EC_OB_BASE = EventContractOrderBookHandlerBase if EventContractOrderBookHandlerBase else object


class EventContractOrderBookHandler(_EC_OB_BASE):
    """预测市场摆盘推送回调处理类"""
    def __init__(self, output_json=False):
        super().__init__()
        self.output_json = output_json

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            if self.output_json:
                print(json.dumps({"error": str(content)}, ensure_ascii=False), flush=True)
            else:
                print(f"推送错误: {content}", flush=True)
            return RET_ERROR, content

        # content 为 list[dict]（每只合约一个 dict）
        for ob in content:
            if self.output_json:
                print(json.dumps({
                    "type": "EVENT_CONTRACT_ORDER_BOOK",
                    "code": ob.get("code", ""),
                    "yes_bids": ob.get("yes_bids", []),
                    "yes_asks": ob.get("yes_asks", []),
                    "no_bids": ob.get("no_bids", []),
                    "no_asks": ob.get("no_asks", []),
                }, ensure_ascii=False, default=str), flush=True)
            else:
                print(f"\n[预测市场摆盘推送] {time.strftime('%H:%M:%S')} - {ob.get('code', '')}")
                for label, key in [("YES 买盘", "yes_bids"), ("YES 卖盘", "yes_asks"),
                                   ("NO 买盘", "no_bids"), ("NO 卖盘", "no_asks")]:
                    levels = ob.get(key, [])
                    print(f"  {label}: {levels[:5]}")

        return RET_OK, content


def push_event_contract_orderbook(codes, duration=60, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        handler = EventContractOrderBookHandler(output_json=output_json)
        ctx.set_handler(handler)

        ret, msg = ctx.subscribe_event_contract(codes, [SubType.ORDER_BOOK], subscribe_push=True)
        check_ret(ret, msg, ctx, "订阅预测市场摆盘推送")

        if not output_json:
            print(f"已订阅预测市场摆盘推送: {', '.join(codes)}")
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
    parser = argparse.ArgumentParser(description="接收预测市场摆盘推送")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.xxx")
    parser.add_argument("--duration", type=int, default=60, help="持续接收时间（秒，默认: 60）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    push_event_contract_orderbook(args.codes, args.duration, args.output_json)
