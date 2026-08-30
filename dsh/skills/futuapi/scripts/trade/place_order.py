#!/usr/bin/env python3
"""
下单

功能：在指定账户下单买入或卖出股票
注意：默认使用模拟账户，真实交易需要指定 --trd-env REAL

接口限制：
- 同一账户 ID 每 30 秒最多请求 15 次
- 连续两次下单间隔不可小于 0.02 秒
- 真实账户需先在 OpenD GUI 界面手动解锁交易密码

参数说明：
- price: 市价单/竞价单仍需传参（可传任意值）。精度：期货整数8位小数9位，美股期权小数2位，美股≤$1允许小数4位，其他小数3位超出四舍五入；预测市场 0.01~0.99 允许2位小数
- qty / --quantity: 期权期货单位是"张"。与 --amount 二选一，同时传时 amount 优先且 qty 置 0
- amount: 订单金额，仅预测市场（EC.）有效；传 amount 时 qty 传 0
- pred_side: 预测市场方向 YES/NO，预测市场下单必填
- code: 期货主连代码会自动转为实际合约代码；预测市场代码为 EC.xxx（无市场前缀），走 OpenFutureTradeContext
- adjust_limit: 正数向上调整，负数向下调整，如 0.015 表示向上调整幅度不超过 1.5%
- remark: 可选备注；未填时传 AISKILL，有内容时传 AISKILL+外部输入（UTF-8 总长上限 64 字节）
- time_in_force: 港股、A 股、环球期货的市价单仅支持当日有效；GTD 时可配 expire_time
- expire_time: 订单到期日 yyyy-MM-dd，仅 time_in_force=GTD 时有效
- fill_outside_rth: 用于港股盘前竞价与美股盘前盘后，盘前盘后时段不支持市价单
- aux_price: 止损/止盈类订单必传
- trail_type/trail_value/trail_spread: 跟踪止损类订单必传
- session: 仅对美股生效，支持 RTH/ETH/OVERNIGHT/ALL
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
    parse_trd_side,
    parse_security_firm,
    get_default_acc_id,
    get_default_trd_env,
    infer_market_from_code,
    check_ret,
    safe_close,
    format_enum,
    safe_get,
    safe_int,
    OrderType,
    Session,
    TimeInForce,
    PredSide,
    RET_OK,
    is_empty,
)

# 下单 session 仅对美股生效
ORDER_SESSION_MAP = {
    "NONE": Session.NONE,
    "RTH": Session.RTH,
    "ETH": Session.ETH,
    "OVERNIGHT": Session.OVERNIGHT,
    "ALL": Session.ALL,
}


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


def _fail(msg, output_json=False, code=1):
    if output_json:
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(f"错误: {msg}")
    sys.exit(code)


def _resolve_time_in_force(name):
    if TimeInForce is None:
        raise ValueError("当前 SDK 不支持 TimeInForce")
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


def _build_remark(remark):
    """未填 remark 时为 AISKILL；有外部输入时为 AISKILL+外部内容。"""
    user_remark = str(remark or "").strip()
    if not user_remark:
        return "AISKILL"
    return f"AISKILL{user_remark}"


def place_order(code, side, quantity=None, price=None, order_type="NORMAL",
                acc_id=None, trd_env=None, security_firm=None, output_json=False,
                confirmed=False, fill_outside_rth=False, session_str="NONE",
                amount=None, pred_side=None, time_in_force="DAY", expire_time=None,
                remark=""):
    acc_id = acc_id or get_default_acc_id()
    trd_env = parse_trd_env(trd_env) if trd_env else get_default_trd_env()
    trd_side = parse_trd_side(side)
    is_ec = is_event_contract_code(code)
    firm_enum = parse_security_firm(security_firm)

    if is_ec and format_enum(trd_env) == "SIMULATE":
        _fail("模拟交易不支持预测市场，请使用 --trd-env REAL", output_json)

    if is_ec and not pred_side:
        _fail("预测市场下单必须指定 --pred-side YES 或 NO", output_json)

    pred_side_enum = None
    if pred_side:
        try:
            pred_side_enum = _resolve_pred_side(pred_side)
        except ValueError as e:
            _fail(str(e), output_json)

    if amount is not None:
        if not is_ec:
            _fail("--amount 仅预测市场（EC.）可用", output_json)
        qty = 0
    elif quantity is None:
        _fail("必须指定 --quantity，或预测市场使用 --amount", output_json)
    else:
        try:
            if int(quantity) <= 0:
                raise ValueError
            qty = int(quantity)
        except (ValueError, TypeError):
            _fail("数量必须为正整数", output_json)

    if not is_ec:
        market = infer_market_from_code(code)
        if not market:
            _fail(
                f"无法从代码 '{code}' 推导交易市场，请使用完整格式如 US.AAPL、HK.00700、SG.D05、MY.1155、JP.7203；"
                f"预测市场请使用 EC.xxx",
                output_json,
            )
    else:
        market = None

    if str(order_type).upper() == "MARKET":
        order_type_enum = OrderType.MARKET
        price = 0.0
    else:
        order_type_enum = OrderType.NORMAL
        if price is None:
            _fail("限价单必须指定 --price", output_json)

    try:
        tif_enum = _resolve_time_in_force(time_in_force)
    except ValueError as e:
        _fail(str(e), output_json)

    tif_name = str(time_in_force).upper()
    if tif_name == "GTD" and not expire_time:
        _fail("time_in_force=GTD 时必须指定 --expire-time（yyyy-MM-dd）", output_json)
    if expire_time and tif_name != "GTD":
        _fail("--expire-time 仅在 --time-in-force GTD 时有效", output_json)

    # 实盘下单硬约束：必须传 --confirmed 才能真正下单
    if format_enum(trd_env) == "REAL" and not confirmed:
        summary = {
            "action": "place_order_preview",
            "code": code,
            "side": format_enum(trd_side),
            "quantity": qty,
            "amount": amount,
            "pred_side": str(pred_side).upper() if pred_side else None,
            "price": price,
            "order_type": str(order_type).upper(),
            "time_in_force": tif_name,
            "expire_time": expire_time,
            "trd_env": "REAL",
            "acc_id": acc_id,
            "is_event_contract": is_ec,
            "message": "实盘下单需要确认。请核实订单信息后，加上 --confirmed 参数重新执行。",
        }
        if output_json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print("=" * 60)
            print("实盘下单预览（未执行）")
            print("=" * 60)
            print(f"  代码:     {code}")
            print(f"  方向:     {format_enum(trd_side)}")
            if amount is not None:
                print(f"  金额:     {amount}")
            else:
                print(f"  数量:     {qty}")
            if pred_side:
                print(f"  预测市场方向: {str(pred_side).upper()}")
            print(f"  价格:     {price}")
            print(f"  类型:     {order_type}")
            print(f"  有效期:   {tif_name}")
            if expire_time:
                print(f"  到期日:   {expire_time}")
            print(f"  账户:     {acc_id}")
            print("=" * 60)
            print("请确认后加 --confirmed 参数重新执行。")
        sys.exit(2)

    ctx = None
    try:
        if is_ec:
            ctx = create_future_trade_context(security_firm=firm_enum)
        else:
            ctx = create_trade_context(market, security_firm=firm_enum)

        # 校验账户角色：MASTER 不允许下单；预测市场需 PREDICTION 权限
        if acc_id:
            ret, acc_data = ctx.get_acc_list()
            if ret == RET_OK and not is_empty(acc_data):
                matched = False
                has_any_prediction = False
                for i in range(len(acc_data)):
                    row = acc_data.iloc[i] if hasattr(acc_data, "iloc") else acc_data[i]
                    if _account_has_prediction(row):
                        has_any_prediction = True
                    row_acc_id = safe_int(safe_get(row, "acc_id", default=0))
                    if row_acc_id != safe_int(acc_id):
                        continue
                    matched = True
                    acc_role = format_enum(safe_get(row, "acc_role", default=""))
                    if acc_role.upper() == "MASTER":
                        _fail("主账户（MASTER）不允许下单，请选择非主账户", output_json)
                    if is_ec and not _account_has_prediction(row):
                        if not has_any_prediction:
                            # 再扫一遍确认是否全无 PREDICTION
                            for j in range(len(acc_data)):
                                r2 = acc_data.iloc[j] if hasattr(acc_data, "iloc") else acc_data[j]
                                if _account_has_prediction(r2):
                                    has_any_prediction = True
                                    break
                        if not has_any_prediction:
                            _fail(
                                "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
                                output_json,
                            )
                        _fail(
                            f"账户 {acc_id} 的 trdmarket_auth 不含 PREDICTION，无法交易预测市场",
                            output_json,
                        )
                    break
                if is_ec and not matched:
                    # 账户未匹配时仍检查是否具备 PREDICTION 能力
                    if not has_any_prediction:
                        for i in range(len(acc_data)):
                            row = acc_data.iloc[i] if hasattr(acc_data, "iloc") else acc_data[i]
                            if _account_has_prediction(row):
                                has_any_prediction = True
                                break
                    if not has_any_prediction:
                        _fail(
                            "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
                            output_json,
                        )
        elif is_ec:
            ret, acc_data = ctx.get_acc_list()
            if ret == RET_OK and not is_empty(acc_data):
                if not any(
                    _account_has_prediction(
                        acc_data.iloc[i] if hasattr(acc_data, "iloc") else acc_data[i]
                    )
                    for i in range(len(acc_data))
                ):
                    _fail(
                        "当前不支持交易预测市场（期货账户 trdmarket_auth 均无 PREDICTION）",
                        output_json,
                    )

        session = ORDER_SESSION_MAP.get(session_str.upper(), Session.NONE)
        order_kwargs = dict(
            price=float(price),
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=order_type_enum,
            trd_env=trd_env,
            acc_id=acc_id,
            time_in_force=tif_enum,
            remark=_build_remark(remark),
        )
        if fill_outside_rth:
            order_kwargs["fill_outside_rth"] = True
        if session != Session.NONE:
            order_kwargs["session"] = session
        if expire_time:
            order_kwargs["expire_time"] = expire_time
        if amount is not None:
            order_kwargs["amount"] = float(amount)
        if pred_side_enum is not None:
            order_kwargs["pred_side"] = pred_side_enum

        ret, data = ctx.place_order(**order_kwargs)
        check_ret(ret, data, ctx, "下单")

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
            "amount": amount,
            "pred_side": str(pred_side).upper() if pred_side else None,
            "price": price,
            "order_type": str(order_type).upper(),
            "time_in_force": tif_name,
            "expire_time": expire_time,
            "trd_env": format_enum(trd_env),
            "status": "submitted",
        }

        _audit_log({"action": "place_order", "result": "success", **result})

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 60)
            print("下单成功")
            print("=" * 60)
            print(f"  订单 ID:  {order_id}")
            print(f"  代码:     {code}")
            print(f"  方向:     {format_enum(trd_side)}")
            if amount is not None:
                print(f"  金额:     {amount}")
            else:
                print(f"  数量:     {qty}")
            if pred_side:
                print(f"  预测市场方向: {str(pred_side).upper()}")
            print(f"  价格:     {price}")
            print(f"  类型:     {order_type}")
            print(f"  环境:     {format_enum(trd_env)}")
            print("=" * 60)

    except Exception as e:
        _audit_log({"action": "place_order", "result": "error", "code": code,
                     "side": side, "quantity": quantity, "amount": amount,
                     "price": price, "error": str(e)})
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下单（买入/卖出股票/预测市场）")
    parser.add_argument("--code", required=True, help="标的代码（如 US.AAPL；预测市场 EC.xxx）")
    parser.add_argument("--side", required=True, choices=["BUY", "SELL"], help="交易方向")
    parser.add_argument("--quantity", type=int, default=None, help="数量（与 --amount 二选一；传 amount 时 qty=0）")
    parser.add_argument("--amount", type=float, default=None, help="订单金额（仅预测市场；与 quantity 二选一，优先）")
    parser.add_argument("--pred-side", choices=["YES", "NO"], default=None, dest="pred_side",
                        help="预测市场方向（预测市场下单必填）")
    parser.add_argument("--price", type=float, default=None, help="价格（限价单必填）")
    parser.add_argument("--order-type", default="NORMAL", choices=["NORMAL", "MARKET"], help="订单类型")
    parser.add_argument("--time-in-force", default="DAY", dest="time_in_force",
                        help="有效期限（默认 DAY；GTD 需配合 --expire-time）")
    parser.add_argument("--expire-time", default=None, dest="expire_time",
                        help="订单到期日 yyyy-MM-dd（仅 time_in_force=GTD 时有效）")
    parser.add_argument("--acc-id", type=int, default=None, help="账户 ID")
    parser.add_argument("--trd-env", choices=["REAL", "SIMULATE"], default=None, help="交易环境")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG", "FUTUAU", "FUTUCA", "FUTUJP", "FUTUMY"],
                        default=None, help="券商标识")
    parser.add_argument("--fill-outside-rth", action="store_true",
                        help="允许盘前盘后成交（美股盘前盘后、港股竞价盘）")
    parser.add_argument("--session", choices=["NONE", "RTH", "ETH", "OVERNIGHT", "ALL"],
                        default="NONE", help="美股交易时段（仅对美股生效）")
    parser.add_argument("--remark", default="", help="备注（未填默认 AISKILL；有内容时为 AISKILL+备注，UTF-8 最长 64 字节）")
    parser.add_argument("--confirmed", action="store_true", help="实盘下单确认标志（不传则只预览不执行）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    place_order(code=args.code, side=args.side, quantity=args.quantity, price=args.price,
                order_type=args.order_type, acc_id=args.acc_id,
                trd_env=args.trd_env, security_firm=args.security_firm,
                output_json=args.output_json, confirmed=args.confirmed,
                fill_outside_rth=args.fill_outside_rth, session_str=args.session,
                amount=args.amount, pred_side=args.pred_side,
                time_in_force=args.time_in_force, expire_time=args.expire_time,
                remark=args.remark)
