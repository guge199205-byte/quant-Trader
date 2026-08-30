#!/usr/bin/env python3
"""
获取个股财报日前后股价历史

功能：查询个股历次财报周期中财报当日的股价历史数据，含财报元信息、预期波动率及 IV Crush 分析
用法：python get_financials_earnings_price_history.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 市场限制：支持港股、美股正股

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- data[]: 按财报期+偏移交易日展开的平铺列表；每行含财报元信息（fiscal_year/financial_type/period_text/pub_trading_day_str/pub_type/is_current/predict_vola_ratio_newest/predict_vola_ratio_highest/predict_vola_val_newest/predict_vola_val_highest/option_iv_crush/option_strike_date_iv_crush）、发布日行情（trading_day_str/open_price/close_price/highest_price/lowest_price/last_close_price/volume）及相对偏移收盘价（schedule_delta/schedule_close_price）
"""
import argparse
import json
import math
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

# 展示列（去冗余：只保留字符串时间，不显示原始时间戳；period_text 替代 fiscal_year/financial_type）
DISPLAY_COLUMNS = [
    "period_text",
    "is_current",
    "pub_trading_day_str",
    "pub_time_str",
    "pub_type",
    "predict_vola_ratio_newest",
    "predict_vola_ratio_highest",
    "predict_vola_val_newest",
    "predict_vola_val_highest",
    "option_iv_crush",
    "option_strike_date_iv_crush",
    "trading_day_str",
    "close_price",
    "open_price",
    "highest_price",
    "lowest_price",
    "last_close_price",
    "volume",
    "earnings_pre14d",
    "earnings_post14d",
]

DISPLAY_CN = {
    "period_text":                "财报期",
    "is_current":                 "当前期",
    "pub_trading_day_str":        "发布交易日",
    "pub_time_str":               "发布时间",
    "pub_type":                   "发布时间类型",
    "predict_vola_ratio_newest":  "最新预测波动%",
    "predict_vola_ratio_highest": "最高预期波动%",
    "predict_vola_val_newest":    "最新预期波动",
    "predict_vola_val_highest":   "最高预期波动",
    "option_iv_crush":            "IV Crush%",
    "option_strike_date_iv_crush":"行权日IVCrush%",
    "trading_day_str":            "交易日",
    "close_price":                "收盘价",
    "open_price":                 "开盘价",
    "highest_price":              "最高价",
    "lowest_price":               "最低价",
    "last_close_price":           "昨收",
    "volume":                     "成交量",
    "earnings_pre14d":            "财报前14日%",
    "earnings_post14d":           "财报后14日%",
}

_PUB_TYPE_CN = {0: "-", 1: "盘前", 2: "盘后", 3: "盘中"}

_PRICE_COLS = {
    "close_price", "open_price", "highest_price", "lowest_price",
    "last_close_price", "predict_vola_val_newest", "predict_vola_val_highest",
}
_PCT_COLS = {
    "predict_vola_ratio_newest", "predict_vola_ratio_highest",
    "option_iv_crush", "option_strike_date_iv_crush",
    "earnings_pre14d", "earnings_post14d",
}


def _is_numeric_val(x):
    import math
    if x is None:
        return False
    if isinstance(x, float) and math.isnan(x):
        return False
    if isinstance(x, (int, float)):
        return True
    s = str(x).strip()
    if not s or s in ("-", "None", "nan", "N/A"):
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _apply_display_scale(df):
    """格式化 double 字段用于终端展示（返回副本）"""
    import pandas as pd
    df = df.copy()
    for col in _PRICE_COLS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: f"{float(x):.3f}" if _is_numeric_val(x) else "-"
            )
    for col in _PCT_COLS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: f"{float(x):.2f}" if _is_numeric_val(x) else "-"
            )
    if "volume" in df.columns:
        def _fmt_volume(x):
            if not _is_numeric_val(x):
                return "-"
            v = float(x)
            if v >= 1e8:
                return f"{v / 1e8:.2f}亿"
            if v >= 1e4:
                return f"{v / 1e4:.2f}万"
            return f"{v:.0f}"
        df["volume"] = df["volume"].apply(_fmt_volume)
    if "pub_time_str" in df.columns:
        df["pub_time_str"] = df["pub_time_str"].apply(
            lambda x: str(x).strip() if (x is not None and str(x).strip() not in ("", "-", "None", "nan")) else "-"
        )
    if "pub_type" in df.columns:
        df["pub_type"] = df["pub_type"].apply(
            lambda x: _PUB_TYPE_CN.get(int(float(x)), "-") if _is_numeric_val(x) else "-"
        )
    if "is_current" in df.columns:
        df["is_current"] = df["is_current"].map(
            lambda x: "-" if (x is None or str(x) in ("nan", "None", ""))
            else ("是" if x is True or x == "True" or x == 1
                  else ("否" if x is False or x == "False" or x == 0 else str(x)))
        )
    df = df.fillna("-")
    df = df.replace("", "-")
    return df


def _compute_schedule_metrics(df):
    """将 long 格式 schedule 数据聚合为每期一行，计算前14日/后14日涨跌幅（%）。"""
    period_col = "period_text"
    if period_col not in df.columns or "schedule_delta" not in df.columns:
        return df

    period_deltas = {}
    for _, row in df.iterrows():
        pt = row.get(period_col)
        key = str(pt) if pt is not None else ""
        if key not in period_deltas:
            period_deltas[key] = {}
        d = row.get("schedule_delta")
        c = row.get("schedule_close_price")
        try:
            di = int(float(d))
            cv = float(c)
            if not math.isnan(cv):
                period_deltas[key][di] = cv
        except (TypeError, ValueError):
            pass

    def _pct_change(dm, d_num, d_base):
        p_num = dm.get(d_num)
        p_base = dm.get(d_base)
        if p_num is None or p_base is None or p_base == 0:
            return None
        return (p_num - p_base) / p_base * 100

    base = df.drop_duplicates(subset=[period_col], keep="first").reset_index(drop=True).copy()
    pre14, post14 = [], []
    for pt in base[period_col]:
        dm = period_deltas.get(str(pt) if pt is not None else "", {})
        pre14.append(_pct_change(dm, -1, -15))
        post14.append(_pct_change(dm, 14, 0))
    base["earnings_pre14d"] = pre14
    base["earnings_post14d"] = post14
    return base


def get_financials_earnings_price_history(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        api = getattr(ctx, "get_financials_earnings_price_history", None)
        if not callable(api):
            raise AttributeError("当前 futu-api 不支持 get_financials_earnings_price_history，请升级 SDK。")
        ret, data = api(code)
        check_ret(ret, data, ctx, "获取财报日前后股价历史")

        if output_json:
            if is_empty(data):
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print(json.dumps({"code": code, "data": df_to_records(data)},
                                 ensure_ascii=False, default=str))
        else:
            if is_empty(data):
                print("无数据")
            else:
                agg = _compute_schedule_metrics(data)
                disp = _apply_display_scale(agg)
                avail = [c for c in DISPLAY_COLUMNS if c in disp.columns]
                out = disp[avail].rename(columns={k: DISPLAY_CN[k] for k in avail if k in DISPLAY_CN})
                print(SEP64)
                print(f"财报日前后股价历史  标的：{code}")
                print(DASH64)
                print_display_df(out, max_colwidth=25)
                print(DASH64)
                print(f"共 {len(agg)} 期")
                print(SEP64)

    except SystemExit:
        raise
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="获取各财报周期的历史行情数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "code",
        help="股票代码，如 HK.00700",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()
    get_financials_earnings_price_history(args.code, output_json=args.output_json)
