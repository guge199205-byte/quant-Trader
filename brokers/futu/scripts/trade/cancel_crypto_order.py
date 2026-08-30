#!/usr/bin/env python3
"""
撤销加密货币订单

说明：
- 加密货币订单仅支持撤单，不支持改单/失效/生效/删单
- 支持 cancel_all_order() 一键全撤
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
    TrdEnv,
    ModifyOrderOp,
)


def cancel_crypto_order(order_id=None, cancel_all=False, acc_id=None,
                        security_firm=None, output_json=False, confirmed=False):
    acc_id = acc_id or get_default_acc_id()
    firm_enum = parse_security_firm(security_firm)

    if not cancel_all and not order_id:
        msg = "请传入 --order-id 或 --all"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    if cancel_all and not confirmed:
        preview = {
            "action": "cancel_all_crypto_orders_preview",
            "acc_id": acc_id,
            "security_firm": security_firm or "(env default)",
            "message": "将撤销该账户下所有加密货币未完成订单。核实后加 --confirmed 重新执行。",
        }
        if output_json:
            print(json.dumps(preview, ensure_ascii=False))
        else:
            print("=" * 60)
            print("加密货币全部撤单预览（未执行）")
            print("=" * 60)
            print(f"  账户:     {acc_id}")
            print(f"  券商:     {security_firm or '(env default)'}")
            print("=" * 60)
            print("将撤销该账户下所有加密货币未完成订单。请确认后加 --confirmed 重新执行。")
        sys.exit(2)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        if cancel_all:
            ret, data = ctx.cancel_all_order(trd_env=TrdEnv.REAL, acc_id=acc_id)
            check_ret(ret, data, ctx, "加密货币全部撤单")
            result = {"action": "cancel_all", "status": "submitted"}
        else:
            ret, data = ctx.modify_order(ModifyOrderOp.CANCEL, order_id, 0, 0,
                                         trd_env=TrdEnv.REAL, acc_id=acc_id)
            check_ret(ret, data, ctx, f"加密货币撤单(order_id={order_id})")
            result = {"action": "cancel", "order_id": str(order_id), "status": "submitted"}

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"撤单请求已提交: {result}")
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="撤销加密货币订单")
    parser.add_argument("--order-id", default=None, help="订单 ID")
    parser.add_argument("--all", action="store_true", dest="cancel_all", help="撤销所有加密货币订单")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识")
    parser.add_argument("--confirmed", action="store_true", help="--all 时必须同时传入 --confirmed 才执行")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    cancel_crypto_order(order_id=args.order_id, cancel_all=args.cancel_all,
                        acc_id=args.acc_id, security_firm=args.security_firm,
                        output_json=args.output_json, confirmed=args.confirmed)
