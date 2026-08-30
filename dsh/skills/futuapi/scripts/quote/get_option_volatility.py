#!/usr/bin/env python3
"""
获取期权波动率分析

功能：获取期权波动率
用法：python get_option_volatility.py [-h] [--query-time-period QUERY_TIME_PERIOD] [--hv-time-period HV_TIME_PERIOD] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 仅支持期权合约代码

参数说明：
- code: 期权代码，如 US.AAPL260427C270000
- --query-time-period: 查询时间周期：1=周, 2=月, 3=季度, 4=半年, 5=年（默认 2=月）
- --hv-time-period: 标的物历史波动率周期（5~250 日，默认 30）

返回字段说明：
- data.average_impvol: 隐含波动率综合值（均值，百分比，如 25.0 表示 25%）
- data.impvol_status:  波动率状态（Qot_Common.OptionImpvolStatusType）
- data.analysis:       分析文案
- data.items[]:        波动率列表，按日期从大到小；每项含 timestamp/timestamp_str/implied_volatility/history_volatility/volatility_premium
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

_IMPVOL_STATUS_MAP = {0: "震荡中", 1: "高估", 2: "低估"}

_SEP64 = "=" * 64
_SEP64D = "-" * 64


def get_option_volatility(code, query_time_period=None, hv_time_period=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, df = ctx.get_option_volatility(
            code,
            query_time_period=query_time_period,
            hv_time_period=hv_time_period,
        )
        check_ret(ret, df, ctx, "获取期权波动率分析")

        if is_empty(df):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            avg_impvol_j = df["average_impvol"].iloc[0] if not df.empty and "average_impvol" in df.columns else None
            impvol_status_j = df["impvol_status"].iloc[0] if not df.empty and "impvol_status" in df.columns else None
            analysis_j = df["analysis"].iloc[0] if not df.empty and "analysis" in df.columns else ""
            records = []
            if not df.empty:
                for _, row in df.iterrows():
                    rec = {
                        "timestamp": row.get("timestamp"),
                        "timestamp_str": row.get("timestamp_str"),
                        "implied_volatility": row.get("implied_volatility"),
                        "history_volatility": row.get("history_volatility"),
                        "volatility_premium": row.get("volatility_premium"),
                    }
                    records.append(rec)
            print(json.dumps({
                "code": code,
                "data": {
                    "average_impvol": avg_impvol_j,
                    "impvol_status": int(impvol_status_j) if impvol_status_j is not None else None,
                    "analysis": analysis_j or "",
                    "items": records,
                },
            }, ensure_ascii=False, default=str))
            return

        # ---- non-JSON display ----
        avg_impvol = df["average_impvol"].iloc[0] if not df.empty and "average_impvol" in df.columns else None
        status_val = df["impvol_status"].iloc[0] if not df.empty and "impvol_status" in df.columns else None
        status_str = _IMPVOL_STATUS_MAP.get(int(status_val), str(status_val)) if status_val is not None else ""
        analysis = df["analysis"].iloc[0] if not df.empty and "analysis" in df.columns else ""

        print(_SEP64)
        print(f"期权波动率分析  标的：{code}")
        print(_SEP64)
        if avg_impvol is not None:
            print(f"  隐含波动率均值：{avg_impvol:.2f}%")
        if status_val is not None:
            print(f"  波动率状态：    {int(status_val)}({status_str})")
        if analysis:
            print(f"  分析：          {analysis}")
        print()

        if not df.empty:
            disp = df[["timestamp_str", "implied_volatility", "history_volatility", "volatility_premium"]].copy()
            for col in ("implied_volatility", "history_volatility", "volatility_premium"):
                if col in disp.columns:
                    disp[col] = disp[col].apply(lambda x: f"{x:.2f}" if x is not None else "-")
            disp = disp.rename(columns={
                "timestamp_str":      "日期",
                "implied_volatility": "隐含波动率(%)",
                "history_volatility": "历史波动率(%)",
                "volatility_premium": "波动率溢价(%)",
            })
            print_display_df(disp, max_colwidth=20)
        else:
            print("  暂无数据")

        print(_SEP64D)
        print(f"返回条数：{len(df)}")
        print(_SEP64)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误：{e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权波动率数据")
    parser.add_argument("code", help="期权代码，如 US.AAPL260427C270000")
    parser.add_argument("--query-time-period", type=int, default=None, dest="query_time_period",
                        help="查询时间周期：1=周, 2=月, 3=季度, 4=半年, 5=年（默认 2=月）")
    parser.add_argument("--hv-time-period", type=int, default=None, dest="hv_time_period",
                        help="标的物历史波动率周期（5~250 日，默认 30）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_volatility(
        args.code,
        query_time_period=args.query_time_period,
        hv_time_period=args.hv_time_period,
        output_json=args.output_json,
    )
