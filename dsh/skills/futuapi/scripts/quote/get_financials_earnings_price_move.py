#!/usr/bin/env python3
"""
获取个股财报日前后价格涨跌幅表现

功能：获取指定股票在多个财报周期内、财报公布日前后各交易日的价格表现数据
用法：python get_financials_earnings_price_move.py [-h] [--period-count N] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 市场限制：支持港股、美股正股

参数说明：
- code: 股票代码，如 HK.00700
- --period-count: 财报周期数量，默认 10，范围 1-50

返回字段说明：
- data.period_count: 财报周期总数
- data.items[]:      按交易日展开的平铺列表；每行同时含财报元信息（fiscal_year/financial_type/period_text/pub_trading_day_str/pub_type/price_info_index）和当日行情（day_offset/trading_day_str/close_price/open_price/highest_price/lowest_price/last_close_price/option_iv/option_hv）
"""
import argparse
import json
import math
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
import pandas as pd
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
    print_display_df,
)

_SEP64 = "=" * 64
_SEP64D = "-" * 64

_PUB_TYPE_MAP = {0: "无", 1: "盘前", 2: "盘后", 3: "盘中"}


def _fmt_float(v, decimals=3):
    if v is None:
        return "-"
    if isinstance(v, float) and math.isnan(v):
        return "-"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _day_offset_str(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return "-"
    if v < 0:
        return f"前{-v}天"
    elif v == 0:
        return "当天"
    else:
        return f"后{v}天"


def _pub_type_str(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return "-"
    if v == 0:
        return "-"
    label = _PUB_TYPE_MAP.get(v)
    return label if label is not None else str(v)


def _build_display_df(data: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in data.iterrows():
        rows.append({
            "财报期":     str(row.get("period_text", "") or ""),
            "发布日":     str(row.get("pub_trading_day_str", "") or ""),
            "发布时间类型": _pub_type_str(row.get("pub_type")),
            "距财报日":   _day_offset_str(row.get("day_offset")),
            "交易日":     str(row.get("trading_day_str", "") or ""),
            "收盘价":     _fmt_float(row.get("close_price")),
            "开盘价":     _fmt_float(row.get("open_price")),
            "最高价":     _fmt_float(row.get("highest_price")),
            "最低价":     _fmt_float(row.get("lowest_price")),
            "昨收":       _fmt_float(row.get("last_close_price")),
            "隐含波动率%": _fmt_float(row.get("option_iv"), decimals=2),
            "历史波动率%": _fmt_float(row.get("option_hv"), decimals=2),
        })
    return pd.DataFrame(rows)


def get_financials_earnings_price_move(code, period_count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        if period_count is not None:
            kwargs["period_count"] = period_count
        ret, data = ctx.get_financials_earnings_price_move(code, **kwargs)
        check_ret(ret, data, ctx, "获取财报日前后价格涨跌幅表现")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({
                "code": code,
                "data": {
                    "period_count": period_count,
                    "items": df_to_records(data, limit=500),
                },
            }, ensure_ascii=False, default=str))
        else:
            print(_SEP64)
            print(f"财报日前后价格涨跌幅表现  标的：{code}")
            print(_SEP64D)
            print_display_df(_build_display_df(data), max_colwidth=24)
            print(_SEP64D)
            n_periods = data["period_text"].nunique() if "period_text" in data.columns else len(data)
            print(f"共 {n_periods} 期")
            print(_SEP64)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取财报发布前后若干交易日的股价行情数据")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--period-count", type=int, default=None, metavar="N",
                        help="财报周期数量，默认 10，范围 1-50")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_financials_earnings_price_move(args.code, period_count=args.period_count, output_json=args.output_json)
