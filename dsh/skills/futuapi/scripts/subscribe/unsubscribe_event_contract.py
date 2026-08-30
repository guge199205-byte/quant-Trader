#!/usr/bin/env python3
"""
取消订阅预测市场

功能：按合约代码、数据类型、K 线来源三个维度精确取消订阅预测市场
用法：python unsubscribe_event_contract.py EC.xxx --types TICKER [--kline-source ORDER_BOOK_YES] [--json]

接口：OpenQuoteContext.unsubscribe_event_contract(code_list, subtype_list, kline_source_list=None)
返回：(ret, err_message)

接口限制：
- 订阅后至少 1 分钟才能反订阅
- 反订阅时三个维度（合约/类型/来源）需与订阅时一致才能精确匹配
- 取消当前连接所有订阅可用 unsubscribe_all_event_contract.py
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
    assert_event_contract_support,
)


def unsubscribe_event_contract(codes, subtype_names, kline_source_names=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        subtypes = parse_subtypes(subtype_names)
        kline_sources = None
        if kline_source_names:
            kline_sources = [parse_ec_kline_source(s) for s in kline_source_names]

        ret, err = ctx.unsubscribe_event_contract(
            codes, subtypes, kline_source_list=kline_sources)
        check_ret(ret, err, ctx, "取消订阅预测市场")

        result = {
            "codes": codes,
            "subtypes": [str(s).split(".")[-1] for s in subtypes],
            "kline_sources": kline_source_names or [],
            "status": "unsubscribed",
        }

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 50)
            print("取消订阅预测市场成功")
            print("=" * 50)
            print(f"  合约: {', '.join(codes)}")
            print(f"  类型: {', '.join(result['subtypes'])}")
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
    parser = argparse.ArgumentParser(description="取消订阅预测市场")
    parser.add_argument("codes", nargs="+", help="预测市场合约代码，如 EC.xxx")
    parser.add_argument("--types", nargs="+", required=True,
                        help="取消订阅的类型: ORDER_BOOK TICKER K_1M K_5M K_60M K_DAY")
    parser.add_argument("--kline-source", nargs="+", default=None,
                        help="K线来源（精确反订阅某 K 线来源）: ORDER_BOOK_YES")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    unsubscribe_event_contract(
        codes=args.codes, subtype_names=args.types,
        kline_source_names=args.kline_source, output_json=args.output_json)
