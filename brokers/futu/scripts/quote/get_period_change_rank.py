#!/usr/bin/env python3
"""
获取区间涨跌幅排行

功能：获取指定市场、指定时间区间的涨跌幅排行数据
用法：python get_period_change_rank.py --market US [--period FIVE_MIN] [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]

接口限制：
- 每 30 秒内最多请求 60 次

参数说明：
- --market: 市场代码（HK/US）
- --period: 区间类型（FIVE_MIN/ONE_DAY/FIVE_DAY/TWENTY_DAY/SIXTY_DAY/ONE_TWENTY_DAY/TWO_FIFTY_DAY/YTD）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量，范围 [1,200]，默认 10
- --offset: 起始偏移
- --config: JSON 筛选配置文件路径（PeriodChangeRankFilter）

返回字段说明：
- security: 股票代码
- name: 名称
- cur_price: 当前价格
- change_ratio: 涨跌幅
- turnover: 成交额
- volume: 成交量
- market_cap: 市值
- change_rate_5min/5d/10d/20d/60d/120d/250d/ytd: 各区间涨跌幅
- pe_ttm: 市盈率(TTM)
- pb: 市净率
- turnover_ratio: 换手率
- volume_ratio: 量比
- amplitude: 振幅
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

PERIOD_MAP = {
    "FIVE_MIN": futu.RankPeriodType.FIVE_MIN,
    "ONE_DAY": futu.RankPeriodType.ONE_DAY,
    "FIVE_DAY": futu.RankPeriodType.FIVE_DAY,
    "TWENTY_DAY": futu.RankPeriodType.TWENTY_DAY,
    "SIXTY_DAY": futu.RankPeriodType.SIXTY_DAY,
    "ONE_TWENTY_DAY": futu.RankPeriodType.ONE_TWENTY_DAY,
    "TWO_FIFTY_DAY": futu.RankPeriodType.TWO_FIFTY_DAY,
    "YTD": futu.RankPeriodType.YTD,
}

SORT_DIR_MAP = {
    0: futu.RankSortDir.DESCENDING,
    1: futu.RankSortDir.ASCENDING,
}


def _build_filters(config_path):
    from futu import PeriodChangeRankFilter, PeriodChangeIndicatorType
    with open(config_path, "r", encoding="utf-8") as f:
        specs = json.load(f)
    if isinstance(specs, dict):
        specs = [specs]
    filters = []
    for s in specs:
        indicator_str = s.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        ind = getattr(PeriodChangeIndicatorType, str(indicator_str).upper())
        kwargs = {"indicator_type": ind}
        if "interval_min" in s:
            kwargs["interval_min"] = s["interval_min"]
        if "interval_max" in s:
            kwargs["interval_max"] = s["interval_max"]
        filters.append(PeriodChangeRankFilter(**kwargs))
    return filters


def get_period_change_rank(market_str, period=None, sort_dir=0, count=10, offset=None,
                           config_path=None, output_json=False):
    market = MARKET_MAP.get(market_str.upper())
    if market is None:
        raise ValueError(f"不支持的市场: {market_str}，可选: {list(MARKET_MAP.keys())}")

    period_enum = PERIOD_MAP.get(period.upper()) if period else None
    sort_dir_enum = SORT_DIR_MAP.get(sort_dir)
    filter_list = _build_filters(config_path) if config_path else None

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_period_change_rank(market, period_type=period_enum,
                                               sort_dir=sort_dir_enum, count=count,
                                               offset=offset, filter_list=filter_list)
        check_ret(ret, data, ctx, "获取区间涨跌幅排行")

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
            print(f"区间涨跌幅排行 - {market_str}（共 {all_count} 条）")
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
    parser = argparse.ArgumentParser(description="获取区间涨跌幅排行")
    parser.add_argument("--market", required=True, choices=["HK", "US"], help="市场")
    parser.add_argument("--period", choices=["FIVE_MIN", "ONE_DAY", "FIVE_DAY", "TWENTY_DAY",
                                             "SIXTY_DAY", "ONE_TWENTY_DAY", "TWO_FIFTY_DAY", "YTD"],
                        default=None, help="区间类型")
    parser.add_argument("--sort-dir", type=int, choices=[0, 1], default=0,
                        help="排序方向（0=降序，1=升序）")
    parser.add_argument("--count", type=int, default=10, help="返回数量，范围 [1,200]")
    parser.add_argument("--offset", type=int, default=None, help="起始偏移")
    parser.add_argument("--config", default=None, help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_period_change_rank(args.market, period=args.period, sort_dir=args.sort_dir,
                           count=args.count, offset=args.offset, config_path=args.config,
                           output_json=args.output_json)
