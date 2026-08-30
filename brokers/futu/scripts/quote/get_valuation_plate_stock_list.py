#!/usr/bin/env python3
"""
获取板块/指数成分股估值列表

功能：获取板块或指数成分股的估值列表，包含估值、预测估值、历史分位、市值等；指数首次请求还返回所属板块列表
用法：python get_valuation_plate_stock_list.py [-h] [--valuation-type VALUATION_TYPE] [--next-key NEXT_KEY] [--num NUM] [--sort-type SORT_TYPE] [--sort-id SORT_ID] [--filter-security FILTER_SECURITY] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持板块和指数；不支持个股
- 指数作为入参时，首次请求额外返回所属板块列表（plate_list）

参数说明：
- code: 板块或指数代码，如 HK.800000
- --valuation-type: 估值类型：1=市盈率(PE), 2=市净率(PB), 3=市销率(PS)（默认：1=市盈率(PE)）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-type: 排序方向：1=Desc(降序), 2=Asc(升序)（默认：2=升序）
- --sort-id: 排序列：51=市值（默认）52=估值 53=预测估值 54=历史分位
- --filter-security: 仅对指数有效：按行业/板块筛选成分股（如 HK.LIST23363）；不传则不筛选

返回字段说明：
- data.count:         成分股总数
- data.next_key:      分页标识，"-1" 表示无更多数据
- data.stock_list:    成分股估值列表，每项含 symbol/name/valuation_val/forward_value/valuation_percentile（分位，百分号前的值）/market_cap
- data.plate_list:    指数场景下成分股所属板块列表（仅全量首页），每项含 symbol/name
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
    print_display_df,
    format_big_number,
)

SEP_EQ = "=" * 64
SEP_DA = "-" * 64

_VALUATION_TYPE_MAP = {1: "市盈率(PE)", 2: "市净率(PB)", 3: "市销率(PS)"}
_SORT_TYPE_MAP = {0: "未知", 1: "降序", 2: "升序"}
_SORT_ID_MAP = {1: "市值", 2: "估值", 3: "预测估值", 4: "历史分位"}


def _vtype_str(v):
    if v is None:
        return _VALUATION_TYPE_MAP[1]
    return _VALUATION_TYPE_MAP.get(v, str(v))


def _fmt_pct(val):
    """显示估值历史分位（百分号前的值），直接加 %"""
    if val is None:
        return "-"
    try:
        return f"{float(val):.2f}%"
    except Exception:
        return str(val)


def _fmt_float(val, decimals=2):
    if val is None:
        return "-"
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return str(val)


def get_valuation_plate_stock_list(code, valuation_type=None, next_key=None, num=None,
                                   sort_type=None, sort_id=None, filter_security=None,
                                   output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_valuation_plate_stock_list(
            code,
            valuation_type=valuation_type,
            next_key=next_key,
            num=num,
            sort_type=sort_type,
            sort_id=sort_id,
            filter_security=filter_security,
        )
        check_ret(ret, data, ctx, "获取板块/指数成分股估值")

        if not data:
            stock_list = []
            total = 0
            nxt = -1
            plate_list = []
        else:
            stock_list = data.get("stock_list", [])
            total = data.get("count", len(stock_list))
            nxt = data.get("next_key", -1)
            plate_list = data.get("plate_list", [])

        nextkey_str = f"已结束(-1)" if nxt in ("-1", -1, None) else str(nxt)

        if not stock_list and not plate_list:
            if output_json:
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": {
                "count": total,
                "next_key": nxt,
                "stock_list": stock_list,
                "plate_list": plate_list,
            }}, ensure_ascii=False, default=str))
            return

        print(SEP_EQ)
        print(f"板块/指数成分股估值  标的：{code}")
        print(SEP_DA)

        if stock_list:
            rows = []
            for item in stock_list:
                sym = item.get("symbol", "-")
                name = item.get("name", "-") or "-"
                vval = _fmt_float(item.get("valuation_val"), 2)
                fwd = _fmt_float(item.get("forward_value"), 2)
                pct = _fmt_pct(item.get("valuation_percentile"))
                cap_str = format_big_number(item.get("market_cap"))
                rows.append({
                    "代码": sym,
                    "名称": name,
                    "估值": vval,
                    "预测估值": fwd,
                    "历史分位": pct,
                    "市值": cap_str,
                })
            df = pd.DataFrame(rows)
            print_display_df(df, max_colwidth=20)
        if plate_list:
            print()
            print(f"指数所属板块列表（共 {len(plate_list)} 个）：")
            plate_rows = [{"序号": i + 1, "板块代码": p.get("symbol", "-"), "板块名称": p.get("name", "-")} for i, p in enumerate(plate_list)]
            print_display_df(pd.DataFrame(plate_rows), max_colwidth=30)

        print(SEP_DA)
        print(f"返回条数：{len(stock_list)}   --next-key：{nextkey_str}")
        print(SEP_EQ)

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
    parser = argparse.ArgumentParser(description="获取板块/指数成分股估值列表，支持分页拉取")
    parser.add_argument("code", help="板块或指数代码，如 HK.800000")
    parser.add_argument("--valuation-type", type=int, default=None, dest="valuation_type",
                        help="估值类型：1=市盈率(PE), 2=市净率(PB), 3=市销率(PS)（默认：1=市盈率(PE)）")
    parser.add_argument("--next-key", type=str, default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None, dest="num",
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--sort-type", type=int, default=None, dest="sort_type",
                        help="排序方向（Qot_Common.SortType）：1=Desc(降序), 2=Asc(升序)（默认：2=升序）")
    parser.add_argument("--sort-id", type=int, default=None, dest="sort_id",
                        help="排序列（Qot_Common.SortField）：51=市值（默认）52=估值 53=预测估值 54=历史分位")
    parser.add_argument("--filter-security", default=None, dest="filter_security",
                        help="仅对指数有效：按行业/板块筛选成分股（如 HK.LIST23363）；不传则不筛选")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()

    get_valuation_plate_stock_list(
        args.code,
        valuation_type=args.valuation_type,
        next_key=args.next_key,
        num=args.num,
        sort_type=args.sort_type,
        sort_id=args.sort_id,
        filter_security=args.filter_security,
        output_json=args.output_json,
    )
