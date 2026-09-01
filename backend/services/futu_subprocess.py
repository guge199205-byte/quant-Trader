#!/usr/bin/env python3
"""富途 OpenD 子进程执行器（BayMax 自有，直连 OpenD 网关，不经任何外部平台）。

futu SDK 的连接/等待模型与 asyncio 事件循环混用会死锁，故由
backend/services/futu_live.py 以独立子进程方式调用本脚本。

用法:
  python futu_subprocess.py <host> <port> <rsa_key_path> <op> <payload_json> <output_path>

op:
  account      — 查询单 env 账户（资产/持仓）
  account_both — 一次握手查 REAL+SIMULATE 两套账户（省一次 RSA 握手，降 Live 实盘 tab 延迟）
  place        — 下单（payload.order: code/price/quantity/order_type/trd_side/is_hk）
  cancel       — 撤单（payload.order_id）
  orders       — 当日订单历史（成交 tab）
  closed       — 已平仓行（realized_pl）→ Live「已完成」tab 港股
  snapshot     — 实时快照（last_price/prev_close → 当日涨跌）→ 港股盘中分析循环
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")


def _f(v, default: float = 0.0) -> float:
    """Futu DataFrame 数值列可能返回 'N/A' 字符串（如 realized_pl），安全转 float。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _query_account_dict(ctx, trd_env) -> dict:
    """accinfo_query + position_list_query → {total_asset, cash, market_value, positions}。

    供 account / account_both op 复用：同一 ctx 连接查多 env，避免每个 env 各起
    一次子进程（RSA 握手 + OpenSecTradeContext ~4s），account_both 一次握手游走 REAL/SIMULATE。
    """
    out: dict = {}
    ret, data = ctx.accinfo_query(trd_env=trd_env)
    if ret == 0 and len(data):
        row = data.iloc[0]
        out = {
            "total_asset": _f(row.get("total_assets")),
            "cash": _f(row.get("cash")),
            "market_value": _f(row.get("market_val")),
        }
    ret2, plist = ctx.position_list_query(trd_env=trd_env)
    positions = {}
    if ret2 == 0 and len(plist):
        for _, p in plist.iterrows():
            qty = _f(p.get("qty"))
            # Futu 对同一代码返回多行：当前持仓(qty>0) + 已平仓行(qty=0, realized_pl)。
            # 跳过 qty<=0 的已平仓行，否则后入的 0 行会覆盖当前持仓。
            if qty <= 0:
                continue
            code = str(p.get("code", ""))
            mkt_val = _f(p.get("market_val"))
            cost = _f(p.get("cost_price"))
            # nominal_price 是实时价；current_price 列不存在（旧代码读错列恒为 0）
            last_price = _f(p.get("nominal_price"))
            if not last_price and qty and mkt_val:
                last_price = mkt_val / qty
            # 同一代码多行（拆仓/多笔）聚合：累加数量与市值、加权成本
            if code in positions:
                prev = positions[code]
                new_qty = prev["volume"] + qty
                prev["market_value"] = prev["market_value"] + mkt_val
                prev["available_volume"] = prev["available_volume"] + _f(p.get("can_sell_qty"))
                if new_qty:
                    prev["cost"] = (prev["cost"] * (new_qty - qty) + cost * qty) / new_qty
                    prev["price"] = prev["market_value"] / new_qty
                prev["volume"] = new_qty
            else:
                positions[code] = {
                    "volume": qty,
                    "available_volume": _f(p.get("can_sell_qty")),
                    "price": last_price,
                    "market_value": mkt_val,
                    "cost": cost,
                    "name": str(p.get("stock_name") or ""),
                    "currency": str(p.get("currency") or "HKD"),
                }
    out["positions"] = positions
    return out


def _open_ctx(op: str, host: str, port: int):
    """按 op 开对应连接：snapshot 走行情连接（OpenQuoteContext；本 SDK 版本的
    OpenSecTradeContext 不暴露 get_market_snapshot），交易类 op 走 OpenSecTradeContext。"""
    if op == "snapshot":
        from futu import OpenQuoteContext

        return OpenQuoteContext(host=host, port=port, is_encrypt=True)
    from futu import OpenSecTradeContext, TrdMarket

    return OpenSecTradeContext(
        filter_trdmarket=TrdMarket.HK,
        host=host,
        port=port,
        security_firm="FUTUSECURITIES",
        is_encrypt=True,
    )


