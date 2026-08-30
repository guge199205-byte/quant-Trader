#!/usr/bin/env python3
"""
获取宏观指标列表

功能：获取指定国家/地区的宏观指标列表
用法：python get_macro_indicator_list.py --region US [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --region: 国家/地区（HK/US/JP/SG/AU/CA/MY/CN）

返回字段说明：
- category_name, indicator_id, name
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu

REGION_MAP = {
    "HK": futu.MacroRegion.HK,
    "US": futu.MacroRegion.US,
    "JP": futu.MacroRegion.JP,
    "SG": futu.MacroRegion.SG,
    "AU": futu.MacroRegion.AU,
    "CA": futu.MacroRegion.CA,
    "MY": futu.MacroRegion.MY,
    "CN": futu.MacroRegion.CN,
}


def get_macro_indicator_list(region_str, output_json=False):
    region = REGION_MAP.get(region_str.upper())
    if region is None:
        raise ValueError(f"不支持的地区: {region_str}，可选: {list(REGION_MAP.keys())}")

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_macro_indicator_list(region=region)
        check_ret(ret, data, ctx, "获取宏观指标列表")

        if is_empty(data):
            print("无数据")
            return

        if output_json:
            print(json.dumps({"records": df_to_records(data)}, ensure_ascii=False, indent=2))
        else:
            print(data.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取宏观指标列表")
    parser.add_argument("--region", required=True, choices=list(REGION_MAP.keys()), help="国家/地区")
    parser.add_argument("--json", dest="output_json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    get_macro_indicator_list(args.region, output_json=args.output_json)
