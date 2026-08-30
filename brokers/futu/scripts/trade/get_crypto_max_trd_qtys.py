#!/usr/bin/env python3
"""
查询加密货币最大可买卖数量

功能：查询指定币对的最大可买/可卖数量（仅现金账户，仅实盘）
用法：python get_crypto_max_trd_qtys.py --code CC.BTCUSD --price 72873.22

注意事项：
- 加密货币只有现金账户，不支持融资融券购买力（无 max_cash_and_margin_buy）
- 仅支持实盘（TrdEnv.REAL），不支持模拟交易
- code 必须为币对（CC.BTCUSD / CC.ETHUSD / CC.BTCHKD 等），不接受 CC.BTC 这类币种
- 券商仅支持 FUTUSECURITIES / FUTUINC / FUTUSG
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
    is_crypto_code,
    check_ret,
    safe_close,
    is_empty,
    safe_get,
    safe_float,
    OrderType,
    TrdEnv,
)


def get_crypto_max_trd_qtys(code, price, acc_id=None, security_firm=None, output_json=False):
    acc_id = acc_id or get_default_acc_id()

    if not is_crypto_code(code):
        msg = f"加密货币最大可买卖查询代码必须为 CC. 开头（如 CC.BTCUSD），当前: {code}"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    base = code.split(".", 1)[1] if "." in code else ""
    if len(base) < 6:
        msg = f"加密货币最大可买卖需使用币对代码（如 CC.BTCUSD），不支持币种代码 {code}"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    firm_enum = parse_security_firm(security_firm)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        ret, data = ctx.acctradinginfo_query(
            order_type=OrderType.NORMAL,
            code=code,
            price=price,
            trd_env=TrdEnv.REAL,
            acc_id=acc_id,
        )
        check_ret(ret, data, ctx, "查询加密货币最大可买卖数量")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": {}}, ensure_ascii=False))
            else:
                print("无数据")
            return

        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        result = {
            "code": code,
            "price": price,
            "acc_id": acc_id,
            "max_cash_buy": safe_float(safe_get(row, "max_cash_buy", default=0)),
            "max_position_sell": safe_float(safe_get(row, "max_position_sell", default=0)),
        }

        if output_json:
            print(json.dumps({"data": result}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"加密货币最大可买卖数量 - {code} @ {price}")
            print("=" * 70)
            print(f"  账户:           {acc_id}")
            print(f"  最大现金可买:   {result['max_cash_buy']}")
            print(f"  最大可卖:       {result['max_position_sell']}")
            print("  说明: 加密货币仅支持现金账户，无融资融券购买力")
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
    parser = argparse.ArgumentParser(description="查询加密货币最大可买卖数量（仅现金账户）")
    parser.add_argument("--code", required=True, help="加密货币币对代码（如 CC.BTCUSD）")
    parser.add_argument("--price", type=float, required=True, help="目标价格")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识（仅支持 FUTUSECURITIES/FUTUINC/FUTUSG）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_max_trd_qtys(code=args.code, price=args.price, acc_id=args.acc_id,
                            security_firm=args.security_firm, output_json=args.output_json)
