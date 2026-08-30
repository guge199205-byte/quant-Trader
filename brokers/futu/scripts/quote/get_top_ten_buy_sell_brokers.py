#!/usr/bin/env python3
"""
获取十大买卖经纪商

功能：获取指定港股的十大净买入和净卖出经纪商列表（实时或历史）
用法：python get_top_ten_buy_sell_brokers.py [-h] [--days-before DAYS_BEFORE] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股正股及基金
- days_before=0 返回实时数据（含均价/总量/总额），days_before>0 仅含净量和经纪商名称

参数说明：
- code: 股票代码，如 HK.00700
- --days-before: 距当前交易日天数，0=实时，>0=历史第 N 个交易日（默认不填=实时）

返回字段说明：
- data[]: 经纪商列表，按净买/卖量从高到低；每项含 is_real_time/data_time/data_time_str/broker_name/buy_sell_type/net_vol/avg_price/total_vol/total_turnover
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
DIV64 = "-" * 64

_COLS_REALTIME = ["broker_name", "net_vol", "avg_price", "total_vol", "total_turnover"]
_COLS_HIST     = ["broker_name", "net_vol"]

_COLS_CN = {
    "broker_name":     "经纪商",
    "net_vol":         "净量",
    "avg_price":       "均价",
    "total_vol":       "成交量",
    "total_turnover":  "成交额",
}


def get_top_ten_buy_sell_brokers(code, days_before=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        kwargs = {}
        if days_before is not None:
            kwargs["days_before"] = days_before
        ret, data = ctx.get_top_ten_buy_sell_brokers(code, **kwargs)
        check_ret(ret, data, ctx, "获取十大买卖经纪商")

        total_rows = len(data) if data is not None and not is_empty(data) else 0

        if output_json:
            records = df_to_records(data) if not is_empty(data) else []
            print(json.dumps({"code": code, "data": records}, ensure_ascii=False))
            return

        if is_empty(data):
            print("无数据")
        else:
            update_time = ""
            if "data_time_str" in data.columns:
                _ts = data["data_time_str"].dropna()
                _ts = _ts[_ts.astype(str).str.len() > 0]
                if not _ts.empty:
                    update_time = str(_ts.iloc[0])

            print(SEP64)
            header = "十大买卖经纪商  标的：" + code
            if days_before is not None:
                header += "  days-before=" + str(days_before)
            print(header)
            if update_time:
                print("数据更新时间：" + update_time)
            print(DIV64)

            is_realtime = data.iloc[0].get("is_real_time", True)
            cols = _COLS_REALTIME if is_realtime else _COLS_HIST
            buy_data  = data[data["buy_sell_type"] == 1].copy() if "buy_sell_type" in data.columns else data
            sell_data = data[data["buy_sell_type"] == 2].copy() if "buy_sell_type" in data.columns else data

            def _print_section(label, df):
                avail = [c for c in cols if c in df.columns]
                print("\n十大" + label + "经纪商  共 " + str(len(df)) + " 条")
                print(DIV64)
                if is_empty(df):
                    print("无数据")
                else:
                    sub = df[avail].copy()
                    for col in ("net_vol", "total_vol", "total_turnover"):
                        if col in sub.columns:
                            sub[col] = sub[col].apply(lambda x: format_big_number(x) if x else "-")
                    if "avg_price" in sub.columns:
                        sub["avg_price"] = sub["avg_price"].apply(lambda x: f"{x:.3f}" if x else "-")
                    sub = sub.rename(columns={k: _COLS_CN[k] for k in avail if k in _COLS_CN})
                    print_display_df(sub, max_colwidth=28)

            _print_section("净买入", buy_data)
            _print_section("净卖出", sell_data)
            print(DIV64)
            print("返回条数：" + str(total_rows))
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
    parser = argparse.ArgumentParser(description="获取十大买卖经纪商")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--days-before", type=int, default=None, dest="days_before",
                        help="距当前交易日天数，0=实时，>0=历史第 N 个交易日（默认不填=实时）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_top_ten_buy_sell_brokers(args.code, days_before=args.days_before, output_json=args.output_json)
