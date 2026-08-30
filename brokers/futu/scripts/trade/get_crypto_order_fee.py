#!/usr/bin/env python3
"""
查询加密货币订单费用

功能：查询指定加密货币订单的费用明细（仅实盘）
用法：python get_crypto_order_fee.py 12345678 87654321 --security-firm FUTUINC --acc-id 12345

接口限制：
- 每 30 秒内最多请求 10 次
- 每次最多查询 20 个订单

参数说明：
- order_ids: 订单 ID 列表（位置参数，至少 1 个，最多 20 个）
- 加密货币订单费用查询基于 OpenCryptoTradeContext，仅实盘（TrdEnv.REAL）
- security_firm 仅支持 FUTUSECURITIES / FUTUINC / FUTUSG

返回字段说明：
- order_id: 订单 ID
- total_fee: 总费用
- fee_list: 费用明细列表
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_crypto_trade_context,
    parse_security_firm,
    get_default_acc_id,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
    TrdEnv,
)


def get_crypto_order_fee(order_ids, acc_id=None, security_firm=None, output_json=False):
    acc_id = acc_id or get_default_acc_id()
    firm_enum = parse_security_firm(security_firm)

    if len(order_ids) > 20:
        msg = f"单次查询最多 20 个订单，当前传入 {len(order_ids)} 个"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        ret, data = ctx.order_fee_query(
            order_id_list=list(order_ids),
            trd_env=TrdEnv.REAL,
            acc_id=acc_id,
        )
        check_ret(ret, data, ctx, "查询加密货币订单费用")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}, ensure_ascii=False))
            else:
                print("无订单费用数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("加密货币订单费用明细")
            print("=" * 70)
            print(data.to_string(index=False))
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
    parser = argparse.ArgumentParser(description="查询加密货币订单费用（仅实盘）")
    parser.add_argument("order_ids", nargs="+", help="订单 ID 列表（最多 20 个）")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识（仅支持 FUTUSECURITIES/FUTUINC/FUTUSG）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_order_fee(args.order_ids, acc_id=args.acc_id,
                         security_firm=args.security_firm, output_json=args.output_json)
