#!/usr/bin/env python3
"""
获取破净高股息国央企排行（港股）

功能：获取破净高股息国央企排行数据（仅港股市场）
用法：python get_high_dividend_soe_rank.py [--sort-field MARKET_CAP] [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --sort-field: 排序字段（MARKET_CAP/DIVIDEND_YIELD_TTM/PB/PE_TTM/PRICE/CHANGE_RATIO）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量，默认 10
- --offset: 起始偏移
- --config: JSON 筛选配置文件路径（HighDividendSOERankFilter）

返回字段说明：
- security, name, industry, cur_price, change_ratio, turnover, volume, market_cap
- pe_ttm, pb, dividend_yield_ttm, turnover_ratio
- change_rate_5d, change_rate_10d, change_rate_20d, change_rate_60d, change_rate_120d, change_rate_250d
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu

SORT_FIELD_MAP = {
    "MARKET_CAP": futu.HighDividendSOESortField.MARKET_CAP,
    "DIVIDEND_YIELD_TTM": futu.HighDividendSOESortField.DIVIDEND_YIELD_TTM,
    "PB": futu.HighDividendSOESortField.PB,
    "PE_TTM": futu.HighDividendSOESortField.PE_TTM,
    "PRICE": futu.HighDividendSOESortField.PRICE,
    "CHANGE_RATIO": futu.HighDividendSOESortField.CHANGE_RATIO,
}

SORT_DIR_MAP = {
    0: futu.RankSortDir.DESCENDING,
    1: futu.RankSortDir.ASCENDING,
}


def _build_filters(config_path):
    from futu import HighDividendSOERankFilter, HighDividendSOEIndicatorType
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(HighDividendSOEIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        filters.append(HighDividendSOERankFilter(**kwargs))
    return filters


def get_high_dividend_soe_rank(sort_field=None, sort_dir=0, count=10, offset=None,
                                config_path=None, output_json=False):
    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    sort_dir_enum = SORT_DIR_MAP.get(sort_dir)
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_high_dividend_soe_rank(sort_field=sort_field_enum,
                                                    sort_dir=sort_dir_enum, count=count,
                                                    offset=offset, filter_list=filter_list)
        check_ret(ret, data, ctx, "获取破净高股息国央企排行")

        all_count, df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"all_count": 0, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"all_count": all_count, "data": df_to_records(df)},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"破净高股息国央企排行（共 {all_count} 条）")
            print("=" * 70)
            print(df.to_string(index=False))
            print(f"\n共 {len(df)} 条记录")
            print("=" * 70)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取破净高股息国央企排行（港股）")
    parser.add_argument("--sort-field", choices=list(SORT_FIELD_MAP.keys()),
                        default=None, help="排序字段")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], default=0,
                        help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=10, help="返回数量")
    parser.add_argument("--offset", type=int, default=None, help="起始偏移")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_high_dividend_soe_rank(sort_field=args.sort_field, sort_dir=args.sort_dir,
                                count=args.count, offset=args.offset,
                                config_path=args.config, output_json=args.output_json)
