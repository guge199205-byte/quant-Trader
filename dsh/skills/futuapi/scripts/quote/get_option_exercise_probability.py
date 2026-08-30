#!/usr/bin/env python3
"""
获取期权行权概率

功能：获取指定期权合约的历史行权概率数据，按时间从大到小排序
用法：python get_option_exercise_probability.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 仅支持期权合约代码

参数说明：
- code: 期权代码，如 US.AAPL260427C270000

返回字段说明：
- data[]:    行权概率列表，按日期从大到小；每项含 timestamp/timestamp_str/security_price/strike_probability（百分号前的值，如 12.34 表示 12.34%）
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
)

SEP64 = "=" * 64
DASH64 = "-" * 64


def get_option_exercise_probability(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, df = ctx.get_option_exercise_probability(code)
        check_ret(ret, df, ctx, "获取期权行权概率")

        if is_empty(df):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        row_count = len(df) if df is not None and not df.empty else 0

        if output_json:
            records = df_to_records(df) if df is not None and not df.empty else []
            out = {
                "code": code,
                "data": records,
            }
            print(json.dumps(out, ensure_ascii=False, default=str))
            return

        # 非 JSON 路径
        if df is None or df.empty:
            print("无数据")
        else:
            print(SEP64)
            print(f"期权行权概率  标的：{code}")
            print(DASH64)
            disp = df.copy()
            if "timestamp_str" in disp.columns:
                disp = disp.drop(columns=["timestamp"], errors="ignore")
            if "security_price" in disp.columns:
                disp["security_price"] = disp["security_price"].apply(
                    lambda x: f"{float(x):.3f}" if x is not None and str(x) != "" else "-"
                )
            if "strike_probability" in disp.columns:
                disp["strike_probability"] = disp["strike_probability"].apply(
                    lambda x: f"{float(x):.2f}%" if x is not None and str(x) != "" else "-"
                )
            disp = disp.rename(columns={
                "timestamp_str":      "日期",
                "security_price":     "正股价格",
                "strike_probability": "行权概率",
            })
            print_display_df(disp, max_colwidth=24)
            print(DASH64)
            print(f"返回条数：{row_count}")
            print(SEP64)

    except SystemExit:
        raise
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误：{e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权行权概率")
    parser.add_argument("code", help="期权代码，如 US.AAPL260427C270000")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_exercise_probability(args.code, output_json=args.output_json)
