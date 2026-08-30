#!/usr/bin/env python3
"""
获取美股盘前榜（get_us_pre_market_rank）

功能：获取美股盘前榜，按指定排序方向返回盘前交易排行数据，支持多维度筛选。
用法：
  python get_us_pre_market_rank.py [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]

JSON 配置示例（filters.json）：
[
  {"indicator_type": "CHANGE_RATE", "interval_min": -10.0, "interval_max": 50.0},
  {"indicator_type": "PRICE", "interval_min": 1.0, "price_filter": "REGULAR"}
]

接口限制：30秒内最多60次
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def _build_filters(config_path):
    import futu
    from futu import SimpleRankFilter, SimpleRankIndicatorType, PriceFilter
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(SimpleRankIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s: kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s: kwargs["interval_max"] = s["interval_max"]
        if "price_filter" in s: kwargs["price_filter"] = getattr(PriceFilter, s["price_filter"].upper())
        filters.append(SimpleRankFilter(**kwargs))
    return filters


def get_us_pre_market_rank(sort_dir=None, count=10, offset=None, config_path=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        filter_list = _build_filters(config_path) if config_path else None

        ret, data = ctx.get_us_pre_market_rank(
            sort_dir=sort_dir,
            count=count,
            offset=offset,
            filter_list=filter_list,
        )
        check_ret(ret, data, ctx, "获取美股盘前榜")
        all_count, df = data

        if is_empty(df):
            if output_json:
                print(json.dumps({"all_count": 0, "data": []}))
            else:
                print("无数据")
            return

        cols = ["security", "name", "pre_market_price", "pre_market_change_ratio",
                "pre_market_change_amount", "pre_market_turnover", "pre_market_volume",
                "close_price", "change_ratio", "change_amount"]
        display_cols = [c for c in cols if c in df.columns]

        if output_json:
            print(json.dumps({"all_count": all_count, "data": df_to_records(df[display_cols])},
                             ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"美股盘前榜 共 {all_count} 条")
            print("=" * 70)
            print(df[display_cols].to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取美股盘前榜 (get_us_pre_market_rank)")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], help="0=降序(默认), 1=升序")
    parser.add_argument("--count", type=int, default=10, help="返回数量(默认10)")
    parser.add_argument("--offset", type=int, help="偏移量")
    parser.add_argument("--config", help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_us_pre_market_rank(args.sort_dir, args.count, args.offset, args.config, args.output_json)
