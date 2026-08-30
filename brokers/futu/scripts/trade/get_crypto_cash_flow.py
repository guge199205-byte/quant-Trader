#!/usr/bin/env python3
"""
查询加密货币账户资金流水

与证券账户差异：
- 加密货币：按 [start, end] 联日查询（根据 create_time）
- 证券/期货：按 clearing_date 单日查询
- 返回字段新增 create_time；加密货币账户的 settlement_date 返回 N/A
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


def get_crypto_cash_flow(start, end, acc_id=None, security_firm=None, output_json=False):
    if not start or not end:
        msg = "加密货币账户资金流水需传入 --start 和 --end（YYYY-MM-DD）"
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    acc_id = acc_id or get_default_acc_id()
    firm_enum = parse_security_firm(security_firm)

    ctx = None
    try:
        ctx = create_crypto_trade_context(security_firm=firm_enum)
        ret, data = ctx.get_acc_cash_flow(trd_env=TrdEnv.REAL, acc_id=acc_id,
                                          start=start, end=end)
        check_ret(ret, data, ctx, "查询加密货币资金流水")
        records = df_to_records(data) if not is_empty(data) else []
        if output_json:
            print(json.dumps({"cash_flow": records}, ensure_ascii=False))
            return
        print(f"共 {len(records)} 条流水")
        for r in records:
            print(f"  create={r.get('create_time')} amt={r.get('cash_flow_amount')} type={r.get('cash_flow_direction')} remark={r.get('cash_flow_remark')}")
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="加密货币账户资金流水")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--acc-id", type=int, default=None, help="加密货币账户 ID")
    parser.add_argument("--security-firm",
                        choices=["FUTUSECURITIES", "FUTUINC", "FUTUSG"],
                        default=None, help="券商标识")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_cash_flow(start=args.start, end=args.end, acc_id=args.acc_id,
                         security_firm=args.security_firm, output_json=args.output_json)
