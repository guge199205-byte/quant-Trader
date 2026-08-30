#!/usr/bin/env python3
"""
获取热议榜（热度排行）

功能：获取指定市场的热议榜数据，包含交易热度、搜索热度、新闻热度等
用法：python get_hot_list.py --market US [--sort-field AVERAGE_HEAT] [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US）
- --sort-field: 排序字段（TRADE_HEAT/SEARCH_HEAT/NEWS_HEAT/AVERAGE_HEAT）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量，范围 [1,200]，默认 10
- --offset: 起始偏移
- --config: JSON 筛选配置文件（HotListFilter，indicator_type 支持 MARKET_CAP 等）

返回字段说明：
- security: 股票代码
- name: 名称
- trade_heat/search_heat/news_heat/average_heat: 各热度值
- trade_heat_change/search_heat_change/news_heat_change/average_heat_change: 热度变化
- news_type/news_title/news_url: 相关新闻信息
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records

import futu

MARKET_MAP = {
    "HK": futu.Market.HK,
    "US": futu.Market.US,
}

SORT_FIELD_MAP = {
    "TRADE_HEAT": futu.HotListSortField.TRADE_HEAT,
    "SEARCH_HEAT": futu.HotListSortField.SEARCH_HEAT,
    "NEWS_HEAT": futu.HotListSortField.NEWS_HEAT,
    "AVERAGE_HEAT": futu.HotListSortField.AVERAGE_HEAT,
}

SORT_DIR_MAP = {
    0: futu.RankSortDir.DESCENDING,
    1: futu.RankSortDir.ASCENDING,
}


def _build_filters(config_path):
    from futu import HotListFilter, HotListIndicatorType
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(HotListIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        filters.append(HotListFilter(**kwargs))
    return filters


def get_hot_list(market_str, sort_field=None, sort_dir=0, count=10, offset=None,
                 config_path=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    sort_field_enum = SORT_FIELD_MAP.get(sort_field.upper()) if sort_field else None
    sort_dir_enum = SORT_DIR_MAP.get(sort_dir)
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_hot_list(market, sort_field=sort_field_enum,
                                     sort_dir=sort_dir_enum, count=count,
                                     offset=offset, filter_list=filter_list)
        check_ret(ret, data, ctx, "获取热议榜")

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
            print(f"热议榜 - {market_str}（共 {all_count} 条）")
            print("=" * 70)
            print(df.to_string(index=False))
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
    parser = argparse.ArgumentParser(description="获取热议榜（热度排行）")
    parser.add_argument("--market", required=True, choices=["HK", "US"], help="市场")
    parser.add_argument("--sort-field", choices=["TRADE_HEAT", "SEARCH_HEAT", "NEWS_HEAT", "AVERAGE_HEAT"],
                        default=None, help="排序字段")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], default=0,
                        help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=10, help="返回数量，范围 [1,200]")
    parser.add_argument("--offset", type=int, default=None, help="起始偏移")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_hot_list(args.market, sort_field=args.sort_field, sort_dir=args.sort_dir,
                 count=args.count, offset=args.offset, config_path=args.config,
                 output_json=args.output_json)