def main() -> int:
    host, port, rsa_key, op, payload, output_path = (
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3],
        sys.argv[4],
        json.loads(sys.argv[5]),
        sys.argv[6],
    )

    from futu.common.sys_config import SysConfig

    SysConfig.set_init_rsa_file(rsa_key)

    from futu import ModifyOrderOp, OrderType, TrdEnv, TrdSide

    ctx = _open_ctx(op, host, port)
    try:
        env = TrdEnv.REAL if payload.get("env") == "REAL" else TrdEnv.SIMULATE
        out: dict = {}

        if op == "account":
            out = _query_account_dict(ctx, env)

        elif op == "account_both":
            # 一次握手查 REAL+SIMULATE 两套账户（省一次 RSA 握手，降 Live 实盘 tab 延迟）
            out = {
                "real": _query_account_dict(ctx, TrdEnv.REAL),
                "simulate": _query_account_dict(ctx, TrdEnv.SIMULATE),
            }

        elif op == "closed":
            # 已平仓行（qty==0 且 realized_pl!=0）→ Live「已完成」tab 港股
            ret2, plist = ctx.position_list_query(trd_env=env)
            closed = []
            if ret2 == 0 and len(plist):
                for _, p in plist.iterrows():
                    qty = _f(p.get("qty"))
                    realized = _f(p.get("realized_pl"))
                    # 已平仓行：当前持仓 qty==0，但有实现盈亏 realized_pl
                    if qty != 0 or realized == 0:
                        continue
                    closed.append({
                        "code": str(p.get("code", "") or ""),
                        "name": str(p.get("stock_name") or ""),
                        "cost_price": _f(p.get("cost_price")),
                        "last_price": _f(p.get("nominal_price")),
                        "realized_pl": realized,
                        "currency": str(p.get("currency") or "HKD"),
                    })
            out = {"closed": closed}

        elif op == "place":
            order = payload["order"]
            order_type = {
                "MARKET": OrderType.MARKET,
                "NORMAL": OrderType.NORMAL,
            }.get(order["order_type"], OrderType.NORMAL)
            trd_side = {
                "BUY": TrdSide.BUY,
                "SELL": TrdSide.SELL,
            }.get(order["trd_side"], TrdSide.BUY)
            ret, data = ctx.place_order(
                code=order["code"],
                price=float(order["price"]),
                qty=float(order["quantity"]),
                order_type=order_type,
                trd_side=trd_side,
                trd_env=env,
                adjust_limit=0.0 if order.get("is_hk") else None,
            )
            if ret != 0:
                out = {"success": False, "message": str(data)}
            else:
                # place_order 返回单行 DataFrame；提取标量字段。
                # dealt_qty/dealt_avg_price 对 MARKET 单即时成交的模拟单 >0，
                # 透传给上层才能即时落成交记录，否则 SIMULATE 成交丢失。
                row = data.iloc[0] if len(data) else None
                order_id = ""
                status = ""
                filled_qty = 0.0
                filled_price = 0.0
                err_msg = ""
                if row is not None:
                    order_id = str(row.get("order_id", "") or "")
                    status = str(row.get("order_status", "") or "")
                    filled_qty = _f(row.get("dealt_qty"))
                    filled_price = _f(row.get("dealt_avg_price"))
                    err_msg = str(row.get("last_err_msg") or "")
                out = {
                    "success": True,
                    "order_id": order_id,
                    "status": status,
                    "filled_quantity": filled_qty,
                    "filled_price": filled_price,
                    "message": err_msg or "SUBMITTED",
                }

        elif op == "cancel":
            ret, data = ctx.modify_order(
                ModifyOrderOp.CANCEL,
                order_id=payload["order_id"],
                qty=0,
                price=0,
                trd_env=env,
            )
            out = {"success": ret == 0, "message": str(data) if ret != 0 else "CANCELLED"}

        elif op == "orders":
            # 当日订单历史（order_list_query）→ Live 成交 tab
            ret, olist = ctx.order_list_query(trd_env=env)
            orders = []
            if ret == 0 and len(olist):
                for _, o in olist.iterrows():
                    orders.append({
                        "order_id": str(o.get("order_id", "") or ""),
                        "code": str(o.get("code", "") or ""),
                        "name": str(o.get("stock_name", "") or ""),
                        "trd_side": str(o.get("trd_side", "") or ""),
                        "order_type": str(o.get("order_type", "") or ""),
                        "order_status": str(o.get("order_status", "") or ""),
                        "qty": _f(o.get("qty")),
                        "price": _f(o.get("price")),
                        "dealt_qty": _f(o.get("dealt_qty")),
                        "dealt_avg_price": _f(o.get("dealt_avg_price")),
                        "create_time": str(o.get("create_time", "") or ""),
                        "last_err_msg": str(o.get("last_err_msg") or ""),
                    })
            out = {"orders": orders}

        elif op == "snapshot":
            # 实时快照（OpenSecTradeContext 继承 OpenQuoteContext，可直接拉快照，
            # 免订阅）→ 港股盘中分析的现价/昨收（当日涨跌）
            codes = [c for c in payload.get("codes", []) if c]
            ret, data = ctx.get_market_snapshot(code_list=codes)
            snaps = {}
            if ret == 0 and len(data):
                for _, s in data.iterrows():
                    prev = _f(s.get("prev_close_price"))
                    last = _f(s.get("last_price"))
                    snaps[str(s.get("code", "") or "")] = {
                        "name": str(s.get("stock_name") or ""),
                        "last_price": last,
                        "prev_close": prev,
                        "day_chg": (last - prev) / prev * 100 if prev and last else 0.0,
                        "volume": _f(s.get("volume")),
                        "turnover": _f(s.get("turnover")),
                    }
            out = {"snapshot": snaps}

        else:
            out = {"success": False, "message": f"unknown op: {op}"}

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
