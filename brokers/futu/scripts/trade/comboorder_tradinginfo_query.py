#!/usr/bin/env python3
"""
查询组合可交易信息

功能：查询组合订单在指定价格/数量下的可交易信息（保证金、购买力等变动）
用法：python comboorder_tradinginfo_query.py '[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},{"code":"US.AAPL","trd_side":"SELL","qty_ratio":100}]' --price 100 --quantity 1

接口限制：
- 同一账户 ID 每 30 秒最多请求 10 次查询最大可买可卖类接口

参数说明：
- combo_leg_list: 组合腿 JSON 列表，元素字段：code/trd_side/qty_ratio/position_id(可选)
- price: 报价（竞价/市价场景也建议传当前价格）
- qty: 组合数量；每条腿实际数量 = qty * qty_ratio
- order_id: 改单场景可传服务器订单号
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
    parse_security_firm,
    get_default_acc_id,
    get_default_trd_env,
    infer_market_from_code,
    check_ret,
    safe_close,
    safe_get,
    safe_float,
    parse_trd_side,
)


def _resolve_order_type(name):
    from futu import OrderType
    key = str(name).upper()
    val = getattr(OrderType, key, None)
    if val is None:
        raise ValueError(f"不支持的 order_type: {name}")
    return val


def _parse_combo_legs(legs_json):
    from futu import ComboLeg

    try:
        items = json.loads(legs_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"组合腿 JSON 解析失败: {e}")
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("组合腿必须是非空 JSON 数组")

    combo_legs = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 条组合腿必须是对象")
        code = str(item.get("code", "")).strip()
        if not code:
            raise ValueError(f"第 {idx} 条组合腿缺少 code")
        qty_ratio = item.get("qty_ratio", None)
        if qty_ratio is None:
            raise ValueError(f"第 {idx} 条组合腿缺少 qty_ratio")
        trd_side = item.get("trd_side", None)
        if trd_side is None:
            raise ValueError(f"第 {idx} 条组合腿缺少 trd_side")

        leg = ComboLeg()
        leg.code = code
        leg.trd_side = parse_trd_side(str(trd_side))
        leg.qty_ratio = float(qty_ratio)
        if "position_id" in item and item["position_id"] not in (None, ""):
            leg.position_id = int(item["position_id"])
        combo_legs.append(leg)
    return combo_legs


def comboorder_tradinginfo_query(legs_json, price, quantity, order_type="NORMAL",
                                 order_id=None, acc_id=None, trd_env=None,
                                 security_firm=None, ctx_type="SEC", output_json=False):
    acc_id = acc_id or get_default_acc_id()
    trd_env = parse_trd_env(trd_env) if trd_env else get_default_trd_env()

    combo_legs = _parse_combo_legs(legs_json)
    first_code = safe_get(combo_legs[0], "code", default="")
    resolved_ctx = normalize_trade_ctx_type(ctx_type=ctx_type, codes=combo_legs)
    market = None
    if resolved_ctx != "FUTURE":
        market = infer_market_from_code(first_code)
        if not market:
            msg = f"无法从第一条组合腿代码 '{first_code}' 推导交易市场"
            if output_json:
                print(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                print(f"错误: {msg}")
            sys.exit(1)

    order_type_enum = _resolve_order_type(order_type)
    ctx = None
    try:
        ctx = create_sec_or_future_trade_context(
            market, security_firm=parse_security_firm(security_firm),
            ctx_type=resolved_ctx,
        )
        ret, data = ctx.comboorder_tradinginfo_query(
            combo_leg_list=combo_legs,
            price=float(price),
            qty=float(quantity),
            order_type=order_type_enum,
            order_id=order_id,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        check_ret(ret, data, ctx, "查询组合可交易信息")

        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        result = {
            "nlv_change": safe_float(safe_get(row, "nlv_change", default=0.0)),
            "initial_margin_change": safe_float(safe_get(row, "initial_margin_change", default=0.0)),
            "maintenance_margin_change": safe_float(safe_get(row, "maintenance_margin_change", default=0.0)),
            "option_bp": safe_float(safe_get(row, "option_bp", default=0.0)),
            "max_withdraw_change": safe_float(safe_get(row, "max_withdraw_change", default=0.0)),
            "bp_decrease": safe_float(safe_get(row, "bp_decrease", default=0.0)),
        }

        if output_json:
            print(json.dumps({"data": result}, ensure_ascii=False))
        else:
            print("=" * 70)
            print("组合可交易信息")
            print("=" * 70)
            print(f"  nlv_change:                 {result['nlv_change']}")
            print(f"  initial_margin_change:      {result['initial_margin_change']}")
            print(f"  maintenance_margin_change:  {result['maintenance_margin_change']}")
            print(f"  option_bp:                  {result['option_bp']}")
            print(f"  max_withdraw_change:        {result['max_withdraw_change']}")
            print(f"  bp_decrease:                {result['bp_decrease']}")
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
    parser = argparse.ArgumentParser(description="查询组合可交易信息")
    parser.add_argument(
        "legs",
        help='组合腿 JSON，如 \'[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},{"code":"US.AAPL","trd_side":"SELL","qty_ratio":100}]\'',
    )
    parser.add_argument("--price", type=float, required=True, help="报价")
    parser.add_argument("--quantity", type=float, required=True, help="组合数量")
    parser.add_argument("--order-type", default="NORMAL", help="订单类型（默认 NORMAL）")
    parser.add_argument("--order-id", default=None, help="订单号（改单场景可选）")
    parser.add_argument("--acc-id", type=int, default=None, help="账户 ID")
    parser.add_argument("--trd-env", choices=["REAL", "SIMULATE"], default=None, help="交易环境")
    parser.add_argument(
        "--security-firm",
        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG", "FUTUAU", "FUTUCA", "FUTUJP", "FUTUMY"],
        default=None,
        help="券商标识",
    )
    parser.add_argument("--ctx-type", choices=list(TRADE_CTX_TYPE_CHOICES), default="SEC",
                        help="交易上下文：SEC=证券，FUTURE=期货/预测市场；腿含 EC. 时自动用 FUTURE")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    comboorder_tradinginfo_query(
        legs_json=args.legs,
        price=args.price,
        quantity=args.quantity,
        order_type=args.order_type,
        order_id=args.order_id,
        acc_id=args.acc_id,
        trd_env=args.trd_env,
        security_firm=args.security_firm,
        ctx_type=args.ctx_type,
        output_json=args.output_json,
    )
