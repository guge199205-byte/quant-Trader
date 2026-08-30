#!/usr/bin/env python3
"""
获取加密货币交易账户列表

功能：查询当前登录用户在 FUTUSECURITIES、FUTUINC、FUTUSG 下的加密货币账户。

返回字段说明：
- acc_id: 加密货币账户 ID
- acc_type: CASH（加密货币仅支持现金账户）
- uni_card_num: 与母账户（证券）共用
- trdmarket_auth: 应包含 CRYPTO
- security_firm: FUTUSECURITIES / FUTUINC / FUTUSG
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_crypto_trade_context,
    check_ret,
    safe_close,
    is_empty,
    safe_get,
    safe_int,
    format_enum,
    CRYPTO_SUPPORTED_FIRMS,
)

from futu import SecurityFirm


def _parse_account_row(row, firm_name):
    trdmarket_auth_raw = safe_get(row, "trdmarket_auth", default=[])
    if isinstance(trdmarket_auth_raw, str):
        trdmarket_auth = [s.strip() for s in trdmarket_auth_raw.strip("[]").split(",") if s.strip()]
    elif isinstance(trdmarket_auth_raw, list):
        trdmarket_auth = [format_enum(m) for m in trdmarket_auth_raw]
    else:
        trdmarket_auth = []
    return {
        "acc_id": safe_int(safe_get(row, "acc_id", default=0)),
        "acc_type": format_enum(safe_get(row, "acc_type", default="")),
        "trd_env": format_enum(safe_get(row, "trd_env", default="")),
        "uni_card_num": safe_get(row, "uni_card_num", "card_num", default=""),
        "card_num": safe_get(row, "card_num", default=""),
        "security_firm": firm_name,
        "trdmarket_auth": trdmarket_auth,
        "acc_status": format_enum(safe_get(row, "acc_status", default="")),
    }


def get_crypto_accounts(output_json=False):
    seen = set()
    accounts = []
    for firm_name in CRYPTO_SUPPORTED_FIRMS:
        firm = getattr(SecurityFirm, firm_name, None)
        if firm is None:
            continue
        ctx = None
        try:
            ctx = create_crypto_trade_context(security_firm=firm)
            ret, data = ctx.get_acc_list()
            if ret != 0 or is_empty(data):
                continue
            for i in range(len(data)):
                row = data.iloc[i] if hasattr(data, "iloc") else data[i]
                acc = _parse_account_row(row, firm_name)
                if acc["acc_id"] in seen:
                    continue
                seen.add(acc["acc_id"])
                accounts.append(acc)
        except SystemExit:
            raise
        except Exception:
            pass
        finally:
            safe_close(ctx)

    if output_json:
        print(json.dumps({"accounts": accounts}, ensure_ascii=False))
        return

    if not accounts:
        print("未查询到加密货币账户，请确认已开通 FUTUHK / moomoo US / moomoo SG 的加密货币交易权限。")
        return

    print("=" * 70)
    print("加密货币账户列表")
    print("=" * 70)
    for a in accounts:
        print(f"\n  账户 ID: {a['acc_id']}  券商: {a['security_firm']}")
        print(f"    类型: {a['acc_type']}  环境: {a['trd_env']}  uni_card_num: {a['uni_card_num']}")
        print(f"    交易市场权限: {', '.join(a['trdmarket_auth']) if a['trdmarket_auth'] else 'N/A'}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取加密货币账户列表")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_crypto_accounts(args.output_json)
