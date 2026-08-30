#!/usr/bin/env python3
"""
获取订单费用

功能：查询指定订单的费用明细
用法：python get_order_fee.py 12345678 87654321

接口限制：
- 每 30 秒内最多请求 10 次
- 每次最多查询 20 个订单

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
    create_sec_or_future_trade_context,
    normalize_trade_ctx_type,
    TRADE_CTX_TYPE_CHOICES,
    parse_trd_env,
    TRD_MARKET_CLI_CHOICES,
    parse_security_firm,
    get_default_acc_id,
    get_default_trd_env,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
)


def get_order_fee(order_ids, acc_id=None, market=None, trd_env=None, security_firm=None,
                  ctx_type="SEC", output_json=False):
    acc_id = acc_id or get_default_acc_id()
    trd_env = parse_trd_env(trd_env) if trd_env else get_default_trd_env()
    ctx_type = normalize_trade_ctx_type(ctx_type)

    ctx = None
    try:
        ctx = create_sec_or_future_trade_context(
            market, security_firm=parse_security_firm(security_firm), ctx_type=ctx_type
        )
        ret, data = ctx.order_fee_query(
            order_id_list=order_ids,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        check_ret(ret, data, ctx, "获取订单费用")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无订单费用数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("订单费用明细")
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
    parser = argparse.ArgumentParser(description="获取订单费用")
    parser.add_argument("order_ids", nargs="+", help="订单 ID 列表")
    parser.add_argument("--acc-id", type=int, default=None, help="账户 ID")
    parser.add_argument("--market", choices=TRD_MARKET_CLI_CHOICES, default=None, help="交易市场")
    parser.add_argument("--trd-env", choices=["REAL", "SIMULATE"], default=None, help="交易环境")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG", "FUTUAU", "FUTUCA", "FUTUJP", "FUTUMY"],
                        default=None, help="券商标识")
    parser.add_argument("--ctx-type", choices=list(TRADE_CTX_TYPE_CHOICES), default="SEC",
                        help="交易上下文：SEC=证券，FUTURE=期货/预测市场（与 get_accounts 的 ctx_type 一致）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_order_fee(args.order_ids, acc_id=args.acc_id, market=args.market,
                  trd_env=args.trd_env, security_firm=args.security_firm,
                  ctx_type=args.ctx_type, output_json=args.output_json)
