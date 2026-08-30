#!/usr/bin/env python3
"""
获取公司高管信息

功能：获取指定股票的董事及高管列表，包含展示名称、姓名、职位、任职起始日、发布日期、性别、年龄、学历、年薪
用法：python get_company_executives.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- data[]:        董事高管列表，每项含 display_leader_name（展示名）/leader_name（用于查询背景）/position_name/begin_date_str/issue_date_str/leader_gender/leader_age/highest_education/annual_salary 等
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

SEP64 = "=" * 64
DASH64 = "-" * 64


def _build_display_df(data):
    """将回包 DataFrame 转换为终端展示格式（去掉冗余时间戳列，大数字转换）。"""
    def _has_large(col):
        if col not in data.columns:
            return False
        return any(
            v is not None and not (isinstance(v, float) and pd.isna(v))
            and abs(float(v)) >= 10000
            for v in data[col]
        )

    salary_large = _has_large("annual_salary")

    rows = []
    for _, row in data.iterrows():
        def _v(col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "-"
            if str(v) == "":
                return "-"
            return v

        # annual_salary
        sal_raw = row.get("annual_salary")
        if sal_raw is None or (isinstance(sal_raw, float) and pd.isna(sal_raw)):
            salary_disp = "-"
        elif salary_large:
            salary_disp = format_big_number(sal_raw)
        else:
            salary_disp = str(int(sal_raw))

        rows.append({
            "姓名": _v("display_leader_name"),
            "职务": _v("position_name"),
            "年薪": salary_disp,
            "任职日期": _v("begin_date_str"),
            "学历": _v("highest_education"),
            "年龄": _v("leader_age"),
            "性别": _v("leader_gender"),
            "更新日期": _v("issue_date_str"),
            "高管背景请求参数": _v("leader_name"),
        })

    return pd.DataFrame(rows)


def get_company_executives(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_company_executives(code)
        check_ret(ret, data, ctx, "获取公司高管信息")

        row_count = len(data) if not is_empty(data) else 0

        if output_json:
            if is_empty(data):
                records = []
            else:
                records = df_to_records(data)
                for r in records:
                    r.pop("begin_date", None)
                    r.pop("issue_date", None)
                    for int_field in ("annual_salary",):
                        v = r.get(int_field)
                        if v is not None and isinstance(v, float) and not (v != v):  # not NaN
                            r[int_field] = int(v)
            out = {"code": code, "data": records}
            print(json.dumps(out, ensure_ascii=False))
        else:
            if is_empty(data):
                print("无数据")
            else:
                print(SEP64)
                print(f"公司高管信息  标的：{code}")
                print(DASH64)
                print_display_df(_build_display_df(data), max_colwidth=30)
                print(DASH64)
                print(f"返回条数：{row_count}")
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
    parser = argparse.ArgumentParser(description="获取公司高管信息")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_company_executives(args.code, output_json=args.output_json)
