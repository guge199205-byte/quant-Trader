#!/usr/bin/env python3
"""
获取机构概况

功能：根据机构 ID 获取机构详细概况
用法：python get_institution_profile.py --market US --institution-id 123 [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场（HK/US）
- --institution-id: 机构 ID（必填）

返回字段说明：
- 返回 dict，包含机构概况信息
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close

import futu

MARKET_MAP = {
    "HK": futu.Market.HK,
    "US": futu.Market.US,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
    "MY": futu.Market.MY,
}


def get_institution_profile(market_str, institution_id, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_institution_profile(market=market, institution_id=institution_id)
        check_ret(ret, data, ctx, "获取机构概况")

        print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取机构概况")
    parser.add_argument("--market", required=True, choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--institution-id", type=int, required=True, help="机构 ID")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_institution_profile(args.market, args.institution_id, output_json=args.output_json)
