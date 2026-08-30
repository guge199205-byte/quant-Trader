#!/usr/bin/env python3
"""
获取涨跌分布

功能：获取指定板块或市场的涨跌分布数据
用法：python get_rise_fall_distribution.py [--security HK.BK1001] [--market HK] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --security: 板块代码（优先使用，如 HK.BK1001）
- --market: 市场（HK/US/CN），security 未传时使用

返回字段说明：
- 返回 dict，包含涨跌分布统计
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
    "CN": futu.Market.SH,
    "SG": futu.Market.SG,
    "JP": futu.Market.JP,
    "MY": futu.Market.MY,
    "AU": futu.Market.AU,
    "CA": futu.Market.CA,
}


def get_rise_fall_distribution(security=None, market_str=None, output_json=False):
    if security is None and market_str is None:
        raise ValueError("必须指定 --security 或 --market 其中之一")

    market = MARKET_MAP.get(market_str.upper()) if market_str else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_rise_fall_distribution(security=security, market=market)
        check_ret(ret, data, ctx, "获取涨跌分布")

        print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取涨跌分布")
    parser.add_argument("--security", help="板块代码（如 HK.BK1001）")
    parser.add_argument("--market", choices=list(MARKET_MAP.keys()), help="市场")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_rise_fall_distribution(security=args.security, market_str=args.market,
                              output_json=args.output_json)
