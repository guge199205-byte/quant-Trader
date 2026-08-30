#!/usr/bin/env python3
"""
接收预测市场 K 线推送

功能：订阅预测市场 K 线并通过 Handler 接收实时推送
用法：python push_event_contract_kline.py EC.KXODIMATCH-26JUL140600INDENG-IND --ktype K_DAY [--kline-source ORDER_BOOK_YES] [--duration 300] [--json]

接口：EventContractKlineHandlerBase 推送（需先 set_handler + 订阅对应 K 线类型）
返回：回调 content 为 DataFrame，字段 code / pre_side / name / time_key / open / high / low / close / volume

接口限制：
- 需先订阅对应 K 线类型，受订阅额度限制
- 预测市场 K 线仅支持 K_1M/K_5M/K_60M/K_DAY
- kline_source 不填默认合约级成交价 K 线
- 回调运行在独立子线程，请注意线程安全
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
    safe_int,
    parse_subtypes,
    parse_ec_kline_source,
    SubType,
    KLType,
    RET_OK,
    EventContractKlineHandlerBase,
    assert_event_contract_support,
)

from futu import RET_ERROR


_KLTYPE_SUBTYPE_MAP = {
    "K_1M": SubType.K_1M,
    "K_5M": SubType.K_5M,
    "K_60M": SubType.K_60M,
    "K_DAY": SubType.K_DAY,
}

# 推送 Handler 基类仅支持预测市场的 SDK 版本提供；旧版本回退为 object 以保证模块可导入
_EC_KL_BASE = EventContractKlineHandlerBase if EventContractKlineHandlerBase else object


class EventContractKlineHandler(_EC_KL_BASE):
    """预测市场 K 线推送回调处理类"""
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
                    "pre_side": row.get("pre_side", ""),
                    "name": row.get("name", ""),
                    "time_key": row.get("time_key", ""),
                    "open": safe_float(row.get("open", 0)),
                    "high": safe_float(row.get("high", 0)),
                    "low": safe_float(row.get("low", 0)),
                    "close": safe_float(row.get("close", 0)),
                    "volume": safe_float(row.get("volume", 0)),
                })
            print(json.dumps({"type": "EVENT_CONTRACT_KLINE", "data": records}, ensure_ascii=False, default=str), flush=True)
        else:
            print(f"\n[预测市场K线推送] {time.strftime('%H:%M:%S')}")
            print(content.to_string(index=False))

        return RET_OK, content


def push_event_contract_kline(codes, ktype="K_DAY", kline_source=None, duration=300, output_json=False):
    ktype_key = str(ktype).upper()
    sub_type = _KLTYPE_SUBTYPE_MAP.get(ktype_key, SubType.K_DAY)

    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        handler = EventContractKlineHandler(output_json=output_json)
        ctx.set_handler(handler)

        kline_sources = None
        if kline_source:
            kline_sources = [parse_ec_kline_source(kline_source)]

        ret, msg = ctx.subscribe_event_contract(
            codes, [sub_type], kline_source_list=kline_sources, subscribe_push=True)
        check_ret(ret, msg, ctx, "订阅预测市场 K 线推送")

        if not output_json:
            print(f"已订阅预测市场 {ktype_key} K线推送: {', '.join(codes)}")
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
    parser = argparse.ArgumentParser(description="接收预测市场 K 线推送")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.xxx")
    parser.add_argument("--ktype", choices=["K_1M", "K_5M", "K_60M", "K_DAY"],
                        default="K_DAY", help="K 线类型（默认: K_DAY）")
    parser.add_argument("--kline-source", choices=["ORDER_BOOK_YES"], default=None,
                        help="K 线来源 ORDER_BOOK_YES（默认合约级成交价 K 线）")
    parser.add_argument("--duration", type=int, default=300, help="持续接收时间（秒，默认: 300）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    push_event_contract_kline(args.codes, args.ktype, args.kline_source, args.duration, args.output_json)
