#!/usr/bin/env python3
"""
组合下单

功能：提交组合期权/组合策略/预测市场组合订单
用法：
  # 组合期权
  python place_combo_order.py '[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},{"code":"US.AAPL","trd_side":"SELL","qty_ratio":100}]' --price 9.9 --quantity 1
  # 预测市场组合（全部腿为 EC.，必填 quote_id 与每腿 pred_side）
  python place_combo_order.py '[{"code":"EC.xxx","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"},{"code":"EC.yyy","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"}]' --price 0.55 --quantity 1 --quote-id {id} --trd-env REAL --confirmed

接口限制：
- 同一账户 ID 每 30 秒最多请求 15 次
- 连续两次下单间隔不可小于 0.02 秒
- 与 place_order 共用一个限频

参数说明：
- combo_leg_list: 组合腿 JSON；期权：code/trd_side/qty_ratio/position_id(可选)；预测市场另须 pred_side
- quote_id: 预测市场 Combo 询价返回的报价 ID（预测市场组合必填）；组合期权忽略
- price: 订单价格；预测市场须取自 request_combo_quotes 的 ask/bid
- qty: 组合数量；每条腿实际数量 = qty * qty_ratio
- time_in_force: 默认 DAY；GTD 时可配合 expire_time
- remark: 可选备注；未填时传 AISKILL，有内容时传 AISKILL+外部输入（UTF-8 总长上限 64 字节）
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_trade_context,
    create_future_trade_context,
    is_event_contract_code,
    parse_trd_env,
    parse_security_firm,
    get_default_acc_id,
    get_default_trd_env,
    infer_market_from_code,
    check_ret,
    safe_close,
    format_enum,
    safe_get,
    safe_float,
    safe_int,
    parse_trd_side,
    is_empty,
    PredSide,
    RET_OK,
)


def _audit_log(entry):
    import datetime
    try:
        log_path = _os.path.join(_os.path.expanduser("~"), ".futu_trade_audit.jsonl")
        entry["timestamp"] = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _fail(msg, output_json=False, code=1):
    if output_json:
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(f"错误: {msg}")
    sys.exit(code)


def _resolve_order_type(name):
    from futu import OrderType
    key = str(name).upper()
    val = getattr(OrderType, key, None)
    if val is None:
        raise ValueError(f"不支持的 order_type: {name}")
    return val


def _resolve_time_in_force(name):
    from futu import TimeInForce
    key = str(name).upper()
    val = getattr(TimeInForce, key, None)
    if val is None:
        raise ValueError(f"不支持的 time_in_force: {name}")
    return val


def _resolve_pred_side(name):
    if PredSide is None:
        raise ValueError("当前 SDK 不支持 PredSide，请升级 futu-api")
    key = str(name).upper()
    val = getattr(PredSide, key, None)
    if val is None or key == "UNKNOWN":
        raise ValueError(f"不支持的 pred_side: {name}，可选 YES/NO")
    return val


def _parse_trdmarket_auth(row):
    raw = safe_get(row, "trdmarket_auth", default=[])
    if isinstance(raw, str):
        return [s.strip().upper() for s in raw.strip("[]").split(",") if s.strip()]
    if isinstance(raw, list):
        return [format_enum(m).upper() for m in raw]
    return []


def _account_has_prediction(row):
    return "PREDICTION" in _parse_trdmarket_auth(row)


def _classify_combo_legs(items):
    """返回 is_ec；混合 EC/非 EC 则抛错。"""
    flags = [is_event_contract_code(str(it.get("code", "")).strip()) for it in items]
    if all(flags):
        return True
    if not any(flags):
        return False
    raise ValueError("组合不合法：预测市场腿（EC.）与非预测市场腿不能混用，全部腿须同为 EC. 或全部不是")


def _parse_combo_legs(legs_json):
    from futu import ComboLeg

    try:
        items = json.loads(legs_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"组合腿 JSON 解析失败: {e}")
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("组合腿必须是非空 JSON 数组")

    is_ec = _classify_combo_legs(items)

    combo_legs = []
    side_names = []
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

        side_enum = parse_trd_side(str(trd_side))
        side_name = str(trd_side).strip().upper()
        side_names.append(side_name)

        leg = ComboLeg()
        leg.code = code
        leg.trd_side = side_enum
        leg.qty_ratio = float(qty_ratio)
        if "position_id" in item and item["position_id"] not in (None, ""):
            leg.position_id = int(item["position_id"])

        if is_ec:
            pred = item.get("pred_side")
            if pred in (None, ""):
                raise ValueError(f"预测市场组合第 {idx} 条腿缺少 pred_side（YES/NO）")
            leg.pred_side = _resolve_pred_side(pred)

        combo_legs.append(leg)

    if is_ec and len(set(side_names)) > 1:
        raise ValueError(
            f"预测市场组合所有腿的 trd_side 必须完全一致，当前为: {side_names}"
        )

    return combo_legs, is_ec


def _validate_prediction_account(ctx, acc_id, output_json):
    ret, acc_data = ctx.get_acc_list()
    if ret != RET_OK or is_empty(acc_data):
        return
    has_any = False
    matched = False
    for i in range(len(acc_data)):
        row = acc_data.iloc[i] if hasattr(acc_data, "iloc") else acc_data[i]
        if _account_has_prediction(row):
            has_any = True
        if acc_id and safe_int(safe_get(row, "acc_id", default=0)) == safe_int(acc_id):
            matched = True
            if format_enum(safe_get(row, "acc_role", default="")).upper() == "MASTER":
                _fail("主账户（MASTER）不允许下单，请选择非主账户", output_json)
            if not _account_has_prediction(row):
                if not has_any:
                    for j in range(len(acc_data)):
                        r2 = acc_data.iloc[j] if hasattr(acc_data, "iloc") else acc_data[j]
                        if _account_has_prediction(r2):
                            has_any = True
                            break
                if not has_any:
                    _fail(
                        "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
                        output_json,
                    )
                _fail(
                    f"账户 {acc_id} 的 trdmarket_auth 不含 PREDICTION，无法交易预测市场组合",
                    output_json,
                )
    if not has_any:
        _fail(
            "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
            output_json,
        )
    if acc_id and not matched and not has_any:
        _fail(
            "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
            output_json,
        )


def _build_remark(remark):
    """未填 remark 时为 AISKILL；有外部输入时为 AISKILL+外部内容。"""
    user_remark = str(remark or "").strip()
    if not user_remark:
        return "AISKILL"
    return f"AISKILL{user_remark}"


def place_combo_order(legs_json, price, quantity, order_type="NORMAL",
                      acc_id=None, trd_env=None, security_firm=None, remark="",
                      time_in_force="DAY", expire_time=None, confirmed=False,
                      quote_id=None, output_json=False):
    acc_id = acc_id or get_default_acc_id()
    trd_env = parse_trd_env(trd_env) if trd_env else get_default_trd_env()
    firm_enum = parse_security_firm(security_firm)

    try:
        combo_legs, is_ec = _parse_combo_legs(legs_json)
    except ValueError as e:
        _fail(str(e), output_json)

    # 组合期权静默忽略 quote_id；预测市场必填
    if is_ec:
        if not quote_id:
            _fail("预测市场组合下单必须指定 --quote-id（来自 request_combo_quotes）", output_json)
        if format_enum(trd_env) == "SIMULATE":
            _fail("模拟交易不支持预测市场组合，请使用 --trd-env REAL", output_json)
        market = None
    else:
        quote_id = None
        first_code = safe_get(combo_legs[0], "code", default="")
        market = infer_market_from_code(first_code)
        if not market:
            _fail(f"无法从第一条组合腿代码 '{first_code}' 推导交易市场", output_json)

    try:
        order_type_enum = _resolve_order_type(order_type)
        tif_enum = _resolve_time_in_force(time_in_force)
    except ValueError as e:
        _fail(str(e), output_json)

    try:
        if float(quantity) <= 0:
            raise ValueError
    except (ValueError, TypeError):
        _fail("quantity 必须为正数", output_json)

    tif_name = str(time_in_force).upper()
    if tif_name == "GTD" and not expire_time:
        _fail("time_in_force=GTD 时必须指定 --expire-time（yyyy-MM-dd）", output_json)
    if expire_time and tif_name != "GTD":
        _fail("--expire-time 仅在 --time-in-force GTD 时有效", output_json)

    if format_enum(trd_env) == "REAL" and not confirmed:
        preview = {
            "action": "place_combo_order_preview",
            "legs": json.loads(legs_json),
            "price": float(price),
            "quantity": float(quantity),
            "order_type": str(order_type).upper(),
            "time_in_force": tif_name,
            "expire_time": expire_time,
            "quote_id": quote_id,
            "is_event_contract": is_ec,
            "trd_env": "REAL",
            "acc_id": acc_id,
            "message": "实盘组合下单需要确认。请核实后加 --confirmed 重新执行。",
        }
        if output_json:
            print(json.dumps(preview, ensure_ascii=False))
        else:
            print("=" * 60)
            print("实盘组合下单预览（未执行）")
            print("=" * 60)
            print(f"  价格:       {price}")
            print(f"  数量:       {quantity}")
            print(f"  订单类型:   {order_type}")
            print(f"  有效期:     {time_in_force}")
            if expire_time:
                print(f"  过期时间:   {expire_time}")
            if quote_id:
                print(f"  quote_id:   {quote_id}")
            print(f"  账户:       {acc_id}")
            print(f"  组合腿数:   {len(combo_legs)}")
            print("=" * 60)
            print("请确认后加 --confirmed 参数重新执行。")
        sys.exit(2)

    ctx = None
    try:
        if is_ec:
            ctx = create_future_trade_context(security_firm=firm_enum)
            _validate_prediction_account(ctx, acc_id, output_json)
        else:
            ctx = create_trade_context(market, security_firm=firm_enum)

        order_kwargs = dict(
            combo_leg_list=combo_legs,
            price=float(price),
            qty=float(quantity),
            order_type=order_type_enum,
            trd_env=trd_env,
            acc_id=acc_id,
            remark=_build_remark(remark),
            time_in_force=tif_enum,
            expire_time=expire_time,
        )
        if quote_id:
            order_kwargs["quote_id"] = quote_id

        ret, data = ctx.place_combo_order(**order_kwargs)
        check_ret(ret, data, ctx, "组合下单")

        if is_empty(data):
            result = {
                "status": "submitted",
                "message": "下单成功，但未返回订单详情",
            }
        else:
            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            result = {
                "order_id": str(safe_get(row, "order_id", default="")),
                "code": str(safe_get(row, "code", default="")),
                "strategy_type": str(safe_get(row, "strategy_type", default="")),
                "trd_side": str(safe_get(row, "trd_side", default="")),
                "order_type": str(safe_get(row, "order_type", default="")),
                "order_status": str(safe_get(row, "order_status", default="")),
                "qty": safe_float(safe_get(row, "qty", default=0.0)),
                "price": safe_float(safe_get(row, "price", default=0.0)),
                "amount": safe_float(safe_get(row, "amount", default=0.0)),
                "time_in_force": str(safe_get(row, "time_in_force", default="")),
                "expire_time": str(safe_get(row, "expire_time", default="")),
                "dealt_qty": safe_float(safe_get(row, "dealt_qty", default=0.0)),
                "dealt_avg_price": safe_float(safe_get(row, "dealt_avg_price", default=0.0)),
                "create_time": str(safe_get(row, "create_time", default="")),
                "updated_time": str(safe_get(row, "updated_time", default="")),
                "last_err_msg": str(safe_get(row, "last_err_msg", default="")),
                "remark": str(safe_get(row, "remark", default="")),
                "quote_id": quote_id,
            }

        _audit_log({"action": "place_combo_order", "result": "success", **result})

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 70)
            print("组合下单成功")
            print("=" * 70)
            print(f"  订单 ID:       {result.get('order_id', '')}")
            print(f"  组合代码:       {result.get('code', '')}")
            print(f"  策略类型:       {result.get('strategy_type', '')}")
            print(f"  方向:           {result.get('trd_side', '')}")
            print(f"  数量:           {result.get('qty', '')}")
            print(f"  价格:           {result.get('price', '')}")
            print(f"  状态:           {result.get('order_status', '')}")
            print("=" * 70)

    except Exception as e:
        _audit_log({
            "action": "place_combo_order",
            "result": "error",
            "legs": json.loads(legs_json) if legs_json else [],
            "price": price,
            "quantity": quantity,
            "quote_id": quote_id,
            "error": str(e),
        })
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="组合下单（组合期权/策略/预测市场组合）")
    parser.add_argument(
        "legs",
        help='组合腿 JSON。期权例：\'[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},...]\'；'
             '预测市场须全部 EC. 且含 pred_side',
    )
    parser.add_argument("--price", type=float, required=True,
                        help="订单价格（预测市场须取自 request_combo_quotes 的 ask/bid）")
    parser.add_argument("--quantity", type=float, required=True, help="组合数量")
    parser.add_argument("--quote-id", default=None, dest="quote_id",
                        help="报价 ID（预测市场 Combo 询价返回；预测市场组合必填；组合期权忽略）")
    parser.add_argument("--order-type", default="NORMAL", help="订单类型（默认 NORMAL）")
    parser.add_argument("--acc-id", type=int, default=None, help="账户 ID")
    parser.add_argument("--trd-env", choices=["REAL", "SIMULATE"], default=None, help="交易环境")
    parser.add_argument(
        "--security-firm",
        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG", "FUTUAU", "FUTUCA", "FUTUJP", "FUTUMY"],
        default=None,
        help="券商标识",
    )
    parser.add_argument("--remark", default="", help="备注（未填默认 AISKILL；有内容时为 AISKILL+备注，UTF-8 最长 64 字节）")
    parser.add_argument("--time-in-force", default="DAY", help="有效期限（默认 DAY）")
    parser.add_argument("--expire-time", default=None, help="过期时间（yyyy-MM-dd，仅 GTD 时有效）")
    parser.add_argument("--confirmed", action="store_true", help="实盘下单确认标志（不传则只预览不执行）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    place_combo_order(
        legs_json=args.legs,
        price=args.price,
        quantity=args.quantity,
        order_type=args.order_type,
        acc_id=args.acc_id,
        trd_env=args.trd_env,
        security_firm=args.security_firm,
        remark=args.remark,
        time_in_force=args.time_in_force,
        expire_time=args.expire_time,
        confirmed=args.confirmed,
        quote_id=args.quote_id,
        output_json=args.output_json,
    )
