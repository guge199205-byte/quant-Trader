#!/usr/bin/env python3
"""
获取持股明细

功能：获取股票某一持股类型下的持有人明细列表，支持按类型、排序列、报告期过滤

用法：python get_shareholders_holder_detail.py [-h] [--request-type REQUEST_TYPE] [--next-key NEXT_KEY] [--num NUM] [--sort-column SORT_COLUMN] [--sort-type SORT_TYPE] [--period-id PERIOD_ID] [--holder-id HOLDER_ID] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持港股、美股正股及基金
- 支持分页，默认每页 10 条；分页标识为字符串类型

参数说明：
- code: 股票代码，如 HK.00700
- --request-type: 请求类型：0=默认，1000=全部，1=其他机构，2=传统投资经理，3=对冲基金，4=风险资本/私募，5=企 业年金，6=基金会基金，7=保险公司，8=银行/投资银行，9=家族办公室/信托，10=主权财富基金，11=R EIT，12=结构化融资经理，13=联合养老金，14=政府养老金，15=捐赠基金，100=个人，200=AD S，300=上市公司，400=未公开上市公司，500=国有股
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-column: 排序列：61=持股股数（默认）62=持股变动数
- --sort-type: 排序方式：1=降序（默认），2=升序
- --period-id: 报告期 ID，0=最新
- --holder-id: 持有人对象 ID，0=不过滤；可取自 GetShareholdersOverview（3237）/GetShareholdersHoldingChanges（3238）/本协议（3239）/GetInsiderHolderList（3241）/GetInsiderTradeList（3242）返回的 holder_id

返回字段说明：
- data.update_time_str: 数据更新时间（YYYY-MM-DD HH:MM:SS）
- data.next_key:        分页标识，"-1" 表示无更多数据
- data.items[]:         持股明细列表，每项含 period_text/holder_id/name/holder_quantity/holder_quantity_change/holder_pct/holder_pct_change/holding_date_str/close_price/price_change_pct/source_group_name 等
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

import pandas as pd

SEP64 = "=" * 64
DASH64 = "-" * 64


def _build_display_df(data: pd.DataFrame) -> pd.DataFrame:
    rows = []
    any_large_quantity = any(
        abs(float(r.get("holder_quantity") or 0)) >= 10000
        for _, r in data.iterrows()
    )
    any_large_change = any(
        abs(float(r.get("holder_quantity_change") or 0)) >= 10000
        for _, r in data.iterrows()
    )
    def _isnull(v):
        return v is None or (isinstance(v, float) and pd.isna(v))

    for _, row in data.iterrows():
        holder_id = row.get("holder_id")
        holder_quantity = row.get("holder_quantity")
        holder_quantity_change = row.get("holder_quantity_change")
        holder_pct = row.get("holder_pct")
        holder_pct_change = row.get("holder_pct_change")
        close_price = row.get("close_price")
        price_change_pct = row.get("price_change_pct")
        rows.append({
            "报告期": str(row.get("period_text") or "") or "-",
            "持股日期": str(row.get("holding_date_str") or "") or "-",
            "持有人ID": int(holder_id) if not _isnull(holder_id) else 0,
            "持有人": str(row.get("name") or "") or "-",
            "持股量": (
                format_big_number(holder_quantity) if any_large_quantity and not _isnull(holder_quantity)
                else (str(int(holder_quantity)) if not _isnull(holder_quantity) else "-")
            ),
            "持股量变动": (
                format_big_number(holder_quantity_change) if any_large_change and not _isnull(holder_quantity_change)
                else (str(int(holder_quantity_change)) if not _isnull(holder_quantity_change) else "-")
            ),
            "持股比例(%)": f"{float(holder_pct):.2f}%" if holder_pct is not None else "-",
            "比例变动(%)": f"{float(holder_pct_change):.2f}%" if holder_pct_change is not None else "-",
            "收盘价": f"{float(close_price):.3f}" if close_price is not None else "-",
            "价格涨跌(%)": f"{float(price_change_pct):.2f}%" if price_change_pct is not None else "-",
            "数据来源": str(row.get("source_group_name") or "") or "-",
        })
    return pd.DataFrame(rows)


def get_shareholders_holder_detail(code, request_type=None, next_key=None, num=None,
                                   sort_column=None, sort_type=None, period_id=None, holder_id=None,
                                   output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_shareholders_holder_detail(
            code, request_type=request_type, next_key=next_key, num=num,
            sort_column=sort_column, sort_type=sort_type,
            period_id=period_id, holder_id=holder_id,
        )
        check_ret(ret, data, ctx, "获取持股明细")

        page_next_key = data.attrs.get("next_key", "-1") if hasattr(data, "attrs") else "-1"
        if is_empty(data):
            page_next_key = "-1"
        row_count = len(data) if not is_empty(data) else 0
        next_key_display = "已结束(-1)" if page_next_key == "-1" else str(page_next_key)

        if output_json:
            if is_empty(data):
                records = []
                update_time_str = ""
                response_type = None
            else:
                update_time_str = str(data.iloc[0].get("update_time_str", "") or "")
                records = df_to_records(data)
                for r in records:
                    r.pop("update_time", None)
                    r.pop("update_time_str", None)
            inner = {
                "update_time_str": update_time_str,
                "next_key": page_next_key,
                "items": records,
            }
            print(json.dumps({"code": code, "data": inner}, ensure_ascii=False))
        else:
            if is_empty(data):
                print("无数据")
            else:
                update_time_str = str(data.iloc[0].get("update_time_str", "") or "")
                print(SEP64)
                print(f"持股明细  标的：{code}" + (f"  更新：{update_time_str}" if update_time_str else ""))
                print(DASH64)
                view = _build_display_df(data)
                print_display_df(view, max_colwidth=28)
                print(DASH64)
                print(f"返回条数：{row_count}   --next-key：{next_key_display}")
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
    parser = argparse.ArgumentParser(description="获取特定股东持股明细，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--request-type", type=int, default=None, dest="request_type",
                        help="请求类型：0=默认，1000=全部，1=其他机构，2=传统投资经理，3=对冲基金，"
                             "4=风险资本/私募，5=企业年金，6=基金会基金，7=保险公司，8=银行/投资银行，"
                             "9=家族办公室/信托，10=主权财富基金，11=REIT，12=结构化融资经理，"
                             "13=联合养老金，14=政府养老金，15=捐赠基金，100=个人，200=ADS，"
                             "300=上市公司，400=未公开上市公司，500=国有股")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~50")
    parser.add_argument("--sort-column", type=int, default=None, dest="sort_column",
                        help="排序列（Qot_Common.SortField）：61=持股股数（默认）62=持股变动数")
    parser.add_argument("--sort-type", type=int, default=None, dest="sort_type",
                        help="排序方式：1=降序（默认），2=升序")
    parser.add_argument("--period-id", type=int, default=None, dest="period_id",
                        help="报告期 ID，0=最新")
    parser.add_argument("--holder-id", type=int, default=None, dest="holder_id",
                        help="持有人对象 ID，0=不过滤；可取自 GetShareholdersOverview（3237）/GetShareholdersHoldingChanges（3238）/本协议（3239）/GetInsiderHolderList（3241）/GetInsiderTradeList（3242）返回的 holder_id")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_shareholders_holder_detail(
        args.code,
        request_type=args.request_type,
        next_key=args.next_key,
        num=args.num,
        sort_column=args.sort_column,
        sort_type=args.sort_type,
        period_id=args.period_id,
        holder_id=args.holder_id,
        output_json=args.output_json,
    )
