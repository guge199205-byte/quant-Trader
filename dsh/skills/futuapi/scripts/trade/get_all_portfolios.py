#!/usr/bin/env python3
"""
查询所有账户的资金与持仓

功能：遍历所有交易账户（证券 + 期货），查询每个账户的资金和持仓信息
用法：python get_all_portfolios.py [--trd-env SIMULATE] [--acc-id 6795352] [--json]

参数说明：
- --trd-env: 交易环境过滤，SIMULATE 或 REAL（默认显示全部）
- --acc-id: 指定账户 ID，只查询该账户
- --show-option-strategy-view: 按期权策略视角查询持仓
- --json: JSON 格式输出
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    parse_trd_env,
    check_ret,
    safe_close,
    is_empty,
    safe_get,
    safe_float,
    safe_int,
    format_enum,
    _sdk_supports_ai_type,
    RET_OK,
    TrdEnv,
    TrdMarket,
    SecurityFirm,
)


# 所有券商枚举
ALL_FIRMS = [
    SecurityFirm.FUTUSECURITIES,
    SecurityFirm.FUTUINC,
    SecurityFirm.FUTUSG,
    SecurityFirm.FUTUAU,
    SecurityFirm.FUTUCA,
    SecurityFirm.FUTUJP,
    SecurityFirm.FUTUMY,
]


def get_all_accounts(host, port):
    """获取所有账户列表（证券 + 期货，按 acc_id 去重）"""
    from common import OpenSecTradeContext, OpenFutureTradeContext

    seen = set()
    accounts = []

    def _collect(ctx_cls, ctx_type, firm):
        if ctx_cls is None:
            return
        try:
            kwargs = dict(host=host, port=port, security_firm=firm)
            if ctx_cls is OpenSecTradeContext:
                kwargs["filter_trdmarket"] = TrdMarket.NONE
            if _sdk_supports_ai_type:
                kwargs["ai_type"] = 1
            ctx = ctx_cls(**kwargs)
            try:
                ret, data = ctx.get_acc_list()
            finally:
                safe_close(ctx)
            if ret != RET_OK or is_empty(data):
                return
            for i in range(len(data)):
                row = data.iloc[i]
                acc_id = safe_int(safe_get(row, "acc_id", default=0))
                if not acc_id or acc_id in seen:
                    continue
                seen.add(acc_id)
                accounts.append({
                    "acc_id": acc_id,
                    "trd_env": safe_get(row, "trd_env", default="N/A"),
                    "acc_type": safe_get(row, "acc_type", default="N/A"),
                    "trdmarket_auth": safe_get(row, "trdmarket_auth", default=[]),
                    "ctx_type": ctx_type,
                })
        except Exception:
            return

    for firm in ALL_FIRMS:
        _collect(OpenSecTradeContext, "SEC", firm)
        _collect(OpenFutureTradeContext, "FUTURE", firm)
    return accounts


def query_portfolio(host, port, acc_id, trd_env, ctx_type="SEC", show_option_strategy_view=False):
    """查询单个账户的资金与持仓"""
    from common import OpenSecTradeContext, OpenFutureTradeContext

    if str(ctx_type).upper() == "FUTURE":
        if OpenFutureTradeContext is None:
            raise RuntimeError("当前 SDK 不支持 OpenFutureTradeContext，请升级 futu-api")
        kwargs = dict(host=host, port=port)
        ctx_cls = OpenFutureTradeContext
    else:
        kwargs = dict(host=host, port=port, filter_trdmarket=TrdMarket.NONE)
        ctx_cls = OpenSecTradeContext
    if _sdk_supports_ai_type:
        kwargs["ai_type"] = 1
    ctx = ctx_cls(**kwargs)
    try:
        # 资金
        ret, acc_data = ctx.accinfo_query(trd_env=trd_env, acc_id=acc_id)
        funds = {}
        if ret == RET_OK and not is_empty(acc_data):
            row = acc_data.iloc[0]
            funds = {
                "total_assets": safe_float(safe_get(row, "total_assets", default=0)),
                "cash": safe_float(safe_get(row, "cash", default=0)),
                "market_val": safe_float(safe_get(row, "market_val", default=0)),
                "us_cash": safe_float(safe_get(row, "us_cash", default=0)),
                "hk_cash": safe_float(safe_get(row, "hk_cash", default=0)),
                "cn_cash": safe_float(safe_get(row, "cn_cash", default=0)),
                "frozen_cash": safe_float(safe_get(row, "frozen_cash", default=0)),
                "power": safe_float(safe_get(row, "power", default=0)),
            }

        # 持仓
        ret, pos_data = ctx.position_list_query(
            trd_env=trd_env,
            acc_id=acc_id,
            show_option_strategy_view=show_option_strategy_view,
        )
        positions = []
        if ret == RET_OK and not is_empty(pos_data):
            for i in range(len(pos_data)):
                row = pos_data.iloc[i]
                positions.append({
                    "code": safe_get(row, "code", default=""),
                    "name": safe_get(row, "stock_name", default=""),
                    "qty": safe_float(safe_get(row, "qty", default=0)),
                    "can_sell_qty": safe_float(safe_get(row, "can_sell_qty", default=0)),
                    "average_cost": safe_float(safe_get(row, "average_cost", default=0)),
                    "nominal_price": safe_float(safe_get(row, "nominal_price", default=0)),
                    "market_val": safe_float(safe_get(row, "market_val", default=0)),
                    "unrealized_pl": safe_float(safe_get(row, "unrealized_pl", default=0)),
                    "pl_ratio_avg_cost": safe_float(safe_get(row, "pl_ratio_avg_cost", default=0)),
                    "combo_id": safe_get(row, "combo_id", default=""),
                    "strategy_type": safe_get(row, "strategy_type", default=""),
                    "position_type": safe_get(row, "position_type", default=""),
                    "acc_id": safe_int(safe_get(row, "acc_id", default=0)),
                    "jp_acc_type": safe_get(row, "jp_acc_type", default=""),
                })

        return funds, positions
    finally:
        safe_close(ctx)


def main():
    parser = argparse.ArgumentParser(description="查询所有账户的资金与持仓（证券 + 期货）")
    parser.add_argument("--acc-id", type=int, default=None, help="指定账户 ID")
    parser.add_argument("--trd-env", choices=["REAL", "SIMULATE"], default=None, help="交易环境过滤")
    parser.add_argument("--show-option-strategy-view", action="store_true",
                        help="按期权策略维度展示持仓（position_list_query 的 show_option_strategy_view）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    from common import get_opend_config, _check_opend_alive
    host, port = get_opend_config()
    _check_opend_alive(host, port)

    # 获取账户列表
    accounts = get_all_accounts(host, port)

    # 过滤
    if args.trd_env:
        accounts = [a for a in accounts if a["trd_env"] == args.trd_env]
    if args.acc_id:
        accounts = [a for a in accounts if a["acc_id"] == args.acc_id]

    if not accounts:
        if args.output_json:
            print(json.dumps({"accounts": []}, ensure_ascii=False))
        else:
            print("未找到匹配的账户")
        return

    results = []
    for acc in accounts:
        acc_id = acc["acc_id"]
        trd_env_str = acc["trd_env"]
        trd_env = TrdEnv.REAL if trd_env_str == "REAL" else TrdEnv.SIMULATE
        funds, positions = query_portfolio(
            host,
            port,
            acc_id,
            trd_env,
            ctx_type=acc.get("ctx_type", "SEC"),
            show_option_strategy_view=args.show_option_strategy_view,
        )
        results.append({
            "acc_id": acc_id,
            "trd_env": trd_env_str,
            "acc_type": acc["acc_type"],
            "trdmarket_auth": acc["trdmarket_auth"],
            "ctx_type": acc.get("ctx_type", "SEC"),
            "funds": funds,
            "positions": positions,
        })

    if args.output_json:
        print(json.dumps({"accounts": results}, ensure_ascii=False))
    else:
        for r in results:
            env_label = "模拟" if r["trd_env"] == "SIMULATE" else "实盘"
            markets = r["trdmarket_auth"] if isinstance(r["trdmarket_auth"], list) else [r["trdmarket_auth"]]
            market_str = ",".join(str(m) for m in markets)
            print(f"\n{'='*60}")
            print(f"账户 {r['acc_id']} | {env_label} | {r['acc_type']} | 上下文: {r['ctx_type']} | 市场: {market_str}")
            print(f"{'='*60}")
            f = r["funds"]
            if f:
                print(f"  总资产: {f['total_assets']:,.2f}  现金: {f['cash']:,.2f}  持仓市值: {f['market_val']:,.2f}")
            if r["positions"]:
                if args.show_option_strategy_view:
                    print(
                        f"  {'代码':<25} {'名称':<12} {'数量':>8} {'现价':>10} {'市值':>12} {'盈亏%':>8} "
                        f"{'策略类型':<12} {'持仓类型':<10}"
                    )
                    print("  " + "-" * 100)
                else:
                    print(f"  {'代码':<25} {'名称':<12} {'数量':>8} {'现价':>10} {'市值':>12} {'盈亏%':>8}")
                    print("  " + "-" * 75)
                for p in r["positions"]:
                    if args.show_option_strategy_view:
                        print(
                            f"  {p['code']:<25} {p['name']:<12} {p['qty']:>8.0f} {p['nominal_price']:>10.3f} "
                            f"{p['market_val']:>12.2f} {p['pl_ratio_avg_cost']:>8.2f}% {str(p['strategy_type']):<12} "
                            f"{str(p['position_type']):<10}"
                        )
                    else:
                        print(
                            f"  {p['code']:<25} {p['name']:<12} {p['qty']:>8.0f} {p['nominal_price']:>10.3f} "
                            f"{p['market_val']:>12.2f} {p['pl_ratio_avg_cost']:>8.2f}%"
                        )
            else:
                print("  无持仓")


if __name__ == "__main__":
    main()
