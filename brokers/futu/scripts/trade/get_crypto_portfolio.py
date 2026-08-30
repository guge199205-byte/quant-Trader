#!/usr/bin/env python3
"""
获取加密货币账户持仓与资金

功能：查询加密货币账户的资金状况和持仓列表（仅实盘）

新增字段（相对证券账户）：
- crypto_mv: 加密货币市值
- exposure_level: 持仓限额状态（NORMAL/NEAR_LIMIT/RESTRICTED/SAFE/MODERATE/WARNING/MARGIN_CALL）
- exposure_limit: 持仓限额（USD）
- used_limit: 已用持仓限额（USD）
- remaining_limit: 剩余持仓限额（USD）

持仓字段：
- code: CC.{Base currency}，如 CC.BTC
- qty: 持仓数量（支持小数）
- currency: 持仓币种，默认 USD
- position_side: 固定为 LONG
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
    safe_get,
    safe_float,
    format_enum,
    TrdEnv,
)


CRYPTO_ACC_FIELDS = [
    "total_assets", "cash", "crypto_mv", "frozen_cash",
    "hk_cash", "us_cash", "sg_cash",
    "hk_avl_withdrawal_cash", "us_avl_withdrawal_cash", "sg_avl_withdrawal_cash",
    "hkd_net_cash_power", "usd_net_cash_power", "sgd_net_cash_power",
    "exposure_level", "exposure_limit", "used_limit", "remaining_limit",
]

CRYPTO_POS_FIELDS = [
    "code", "stock_name", "qty", "can_sell_qty",
    "cost_price", "average_cost", "diluted_cost",
    "market_val", "nominal_price", "pl_ratio", "pl_ratio_avg_cost", "pl_val",
    "position_side", "unrealized_pl", "realized_pl", "currency",
]


def get_crypto_portfolio(acc_id=None, security_firm=None, output_json=False):
    acc_id = acc_id or get_default_acc_id()
    firm_enum = parse_security_firm(security_firm)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)

        ret, acc_data = ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=acc_id, refresh_cache=True)
        check_ret(ret, acc_data, ctx, "查询加密货币账户资金")

        ret, pos_data = ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id, refresh_cache=True)
        check_ret(ret, pos_data, ctx, "查询加密货币持仓")

        funds = {}
        if not is_empty(acc_data):
            row = acc_data.iloc[0] if hasattr(acc_data, "iloc") else acc_data[0]
            for f in CRYPTO_ACC_FIELDS:
                val = safe_get(row, f, default=None)
                if f == "exposure_level":
                    funds[f] = format_enum(val) if val is not None else "N/A"
                else:
                    funds[f] = safe_float(val) if val is not None else None

        positions = []
        if not is_empty(pos_data):
            for i in range(len(pos_data)):
                row = pos_data.iloc[i] if hasattr(pos_data, "iloc") else pos_data[i]
                pos = {}
                for f in CRYPTO_POS_FIELDS:
                    val = safe_get(row, f, default=None)
                    if f in ("code", "stock_name", "position_side", "currency"):
                        pos[f] = format_enum(val) if val is not None else ""
                    else:
                        pos[f] = safe_float(val) if val is not None else None
                positions.append(pos)

        result = {"acc_id": acc_id, "funds": funds, "positions": positions}
        if output_json:
            print(json.dumps(result, ensure_ascii=False))
            return

        print("=" * 70)
        print(f"加密货币账户 {acc_id} 资金与持仓")
        print("=" * 70)
        print("\n[资金]")
        for k, v in funds.items():
            print(f"  {k}: {v}")
        print(f"\n[持仓] 共 {len(positions)} 条")
        for p in positions:
            print(f"  {p.get('code')} {p.get('stock_name')} 数量={p.get('qty')} 市值={p.get('market_val')} 均价={p.get('average_cost')} 盈亏={p.get('unrealized_pl')}")
        print("=" * 70)

    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="加密货币账户持仓与资金")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_portfolio(acc_id=args.acc_id, security_firm=args.security_firm,
                         output_json=args.output_json)
