#!/usr/bin/env python3
"""
获取回购

功能：获取股票的回购记录（港股 / A 股，支持分页）
用法：python get_corporate_actions_buybacks.py [-h] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、A股正股及基金
- 港股和A股各返回独立数据表，字段结构不同

参数说明：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- data.next_key:         分页标识，"-1" 表示无更多数据
- data.hk_buy_back_list: 港股回购列表，每项含 publ_date_str/end_date_str/buy_back_money/buy_back_sum/percentage/high_price/low_price/cumulative_sum/cumulative_percentage/share_type 等
- data.a_buy_back_list:  A股回购列表，每项含 advance_date_str/start_date_str/end_date_str/buy_back_mode/buy_back_sum/buy_back_money/percentage 等进程日期和金额/股数字段
"""
import argparse
import json
import sys
import os as _os

import pandas as pd

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


def _opt(val):
    if _is_null(val) or val == "":
        return "-"
    return str(val)


def _is_null(val):
    """判断值是否为空/NaN"""
    if val is None:
        return True
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return True
    except Exception:
        pass
    return False


def _fmt_money(val):
    """金额格式化（按亿/万缩放）"""
    if _is_null(val):
        return "-"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "-"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.2f}"


def _fmt_pct(val):
    """百分比格式化（百分号前的值，如 12.34 表示 12.34%）"""
    if _is_null(val):
        return "-"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "-"
    return f"{v:.6f}%"


def _fmt_price(val):
    """价格格式化，保留两位小数"""
    if _is_null(val):
        return "-"
    try:
        return f"{float(val):.3f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_range(lo, hi, fmt_fn=None):
    """合并下限~上限为区间字符串"""
    lo_str = fmt_fn(lo) if fmt_fn else _opt(lo)
    hi_str = fmt_fn(hi) if fmt_fn else _opt(hi)
    if lo_str == "-" and hi_str == "-":
        return "-"
    if lo_str == "-":
        return f"~{hi_str}"
    if hi_str == "-":
        return f"{lo_str}~"
    if lo_str == hi_str:
        return lo_str
    return f"{lo_str}~{hi_str}"


def _fmt_date_range(s, e):
    """合并开始~结束日期为区间字符串"""
    s_str = _opt(s)
    e_str = _opt(e)
    if s_str == "-" and e_str == "-":
        return "-"
    if s_str == "-":
        return e_str
    if e_str == "-":
        return s_str
    if s_str == e_str:
        return s_str
    return f"{s_str}~{e_str}"


def _build_hk_display_df(df):
    """港股回购展示 DataFrame（全字段，去冗余时间戳）"""
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "公告日期":                       _opt(row.get("publ_date_str")),
            "回购日期":                       _opt(row.get("end_date_str")),
            "回购金额":                       _fmt_money(row.get("buy_back_money")),
            "回购数量":                       format_big_number(row.get("buy_back_sum")),
            "占总股本比例":                   _fmt_pct(row.get("percentage")),
            "回购价格":                       _fmt_range(row.get("low_price"), row.get("high_price"), _fmt_price),
            "本轮累计回购数量":               format_big_number(row.get("cumulative_sum")),
            "本轮累计回购数量占总股本的比例": _fmt_pct(row.get("cumulative_percentage")),
            "股份类别":                       _opt(row.get("share_type")),
        })
    return pd.DataFrame(rows)


def _build_a_display_df(df):
    """A股回购展示 DataFrame（全字段，去冗余时间戳）"""
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "工商变更登记日":     _opt(row.get("change_reg_date_str")),
            "回购股本变动公告日": _opt(row.get("change_date_str")),
            "事件进程":           _opt(row.get("event_proce_desc")),
            "预案公告日":         _opt(row.get("advance_date_str")),
            "股东大会通过日期":   _opt(row.get("meet_pass_date_str")),
            "回购期限":           _fmt_date_range(row.get("start_date_str"), row.get("end_date_str")),
            "资金划出日":         _opt(row.get("pay_date_str")),
            "股份被回购方":       _opt(row.get("seller")),
            "回购方式":           _opt(row.get("buy_back_mode")),
            "股份类别":           _opt(row.get("share_type")),
            "回购数量":           format_big_number(row.get("buy_back_sum")),
            "回购金额":           _fmt_money(row.get("buy_back_money")),
            "占总股本比例":       _fmt_pct(row.get("percentage")),
            "拟回购资金总额":     _fmt_range(row.get("value_floor"), row.get("value_ceiling"), _fmt_money),
            "回购价格":           _fmt_range(row.get("price_floor"), row.get("price_ceiling"), _fmt_price),
            "拟回购股数":         _fmt_range(row.get("volume_floor"), row.get("volume_ceiling"), format_big_number),
        })
    return pd.DataFrame(rows)


def _nk_display(nk):
    """nextKey 状态文字"""
    if nk == "-1" or nk is None:
        return "已结束(-1)"
    return str(nk)


def get_corporate_actions_buybacks(code, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_corporate_actions_buybacks(code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取回购")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": {"next_key": "-1", "hk_buy_back_list": [], "a_buy_back_list": []}}))
            else:
                print("无数据")
            return

        nk = data.get("next_key", "-1") if isinstance(data, dict) else "-1"
        hk_df = data.get("hk_buy_back_list") if isinstance(data, dict) else None
        a_df = data.get("a_buy_back_list") if isinstance(data, dict) else None

        if hk_df is None or not isinstance(hk_df, pd.DataFrame):
            hk_df = pd.DataFrame()
        if a_df is None or not isinstance(a_df, pd.DataFrame):
            a_df = pd.DataFrame()

        hk_empty = hk_df.empty
        a_empty = a_df.empty

        total_count = (0 if hk_empty else len(hk_df)) + (0 if a_empty else len(a_df))

        if output_json:
            print(json.dumps({
                "code": code,
                "data": {
                    "next_key": nk,
                    "hk_buy_back_list": df_to_records(hk_df) if not hk_empty else [],
                    "a_buy_back_list": df_to_records(a_df) if not a_empty else [],
                },
            }, ensure_ascii=False, default=str))
        else:
            if hk_empty and a_empty:
                print("无数据")
            else:
                print(SEP)
                print(f"回购  标的：{code}")
                print(SEP2)
                if not hk_empty:
                    print(f"港股回购（{len(hk_df)} 条）")
                    print(SEP2)
                    print_display_df(_build_hk_display_df(hk_df), max_colwidth=30)
                    if not a_empty:
                        print(SEP2)
                if not a_empty:
                    print(f"A股回购（{len(a_df)} 条）")
                    print(SEP2)
                    print_display_df(_build_a_display_df(a_df), max_colwidth=24)
                print(SEP2)
                print(f"返回条数：{total_count}  --next-key：{_nk_display(nk)}")
                print(SEP)

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
        description="获取股票回购历史，支持分页拉取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "code",
        help="股票代码，如 HK.00700",
    )
    parser.add_argument(
        "--next-key",
        default=None,
        dest="next_key",
        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据",
    )
    parser.add_argument(
        "--num",
        type=int,
        default=None,
        help="每页返回数量，默认 10，范围 1~50",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()

    get_corporate_actions_buybacks(
        args.code,
        next_key=args.next_key,
        num=args.num,
        output_json=args.output_json,
    )
