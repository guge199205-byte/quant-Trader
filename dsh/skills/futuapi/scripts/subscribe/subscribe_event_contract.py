#!/usr/bin/env python3
"""
订阅预测市场实时信息

功能：指定合约代码和订阅的数据类型即可订阅预测市场实时信息（盘口/逐笔/K线等）
用法：python subscribe_event_contract.py EC.KXODIMATCH-26JUL140600INDENG-IND --types ORDER_BOOK TICKER K_DAY [--kline-source ORDER_BOOK_YES] [--no-first-push] [--json]

接口：OpenQuoteContext.subscribe_event_contract(code_list, subtype_list, kline_source_list=None,
      is_first_push=True, subscribe_push=True)
返回：(ret, err_message)

接口限制：
- 订阅数受限于 OpenD 订阅额度
- 接收推送需先 set_handler 注册对应处理器（推送脚本 push_event_contract_* 会自动设置）
- kline_source_list 仅订阅 K 线类型时生效，与 subtype_list 中的 K 线类型一一对应；不填默认合约级成交价 K 线
- 预测市场 K 线仅支持 K_1M/K_5M/K_60M/K_DAY

常用 SubType：ORDER_BOOK（摆盘）/ TICKER（逐笔）/ K_1M / K_5M / K_60M / K_DAY
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
    parse_subtypes,
    parse_ec_kline_source,
    EC_KLTYPE_CHOICES,
    assert_event_contract_support,
)


# 预测市场支持的 K 线 SubType
_EC_KLINE_SUBTYPES = {"K_1M", "K_5M", "K_60M", "K_DAY"}


def subscribe_event_contract(codes, subtype_names, kline_source_names=None,
                             is_first_push=True, subscribe_push=True, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        subtypes = parse_subtypes(subtype_names)

        # 校验 K 线类型（预测市场仅支持 4 种）
        subtype_keys = [str(s).split(".")[-1] for s in subtypes]
        for k in subtype_keys:
            if k.startswith("K_") and k not in _EC_KLINE_SUBTYPES:
                raise ValueError(f"预测市场 K 线仅支持 {EC_KLTYPE_CHOICES}，收到: {k}")

        # kline_source_list 仅在订阅 K 线时生效
        kline_sources = None
        if kline_source_names:
            kline_sources = [parse_ec_kline_source(s) for s in kline_source_names]

        ret, err = ctx.subscribe_event_contract(
            codes, subtypes, kline_source_list=kline_sources,
            is_first_push=is_first_push, subscribe_push=subscribe_push)
        check_ret(ret, err, ctx, "订阅预测市场")

        result = {
            "codes": codes,
            "subtypes": subtype_keys,
            "kline_sources": kline_source_names or [],
            "is_first_push": is_first_push,
            "subscribe_push": subscribe_push,
            "status": "subscribed",
        }

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 50)
            print("订阅预测市场成功")
            print("=" * 50)
            print(f"  合约: {', '.join(codes)}")
            print(f"  类型: {', '.join(subtype_keys)}")
            if kline_source_names:
                print(f"  K线来源: {', '.join(kline_source_names)}")
            print("=" * 50)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="订阅预测市场实时信息")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.xxx")
    parser.add_argument("--types", nargs="+", required=True,
                        help="订阅类型: ORDER_BOOK TICKER K_1M K_5M K_60M K_DAY")
    parser.add_argument("--kline-source", nargs="+", default=None,
                        help="K线来源（仅 K 线类型生效）: ORDER_BOOK_YES")
    parser.add_argument("--no-first-push", action="store_true", help="不立即推送缓存数据")
    parser.add_argument("--no-push", action="store_true", help="不注册推送回调（仅主动获取）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    subscribe_event_contract(
        codes=args.codes, subtype_names=args.types,
        kline_source_names=args.kline_source,
        is_first_push=not args.no_first_push,
        subscribe_push=not args.no_push, output_json=args.output_json)
