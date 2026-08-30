#!/usr/bin/env python3
"""
获取空头持仓

功能：获取美股或港股指定标的的空头持仓历史记录，包括卖空股数、卖空比例、回补天数、收盘价等数据（支持分页）
用法：python get_short_interest.py [-h] [--next-key NEXT_KEY] [--num MAX_COUNT] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金
- 单次最多返回 50 条，默认 10 条

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.next_key:  分页标识，"-1" 表示无更多数据
- data.items[]:   空头持仓列表（按请求标的返港股或美股数据）
  美股每项含: timestamp_str/shares_short/short_percent/avg_daily_share_volume/days_to_cover/close_price/last_close_price
  港股每项含: timestamp/timestamp_str/close_price/last_close_price/aggregated_short/aggregated_short_ratio
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

SEP = "=" * 64
SEP2 = "-" * 64


def _fmt_pct(val, decimals=2):
    """百分号前的值直接转百分比字符串（不乘 100）"""
    try:
        return f"{float(val):.{decimals}f}%"
    except Exception:
        return "-"


def _fmt_price(val, decimals=3):
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return "-"


def get_short_interest(code, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, us_df, hk_df = ctx.get_short_interest(code, next_key=next_key, num=num)
        check_ret(ret, us_df, ctx, "获取空头持仓")

        if is_empty(us_df) and is_empty(hk_df):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        is_us = code.upper().startswith("US.")
        nk = us_df.attrs.get("next_key", "") if is_us else hk_df.attrs.get("next_key", "")

        if output_json:
            if is_us:
                records = df_to_records(us_df) if not us_df.empty else []
                print(json.dumps({"code": code, "data": {"next_key": nk, "items": records}},
                                 ensure_ascii=False, default=str))
            else:
                records = df_to_records(hk_df) if not hk_df.empty else []
                print(json.dumps({"code": code, "data": {"next_key": nk, "items": records}},
                                 ensure_ascii=False, default=str))
            return

        if is_us:
            row_count = len(us_df)
            print(SEP)
            print(f"空头持仓  标的：{code}")
            print(f"市场：美股  返回条数：{row_count}")
            print(SEP)
            if not us_df.empty:
                disp = us_df.copy()
                # 去冗余：有 timestamp_str 则不显示 timestamp
                if "timestamp_str" in disp.columns:
                    disp = disp.drop(columns=["timestamp"], errors="ignore")
                    disp = disp.rename(columns={"timestamp_str": "日期"})
                # 处理卖空比例：百分号前的值，如 5.12 表示 5.12%
                if "short_percent" in disp.columns:
                    disp["short_percent"] = disp["short_percent"].apply(
                        lambda x: _fmt_pct(x) if x is not None else "-")
                # 大数字：卖空股数、平均日成交量
                if "shares_short" in disp.columns:
                    max_val = disp["shares_short"].dropna().apply(lambda x: abs(float(x)) if x else 0).max()
                    if max_val >= 10000:
                        disp["shares_short"] = disp["shares_short"].apply(
                            lambda x: format_big_number(x) if x else "-")
                    disp = disp.rename(columns={"shares_short": "卖空股数"})
                if "avg_daily_share_volume" in disp.columns:
                    max_val2 = disp["avg_daily_share_volume"].dropna().apply(lambda x: abs(float(x)) if x else 0).max()
                    if max_val2 >= 10000:
                        disp["avg_daily_share_volume"] = disp["avg_daily_share_volume"].apply(
                            lambda x: format_big_number(x) if x else "-")
                    disp = disp.rename(columns={"avg_daily_share_volume": "平均日成交量"})
                if "days_to_cover" in disp.columns:
                    disp["days_to_cover"] = disp["days_to_cover"].apply(
                        lambda x: _fmt_price(x, decimals=2) if x is not None else "-")
                for col in ("close_price", "last_close_price"):
                    if col in disp.columns:
                        disp[col] = disp[col].apply(
                            lambda x: _fmt_price(x) if x is not None else "-")
                disp = disp.rename(columns={
                    "short_percent":    "卖空比例",
                    "days_to_cover":    "回补天数",
                    "close_price":      "收盘价",
                    "last_close_price": "上次收盘价",
                })
                print_display_df(disp, max_colwidth=20)
        else:
            row_count = len(hk_df)
            print(SEP)
            print(f"空头持仓  标的：{code}")
            print(f"市场：港股  返回条数：{row_count}")
            print(SEP)
            if not hk_df.empty:
                disp = hk_df.copy()
                # 去冗余：有 timestamp_str 则不显示 timestamp
                if "timestamp_str" in disp.columns:
                    disp = disp.drop(columns=["timestamp"], errors="ignore")
                    disp = disp.rename(columns={"timestamp_str": "日期"})
                # 处理占流通股比例：百分号前的值，如 2.34 表示 2.34%
                if "aggregated_short_ratio" in disp.columns:
                    disp["aggregated_short_ratio"] = disp["aggregated_short_ratio"].apply(
                        lambda x: _fmt_pct(x) if x is not None else "-")
                # 大数字：未平仓股数
                if "aggregated_short" in disp.columns:
                    max_val3 = disp["aggregated_short"].dropna().apply(lambda x: abs(float(x)) if x else 0).max()
                    if max_val3 >= 10000:
                        disp["aggregated_short"] = disp["aggregated_short"].apply(
                            lambda x: format_big_number(x) if x else "-")
                    disp = disp.rename(columns={"aggregated_short": "未平仓股数"})
                for col in ("close_price", "last_close_price"):
                    if col in disp.columns:
                        disp[col] = disp[col].apply(
                            lambda x: _fmt_price(x) if x is not None else "-")
                disp = disp.rename(columns={
                    "aggregated_short_ratio": "占流通股比例",
                    "close_price":            "收盘价",
                    "last_close_price":       "上次收盘价",
                })
                print_display_df(disp, max_colwidth=20)

        print(SEP2)
        nk_disp = nk if (nk and nk != "-1") else "已结束(-1)"
        print(f"返回条数：{row_count}  --next-key：{nk_disp}")
        print(SEP)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取空头持仓数据，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None, dest="num",
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_short_interest(args.code, next_key=args.next_key, num=args.num,
                       output_json=args.output_json)
