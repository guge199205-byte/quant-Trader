#!/usr/bin/env python3
"""
获取分析师评级概述

功能：获取指定股票近3个月的分析师综合评级、目标价区间及各档评级占比
用法：python get_research_analyst_consensus.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及 REIT

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- highest / average / lowest: 目标价区间
- rating:           综合评级（Qot_Common.ResearchRatingType）
- total:            参与评级的分析师总人数（近3个月）
- update_time_str:  评级数据更新日期（YYYY-MM-DD）
- buy / hold / sell: 各评级占比，百分号前的值
- strong_buy / underperform: 仅非美市场返回
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty

# 近3个月分析师综合评级，与 ResearchRatingType 枚举对应
_RATING_LABEL = {
    1: "卖出",
    2: "跑输大盘",
    3: "持有",
    4: "买入",
    5: "强力推荐",
}

_DIST_ROWS_ALL = [
    ("strong_buy",   "强力推荐"),
    ("buy",          "买入"),
    ("hold",         "持有"),
    ("underperform", "跑输大盘"),
    ("sell",         "卖出"),
]
_DIST_ROWS_US = [
    ("buy",  "买入"),
    ("hold", "持有"),
    ("sell", "卖出"),
]

SEP64 = "=" * 64
DASH64 = "-" * 64


def _cjk_ljust(s, width):
    """按显示宽度左对齐（CJK 字符占 2 列）。"""
    dw = sum(2 if '\u4e00' <= c <= '\u9fff' else 1 for c in s)
    return s + ' ' * max(0, width - dw)


def get_research_analyst_consensus(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_research_analyst_consensus(code)
        check_ret(ret, data, ctx, "获取分析师评级概述")

        if is_empty(data) or not any(data.values()):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False))
            return

        print(SEP64)
        print(f"分析师评级概述  标的：{code}")
        print(DASH64)

        highest = data.get("highest")
        average = data.get("average")
        lowest = data.get("lowest")
        if highest is not None:
            print(f"最高目标价: {highest:.3f}")
        if average is not None:
            print(f"平均目标价: {average:.3f}")
        else:
            print("平均目标价: —")
        if lowest is not None:
            print(f"最低目标价: {lowest:.3f}")

        rating = data.get("rating")
        if rating is not None:
            print(f"综合评级:   {_RATING_LABEL.get(rating, str(rating))} ({rating})")
        else:
            print("综合评级:   —")
        total = data.get("total")
        if total is not None:
            print(f"总分析师数: {total}")
        update_time_str = data.get("update_time_str")
        if update_time_str:
            print(f"更新日期:   {update_time_str}")

        print()
        print("评级分布:")
        is_us = code.upper().startswith("US.")
        dist_rows = _DIST_ROWS_US if is_us else _DIST_ROWS_ALL
        max_lw = max(sum(2 if '\u4e00' <= c <= '\u9fff' else 1 for c in lbl) for _, lbl in dist_rows)
        for key, label in dist_rows:
            pct = data.get(key)
            padded = _cjk_ljust(label, max_lw)
            if pct is not None:
                print(f"  {padded}: {pct:.2f}%")
            else:
                print(f"  {padded}: —")

        print(DASH64)
        field_count = len(data)
        print(f"返回字段数：{field_count}")
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
    parser = argparse.ArgumentParser(description="获取分析师综合评级和目标价")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()
    get_research_analyst_consensus(args.code, output_json=args.output_json)
