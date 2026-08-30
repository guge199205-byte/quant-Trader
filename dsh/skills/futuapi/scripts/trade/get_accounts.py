#!/usr/bin/env python3
"""
获取交易账户列表

功能：查询当前登录用户的所有交易账户（证券 + 期货合并）
用法：python get_accounts.py

接口限制：
- 无特殊限频

返回字段说明：
- ctx_type: 账户来源上下文 SEC（证券 OpenSecTradeContext）/ FUTURE（期货 OpenFutureTradeContext）
- card_num: 综合账户下包含一个或多个业务账户（综合证券、综合期货等），与交易品种有关
- trdmarket_auth: 账户可交易的市场列表（按账户实际权限返回，比赛账户按比赛规则返回）
- acc_role: MASTER=主账户，NORMAL=普通账户
- sim_acc_type: 模拟账户类型（SimAccType 枚举），新增 COMPETITION 标识比赛账户
    NONE / STOCK / OPTION / STOCK_AND_OPTION / FUTURES / COMPETITION
- competition_acc_name: 比赛账户名称（仅 sim_acc_type=COMPETITION 的账户返回，其他模拟账户与真实账户返回 N/A）

比赛账户特性：
- 美股比赛账户：TrdMarket.US 且 acc_type=TrdAccType.MARGIN（支持融资融券）
- 港股比赛账户：TrdMarket.HK 且 acc_type=TrdAccType.CASH（不支持融资融券）
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_trade_context,
    create_future_trade_context,
    safe_close,
    is_empty,
    safe_get,
    safe_int,
    format_enum,
)

from futu import SecurityFirm


# All SecurityFirm enum values to try
_ALL_SECURITY_FIRMS = [
    SecurityFirm.NONE,
    SecurityFirm.FUTUSECURITIES,
    SecurityFirm.FUTUINC,
    SecurityFirm.FUTUSG,
    SecurityFirm.FUTUAU,
    SecurityFirm.FUTUCA,
    SecurityFirm.FUTUJP,
    SecurityFirm.FUTUMY,
]


def _parse_account_row(row, ctx_type):
    """Parse a single account row into a dict."""
    trdmarket_auth_raw = safe_get(row, "trdmarket_auth", default=[])
    if isinstance(trdmarket_auth_raw, str):
        trdmarket_auth = [s.strip() for s in trdmarket_auth_raw.strip("[]").split(",") if s.strip()]
    elif isinstance(trdmarket_auth_raw, list):
        trdmarket_auth = [format_enum(m) for m in trdmarket_auth_raw]
    else:
        trdmarket_auth = []
    sim_acc_type = format_enum(safe_get(row, "sim_acc_type", default="NONE"))
    competition_acc_name_raw = safe_get(row, "competition_acc_name", default="")
    competition_acc_name = competition_acc_name_raw if (sim_acc_type == "COMPETITION" and competition_acc_name_raw) else "N/A"
    return {
        "acc_id": safe_int(safe_get(row, "acc_id", default=0)),
        "acc_type": format_enum(safe_get(row, "acc_type", default="")),
        "acc_role": format_enum(safe_get(row, "acc_role", default="")),
        "trd_env": format_enum(safe_get(row, "trd_env", default="")),
        "card_num": safe_get(row, "card_num", default=""),
        "security_firm": format_enum(safe_get(row, "security_firm", default="")),
        "trdmarket_auth": trdmarket_auth,
        "acc_status": format_enum(safe_get(row, "acc_status", default="")),
        "sim_acc_type": sim_acc_type,
        "competition_acc_name": competition_acc_name,
        "ctx_type": ctx_type,
    }


def _collect_from_context(create_ctx, firm, ctx_type, seen_acc_ids, accounts, show_disabled):
    ctx = None
    try:
        ctx = create_ctx(security_firm=firm) if ctx_type == "FUTURE" else create_ctx(market="NONE", security_firm=firm)
        ret, data = ctx.get_acc_list()
        if ret != 0 or is_empty(data):
            return
        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, "iloc") else data[i]
            acc = _parse_account_row(row, ctx_type)
            if acc["acc_id"] in seen_acc_ids:
                continue
            if not show_disabled and acc["acc_status"] == "DISABLED":
                continue
            seen_acc_ids.add(acc["acc_id"])
            accounts.append(acc)
    except Exception:
        pass
    finally:
        safe_close(ctx)


def get_accounts(output_json=False, show_disabled=False):
    seen_acc_ids = set()
    accounts = []

    for firm in _ALL_SECURITY_FIRMS:
        _collect_from_context(
            create_trade_context, firm, "SEC", seen_acc_ids, accounts, show_disabled
        )
        _collect_from_context(
            create_future_trade_context, firm, "FUTURE", seen_acc_ids, accounts, show_disabled
        )

    if not accounts:
        if output_json:
            print(json.dumps({"accounts": []}))
        else:
            print("无账户数据")
        return

    if output_json:
        print(json.dumps({"accounts": accounts}, ensure_ascii=False))
    else:
        print("=" * 70)
        print("交易账户列表（证券 + 期货）")
        print("=" * 70)
        for a in accounts:
            print(f"\n  账户 ID: {a['acc_id']}")
            print(f"    上下文: {a['ctx_type']}  类型: {a['acc_type']}  角色: {a['acc_role']}  环境: {a['trd_env']}  券商: {a['security_firm']}")
            print(f"    交易市场权限: {', '.join(a['trdmarket_auth']) if a['trdmarket_auth'] else 'N/A'}")
            if a.get("sim_acc_type") and a["sim_acc_type"] != "NONE":
                print(f"    模拟账户类型: {a['sim_acc_type']}")
            if a.get("sim_acc_type") == "COMPETITION":
                print(f"    比赛账户名称: {a['competition_acc_name']}")
        print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取交易账户列表（证券 + 期货）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    parser.add_argument("--show-disabled", action="store_true", help="显示 DISABLED 状态的账户")
    args = parser.parse_args()
    get_accounts(args.output_json, show_disabled=args.show_disabled)
