#!/usr/bin/env python3
"""
加密货币下单

功能：在加密货币账户下进行现金买入/卖出
注意：Crypto 不支持模拟交易，仅支持实盘。

券商支持：
- FUTUSECURITIES（富途证券 香港）：限价单、市价单
- FUTUINC（富途 美国）：限价单、市价单
- FUTUSG（富途 新加坡）：仅限价单

参数说明：
- code: CC.BTCUSD / CC.ETHUSD / CC.BTCHKD 等币对代码
- qty: 加密货币支持小数数量（如 0.000136）
- price: 限价单必填；市价单忽略
- order_type: NORMAL（限价）/ MARKET（市价）
- time_in_force: 限价单默认 GTC；市价单强制 IOC
- 加密货币不支持交易时段、有效期设置
- 不支持改单，只支持撤单
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_crypto_trade_context,
    parse_trd_env,
    parse_trd_side,
    parse_security_firm,
    get_default_acc_id,
    is_crypto_code,
    parse_qty,
    check_ret,
    safe_close,
    format_enum,
    safe_get,
    safe_int,
    OrderType,
    TimeInForce,
    TrdEnv,
    RET_OK,
    is_empty,
)


def _audit_log(entry):
    """追加交易审计日志到 ~/.futu_trade_audit.jsonl"""
    import datetime
    try:
        log_path = _os.path.join(_os.path.expanduser("~"), ".futu_trade_audit.jsonl")
        entry["timestamp"] = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def place_crypto_order(code, side, quantity, price=None, order_type="NORMAL",
                       acc_id=None, security_firm=None, output_json=False,
                       confirmed=False):
    acc_id = acc_id or get_default_acc_id()
    trd_side = parse_trd_side(side)

    if not is_crypto_code(code):
        msg = f"加密货币下单代码必须是 CC. 开头（例如 CC.BTCUSD），当前: {code}"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    # 下单接口使用的 code 为 'CC.BTCUSD'，SDK 内部兼容；用户传入需包含币对
    base = code.split(".", 1)[1] if "." in code else ""
    if len(base) < 6:  # BTCUSD 这类最少 6 字符；短于此显然不是币对
        msg = f"加密货币下单需使用币对代码（如 CC.BTCUSD），当前: {code}"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    try:
        qty = parse_qty(quantity, code=code)
    except ValueError as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)

    order_type_up = str(order_type).upper()
    if order_type_up == "MARKET":
        order_type_enum = OrderType.MARKET
        price_val = 0.0
        tif_enum = getattr(TimeInForce, "IOC", None) if TimeInForce else None
    else:
        order_type_enum = OrderType.NORMAL
        if price is None:
            print("错误: 限价单必须指定 --price")
            sys.exit(1)
        price_val = float(price)
        tif_enum = getattr(TimeInForce, "GTC", None) if TimeInForce else None

    firm_enum = parse_security_firm(security_firm)

    # 实盘下单硬约束：必须传 --confirmed 才能真正下单（加密货币仅实盘）
    if not confirmed:
        summary = {
            "action": "place_crypto_order_preview",
            "code": code,
            "side": format_enum(trd_side),
            "quantity": qty,
            "price": price_val,
            "order_type": order_type_up,
            "security_firm": security_firm or "(env default)",
            "acc_id": acc_id,
            "message": "加密货币下单为实盘操作。核实订单信息后，加上 --confirmed 参数重新执行。",
        }
        if output_json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print("=" * 60)
            print("加密货币下单预览（未执行）")
            print("=" * 60)
            print(f"  代码:     {code}")
            print(f"  方向:     {format_enum(trd_side)}")
            print(f"  数量:     {qty}")
            print(f"  价格:     {price_val}")
            print(f"  类型:     {order_type_up}")
            print(f"  券商:     {security_firm or '(env default)'}")
            print(f"  账户:     {acc_id}")
            print("=" * 60)
            print("请确认后加 --confirmed 参数重新执行。")
        sys.exit(2)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        order_kwargs = dict(
            price=price_val,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=order_type_enum,
            trd_env=TrdEnv.REAL,
            acc_id=acc_id,
        )
        if tif_enum is not None:
            order_kwargs["time_in_force"] = tif_enum
        ret, data = ctx.place_order(**order_kwargs)
        check_ret(ret, data, ctx, "加密货币下单")

        if hasattr(data, "iloc"):
            row = data.iloc[0]
            order_id = safe_get(row, "order_id", "orderID", default=str(data))
        else:
            order_id = str(data)

        result = {
            "order_id": str(order_id),
            "code": code,
            "side": format_enum(trd_side),
            "quantity": qty,
            "price": price_val,
            "order_type": order_type_up,
            "trd_env": "REAL",
            "acc_id": acc_id,
            "security_firm": security_firm or "(env default)",
            "status": "submitted",
        }

        _audit_log({"action": "place_crypto_order", "result": "success", **result})

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 60)
            print("加密货币下单成功")
            print("=" * 60)
            print(f"  订单 ID:  {order_id}")
            print(f"  代码:     {code}")
            print(f"  方向:     {format_enum(trd_side)}")
            print(f"  数量:     {qty}")
            print(f"  价格:     {price_val}")
            print(f"  类型:     {order_type_up}")
            print("=" * 60)

    except Exception as e:
        _audit_log({"action": "place_crypto_order", "result": "error", "code": code,
                     "side": side, "quantity": quantity, "price": price, "error": str(e)})
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="加密货币下单（买入/卖出币对）")
    parser.add_argument("--code", required=True, help="加密货币币对代码（如 CC.BTCUSD）")
    parser.add_argument("--side", required=True, choices=["BUY", "SELL"], help="交易方向")
    parser.add_argument("--quantity", required=True, help="数量（支持小数，如 0.000136）")
    parser.add_argument("--price", type=float, default=None, help="价格（限价单必填）")
    parser.add_argument("--order-type", default="NORMAL", choices=["NORMAL", "MARKET"], help="订单类型")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识（仅支持 FUTUSECURITIES/FUTUINC/FUTUSG）")
    parser.add_argument("--confirmed", action="store_true", help="确认下单标志（不传则只预览不执行）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    place_crypto_order(code=args.code, side=args.side, quantity=args.quantity,
                       price=args.price, order_type=args.order_type, acc_id=args.acc_id,
                       security_firm=args.security_firm, output_json=args.output_json,
                       confirmed=args.confirmed)
