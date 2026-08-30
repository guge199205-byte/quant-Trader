#!/usr/bin/env python3
"""
接收预测市场逐笔推送

功能：订阅预测市场逐笔并通过 Handler 接收实时推送
用法：python push_event_contract_ticker.py EC.KXODIMATCH-26JUL140600INDENG-IND --duration 60 [--json]

接口：EventContractTickerHandlerBase 推送（需先 set_handler + 订阅 SubType.TICKER）
返回：回调 content 为 DataFrame，字段 code / time / yes_price / no_price / volume / side / sequence

接口限制：
- 需先订阅 TICKER 类型，受订阅额度限制
- 回调运行在独立子线程，请注意线程安全
- sequence 为后台逐笔序号，大整数
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
    safe_float,
    SubType,
    RET_OK,
    EventContractTickerHandlerBase,
    assert_event_contract_support,
)

from futu import RET_ERROR

# 推送 Handler 基类仅支持预测市场的 SDK 版本提供；旧版本回退为 object 以保证模块可导入
_EC_TK_BASE = EventContractTickerHandlerBase if EventContractTickerHandlerBase else object


class EventContractTickerHandler(_EC_TK_BASE):
    """预测市场逐笔推送回调处理类"""
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

        # content 为 DataFrame
        if self.output_json:
            records = []
            for i in range(len(content)):
                row = content.iloc[i] if hasattr(content, "iloc") else content[i]
                records.append({
                    "code": row.get("code", ""),
                    "time": row.get("time", ""),
                    "yes_price": safe_float(row.get("yes_price", 0)),
                    "no_price": safe_float(row.get("no_price", 0)),
                    "volume": safe_float(row.get("volume", 0)),
                    "side": row.get("side", ""),
                    "sequence": str(row.get("sequence", "")),
                })
            print(json.dumps({"type": "EVENT_CONTRACT_TICKER", "data": records}, ensure_ascii=False, default=str), flush=True)
        else:
            print(f"\n[预测市场逐笔推送] {time.strftime('%H:%M:%S')}")
            print(content.to_string(index=False))

        return RET_OK, content


def push_event_contract_ticker(codes, duration=60, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        handler = EventContractTickerHandler(output_json=output_json)
        ctx.set_handler(handler)

        ret, msg = ctx.subscribe_event_contract(codes, [SubType.TICKER], subscribe_push=True)
        check_ret(ret, msg, ctx, "订阅预测市场逐笔推送")

        if not output_json:
            print(f"已订阅预测市场逐笔推送: {', '.join(codes)}")
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
    parser = argparse.ArgumentParser(description="接收预测市场逐笔推送")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.xxx")
    parser.add_argument("--duration", type=int, default=60, help="持续接收时间（秒，默认: 60）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    push_event_contract_ticker(args.codes, args.duration, args.output_json)
