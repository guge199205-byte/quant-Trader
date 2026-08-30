#!/usr/bin/env python3
"""
获取估值详情

功能：获取指定个股或指数的估值详情，包含走势、市场分布、行业分布及盈利/营收增速四个聚合模块；个股返回全部模块，指数仅返回走势和市场分布
用法：python get_valuation_detail.py [-h] [--valuation-type VALUATION_TYPE] [--interval-type INTERVAL_TYPE] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股、基金及指数
- PB 估值类型无盈利增速模块；指数无排名、均值、中位数字段

参数说明：
- code: 股票或指数代码，如 HK.00700
- --valuation-type: 估值类型：1=PE, 2=PB, 3=PS（默认不传，服务端推荐）
- --interval-type: 时间周期（有效值 1-10）：1=3月 2=6月 3=1年 4=3年 5=从2019年起 6=5年 7=10年 8=2年 9=20年 10=30年（默认：3=1年）

返回字段说明：
- valuation_type:       实际返回的估值类型（PE/PB/PS）
- last_update_time_str: 最后更新时间（YYYY-MM-DD HH:MM:SS）
- trend:                估值走势，含 current_value/average_value/valuation_percentile（分位，百分号前的值）/forward_value/historical_items 等
- market_distribution:  市场分布，含区间列表 sections（start/end/number）/total/ranking/average_value 等
- plate_distribution:   行业分布（仅个股），含板块信息及成分股估值明细
- profit_growth_rate:   盈利/营收增速（仅个股，PB 无），含增长倍数及各期明细
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
    safe_float,
    safe_int,
    print_display_df,
    format_big_number,
    pad_disp,
)

SEP = "=" * 64
DASH = "-" * 64

_VALUATION_TYPE_MAP = {0: "推荐", 1: "PE（市盈率）", 2: "PB（市净率）", 3: "PS（市销率）"}
_INTERVAL_TYPE_MAP = {
    1: "3个月", 2: "6个月", 3: "1年", 4: "3年", 5: "从2019年起",
    6: "5年", 7: "10年", 8: "2年", 9: "20年", 10: "30年",
}
_DEFAULT_INTERVAL_TYPE = 3  # 与服务端默认一致：未传时按 1 年


def _fmt(v, digits=2, na="-"):
    """格式化浮点数，None 返回 na。"""
    if v is None:
        return na
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return na


def _build_trend_summary(trend, interval_type=None):
    """返回趋势摘要行列表 [(标签, 值)]。

    interval_type 为请求时使用的时间周期（None 时回落到服务端默认 3=1年）。
    """
    it = interval_type if interval_type is not None else _DEFAULT_INTERVAL_TYPE
    interval_str = _INTERVAL_TYPE_MAP.get(safe_int(it), str(it))
    rows = [
        ("请求周期",    interval_str),
        ("当前估值",    _fmt(trend.get("current_value"))),
        ("历史均值",    _fmt(trend.get("average_value"))),
        ("均值-1σ",     _fmt(trend.get("avg_minus_1_stddev"))),
        ("均值+1σ",     _fmt(trend.get("avg_plus_1_stddev"))),
    ]
    if "forward_value" in trend:
        rows.append(("预测估值", _fmt(trend.get("forward_value"))))
    if "valuation_percentile" in trend:
        pct = safe_float(trend.get("valuation_percentile"))
        rows.append(("历史分位", f"{pct:.2f}%"))
    return rows


def _build_hist_df(hist_items):
    """构建历史数据 DataFrame（去冗余：只展示 timeStr，不展示原始时间戳）。"""
    has_plate = any("plate_value" in item for item in hist_items)
    rows = []
    for item in hist_items:
        row = {
            "日期":  item.get("time_str", ""),
            "估值":  _fmt(item.get("value")),
        }
        if has_plate:
            row["行业均值"] = _fmt(item.get("plate_value"))
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fmt_section_range(start, end):
    """合并区间起/终为单列：仅起 -> "xxx以上"；仅终 -> "xxx以下"；都有 -> "xxx ~ yyy"。"""
    has_start = start is not None
    has_end = end is not None
    if has_start and has_end:
        return f"{_fmt(start)} ~ {_fmt(end)}"
    if has_start:
        return f"{_fmt(start)} 以上"
    if has_end:
        return f"{_fmt(end)} 以下"
    return "-"


def _build_sections_df(sections, total=None):
    """构建市场区间分布 DataFrame（合并 start/end 为单列「区间」，附占比）。"""
    try:
        total_int = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_int = 0
    rows = []
    for sec in sections:
        num = safe_int(sec.get("number"))
        if total_int > 0:
            pct_str = f"{num / total_int * 100:.2f}%"
        else:
            pct_str = "-"
        rows.append({
            "区间":   _fmt_section_range(sec.get("start"), sec.get("end")),
            "个股数": num,
            "占比":   pct_str,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _build_stock_items_df(stock_items):
    """构建板块成分股 DataFrame。"""
    rows = []
    for item in stock_items:
        rows.append({
            "标的":     item.get("symbol", ""),
            "名称":     item.get("name", ""),
            "估值":     _fmt(item.get("value")),
            "市值":     format_big_number(item.get("market_cap")),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _build_profit_data_df(profit_data):
    """构建盈利增速 DataFrame（去冗余：只展示 periodStr、reportDateStr，不展示原始年/季/日期戳）。"""
    rows = []
    for item in profit_data:
        rows.append({
            "财报周期":         item.get("period_str", ""),
            "报告日":           item.get("report_date_str", ""),
            "市值倍数":         _fmt(item.get("market_cap_multiple")),
            "财务数据倍数":     _fmt(item.get("finance_data_multiple")),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_valuation_detail(code, valuation_type=None, interval_type=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_valuation_detail(
            code,
            valuation_type=valuation_type,
            interval_type=interval_type,
        )
        check_ret(ret, data, ctx, "获取估值详情")

        if not data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print(SEP)
                print(f"[估值详情]  标的：{code}")
                print(DASH)
                print("无数据")
                print(SEP)
            return

        vt = safe_int(data.get("valuation_type", 0))
        vt_str = _VALUATION_TYPE_MAP.get(vt, str(vt))

        trend = data.get("trend")
        md    = data.get("market_distribution")
        pgr   = data.get("profit_growth_rate")

        def _no_data(d, empty_key):
            if not d:
                return True
            return set(d.keys()) <= {empty_key} and not d.get(empty_key)

        trend_has_data = not _no_data(trend, "historical_items")
        md_has_data    = not _no_data(md,    "sections")
        pgr_has_data   = not _no_data(pgr,   "profit_data")
        all_no_data    = not trend_has_data and not md_has_data and not pgr_has_data

        if output_json:
            out = {"code": code, "data": {} if all_no_data else data}
            print(json.dumps(out, ensure_ascii=False, default=str))
            return

        # ── 非 JSON 展示 ──────────────────────────────────────────────────────
        if all_no_data:
            print("无数据")
            return

        upd_str = data.get("last_update_time_str", "")

        print(SEP)
        print(f"[估值详情]  标的：{code}")
        print(DASH)

        # 趋势摘要（按显示宽度对齐，避免 σ 等字符宽度差异导致错位）
        if trend:
            print(f"\n估值类型（实际）：{vt_str}  最后更新：{upd_str}")
            if not trend_has_data:
                print("\n【趋势摘要】  无数据")
            else:
                print("\n【趋势摘要】")
                for label, val in _build_trend_summary(trend, interval_type=interval_type):
                    print(f"  {pad_disp(label, 12)} {val}")

                hist = trend.get("historical_items", [])
                print(f"\n【历史估值】共 {len(hist)} 条")
                if hist:
                    print_display_df(_build_hist_df(hist))
                else:
                    print("  无历史数据")

        # 市场区间分布
        if md:
            sections = md.get("sections", [])
            total = md.get("total")
            if total is None and not sections:
                print("\n【市场区间分布】  无数据")
            else:
                ranking = md.get("ranking")
                avg_v = md.get("average_value")
                med_v = md.get("median_value")
                header = f"\n【市场区间分布】共 {len(sections)} 段  总数={total if total is not None else '-'}"
                if ranking is not None:
                    header += f"  排名={ranking}/{total if total is not None else '-'}"
                if avg_v is not None:
                    header += f"  均值={_fmt(avg_v)}  中位={_fmt(med_v)}"
                print(header)
                if sections:
                    print_display_df(_build_sections_df(sections, total))

        # 板块分布（仅个股）
        plate = data.get("plate_distribution")
        if plate:
            plate_sym = plate.get("plate", "")
            plate_name = plate.get("plate_name", "")
            p_avg = _fmt(plate.get("plate_average_value"))
            p_rank = safe_int(plate.get("plate_ranking"))
            p_cnt = safe_int(plate.get("plate_stock_item_count"))
            print(f"\n【板块分布】板块={plate_sym} {plate_name}  板块均值={p_avg}  排名={p_rank}/{p_cnt}")
            stock_items = plate.get("stock_items", [])
            if stock_items:
                print(f"  板块成分股共 {len(stock_items)} 只:")
                print_display_df(_build_stock_items_df(stock_items))

        # 盈利/营收增速（仅个股 PE/PS；PS 展示为营收增速）
        if pgr:
            section_title = "营收增速" if vt == 3 else "盈利增速"
            ttm_label = "营收TTM倍数" if vt == 3 else "净利润TTM倍数"
            if pgr.get("financial_ttm_multiple") is None:
                print(f"\n【{section_title}】  无数据")
            else:
                ttm_m = _fmt(pgr.get("financial_ttm_multiple"))
                cap_m = _fmt(pgr.get("market_cap_multiple"))
                yr_cnt = safe_int(pgr.get("year_count"))
                print(f"\n【{section_title}】{ttm_label}={ttm_m}  市值倍数={cap_m}  年数={yr_cnt}")
                conclusion = pgr.get("conclusion_detailed")
                if conclusion:
                    print(f"  估值结论: {conclusion}")
                profit_data = pgr.get("profit_data", [])
                if profit_data:
                    data_label = "营收数据" if vt == 3 else "利润数据"
                    print(f"  {data_label}共 {len(profit_data)} 条:")
                    print_display_df(_build_profit_data_df(profit_data))

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
    parser = argparse.ArgumentParser(description="获取估值详情")
    parser.add_argument("code",
                        help="股票或指数代码，如 HK.00700")
    parser.add_argument("--valuation-type", type=int, default=None, dest="valuation_type",
                        help="估值类型：1=PE, 2=PB, 3=PS（默认不传，服务端推荐）")
    parser.add_argument("--interval-type", type=int, default=None, dest="interval_type",
                        help="时间周期（有效值 1-10）：1=3月 2=6月 3=1年 4=3年 5=从2019年起 6=5年 7=10年 8=2年 9=20年 10=30年（默认：3=1年）")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()

    get_valuation_detail(
        args.code,
        valuation_type=args.valuation_type,
        interval_type=args.interval_type,
        output_json=args.output_json,
    )
