#!/usr/bin/env python3
"""
获取每日卖空

功能：获取美股或港股每日卖空成交量、比例、价格等历史数据，支持分页续拉
用法：python get_daily_short_volume.py [-h] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.next_key:               分页标识，"-1" 表示无更多数据
- data.aggregated_short:       未平仓股数（仅港股）
- data.aggregated_short_ratio: 占流通股比例，百分号前的值（仅港股）
- data.new_time_str:            最新数据时间（仅港股，YYYY-MM-DD）
- data.items[]:                卖空数据列表（按请求标的返港股或美股数据）
  美股每项含: timestamp_str/total_shares_short/nasdaq_shares_short/nyse_shares_short/short_percent/volume/close_price/last_close_price/daily_trade_avg_ratio
  港股每项含: timestamp/timestamp_str/shares_traded/turnover/short_sell_shares_traded/short_sell_turnover/open_price/close_price/last_close_price/daily_trade_avg_ratio
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
    print_display_df,
    format_big_number,
)

SEP64 = "=" * 64
SEP64D = "-" * 64


def _fmt_pct(val, decimals=2):
    """百分号前的值直接转百分比字符串（不乘 100）。"""
    try:
        return f"{float(val):.{decimals}f}%"
    except Exception:
        return "-"


def get_daily_short_volume(code, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        if next_key is not None:
            kwargs["next_key"] = next_key
        if num is not None:
            kwargs["num"] = num
        ret, us_df, hk_df = ctx.get_daily_short_volume(code, **kwargs)
        check_ret(ret, us_df, ctx, "获取每日卖空")

        if is_empty(us_df) and is_empty(hk_df):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        market = code.upper().split(".")[0] if "." in code else ""
        is_us = market == "US"

        if is_us:
            nk = us_df.attrs.get("next_key", "")
        else:
            nk = hk_df.attrs.get("next_key", "")

        if output_json:
            if is_us:
                records = df_to_records(us_df) if not us_df.empty else []
                print(json.dumps({
                    "code": code,
                    "data": {"next_key": nk, "items": records},
                }, ensure_ascii=False, default=str))
            else:
                attrs = hk_df.attrs if hasattr(hk_df, "attrs") else {}
                records = df_to_records(hk_df) if not hk_df.empty else []
                print(json.dumps({
                    "code": code,
                    "data": {
                        "next_key": nk,
                        "aggregated_short": attrs.get("aggregated_short"),
                        "aggregated_short_ratio": attrs.get("aggregated_short_ratio"),
                        "new_time_str": attrs.get("new_time_str"),
                        "items": records,
                    },
                }, ensure_ascii=False, default=str))
            return

        # non-JSON display
        if is_us:
            print(SEP64)
            print("每日卖空  标的：" + code)
            print(SEP64D)
            if not us_df.empty:
                disp = us_df.copy()
                disp = disp.drop(columns=["timestamp"], errors="ignore")
                for col in ["short_percent", "daily_trade_avg_ratio"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(lambda x: _fmt_pct(x) if x is not None else "-")
                for col in ["total_shares_short", "nasdaq_shares_short", "nyse_shares_short", "volume"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(lambda x: format_big_number(x) if x is not None else "-")
                for col in ["close_price", "last_close_price"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(lambda x: f"{float(x):.3f}" if x is not None else "-")
                disp = disp.rename(columns={
                    "timestamp_str":         "日期",
                    "total_shares_short":    "卖空总股数",
                    "nasdaq_shares_short":   "纳斯达克卖空",
                    "nyse_shares_short":     "纽交所卖空",
                    "short_percent":         "卖空比例%",
                    "volume":                "成交量",
                    "close_price":           "收盘价",
                    "last_close_price":      "上次收盘价",
                    "daily_trade_avg_ratio": "日均成交比例%",
                })
                print_display_df(disp, max_colwidth=18)
            else:
                print("  暂无数据")
        else:
            attrs = hk_df.attrs if hasattr(hk_df, "attrs") else {}
            agg_short = attrs.get("aggregated_short")
            agg_ratio = attrs.get("aggregated_short_ratio")
            new_time_str = attrs.get("new_time_str")
            print(SEP64)
            print("每日卖空  标的：" + code)
            print(SEP64D)
            if any(v is not None for v in [agg_short, agg_ratio, new_time_str]):
                if agg_short is not None:
                    print("  未平仓股数:     " + format_big_number(agg_short))
                if agg_ratio is not None:
                    print("  占流通股比例:   " + _fmt_pct(agg_ratio))
                if new_time_str:
                    print("  最新数据时间:   " + str(new_time_str))
                print()
            if not hk_df.empty:
                disp = hk_df.copy()
                disp = disp.drop(columns=["timestamp"], errors="ignore")
                if "daily_trade_avg_ratio" in disp.columns:
                    disp["daily_trade_avg_ratio"] = disp["daily_trade_avg_ratio"].apply(
                        lambda x: _fmt_pct(x) if x is not None else "-"
                    )
                for col in ["shares_traded", "turnover", "short_sell_shares_traded", "short_sell_turnover"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(lambda x: format_big_number(x) if x is not None else "-")
                for col in ["open_price", "close_price", "last_close_price"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(lambda x: f"{float(x):.3f}" if x is not None else "-")
                disp = disp.rename(columns={
                    "timestamp_str":            "日期",
                    "shares_traded":            "成交量",
                    "turnover":                 "成交额",
                    "short_sell_shares_traded": "卖空成交量",
                    "short_sell_turnover":      "卖空成交额",
                    "open_price":               "开盘价",
                    "close_price":              "收盘价",
                    "last_close_price":         "上次收盘",
                    "daily_trade_avg_ratio":    "日均成交比例%",
                })
                print_display_df(disp, max_colwidth=16)
            else:
                print("  暂无数据")

        row_count = len(us_df) if is_us else len(hk_df)
        nk_display = "已结束(-1)" if (not nk or nk == "-1") else str(nk)
        print(SEP64D)
        print("返回条数：" + str(row_count) + "   --next-key：" + nk_display)
        print(SEP64)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print("错误：" + str(e))
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取每日卖空数据，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None, dest="num",
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()
    get_daily_short_volume(args.code, next_key=args.next_key, num=args.num,
                           output_json=args.output_json)
