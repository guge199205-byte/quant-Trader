#!/usr/bin/env python3
"""
查询加密货币订单

功能：
- 默认查询当日/未完成订单（order_list_query）
- --history 查询历史订单（history_order_list_query，默认近 90 天，可传 --start/--end）
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


def get_crypto_orders(history=False, start=None, end=None, code=None,
                     acc_id=None, security_firm=None, output_json=False, limit=200):
    acc_id = acc_id or get_default_acc_id()
    firm_enum = parse_security_firm(security_firm)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        kwargs = dict(trd_env=TrdEnv.REAL, acc_id=acc_id)
        if code:
            kwargs["code"] = code

        if history:
            # history_order_list_query 不支持 refresh_cache 参数
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end
            ret, data = ctx.history_order_list_query(**kwargs)
            action = "查询加密货币历史订单"
        else:
            kwargs["refresh_cache"] = True
            ret, data = ctx.order_list_query(**kwargs)
            action = "查询加密货币订单"

        check_ret(ret, data, ctx, action)

        records = df_to_records(data, limit=limit) if not is_empty(data) else []
        if output_json:
            print(json.dumps({"orders": records}, ensure_ascii=False))
            return

        print(f"共 {len(records)} 条订单")
        for r in records:
            print(f"  {r.get('order_id')} | {r.get('code')} | {r.get('trd_side')} | qty={r.get('qty')} price={r.get('price')} status={r.get('order_status')}")

    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="查询加密货币订单")
    parser.add_argument("--history", action="store_true", help="查询历史订单")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（仅历史订单）")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（仅历史订单）")
    parser.add_argument("--code", default=None, help="币对代码，如 CC.BTCUSD")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识")
    parser.add_argument("--limit", type=int, default=200, help="返回条数上限")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_orders(history=args.history, start=args.start, end=args.end, code=args.code,
                     acc_id=args.acc_id, security_firm=args.security_firm,
                     output_json=args.output_json, limit=args.limit)
