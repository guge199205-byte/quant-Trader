#!/usr/bin/env python3
"""
获取公司详情

功能：获取指定股票的公司详情标签列表，包含各类文本、链接和章节标题信息
用法：python get_company_profile.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- data[]:    公司详情标签列表，每项含 name（标签名）/value（对应信息）/field_type（Qot_Common.CompanyProfileFieldType）
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from unicodedata import east_asian_width as _eaw

from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
    df_to_records,
)

import pandas as pd

SEP = "=" * 64
SEP2 = "-" * 64

# FieldType 枚举翻译（值来自对外 proto FieldType 枚举定义）
_FIELD_TYPE_MAP = {
    0: "文本",
    1: "链接",
    2: "独立标题",
}


def _translate_field_type(val):
    """将 field_type 整型值翻译为中文描述（展示型枚举）"""
    try:
        v = int(val)
    except (TypeError, ValueError):
        return str(val)
    return _FIELD_TYPE_MAP.get(v, "-")


def _cw(c):
    return 2 if _eaw(c) in ("F", "W") else 1


def _dw(s):
    return sum(_cw(c) for c in str(s))


def _trunc(s, max_w):
    s = str(s)
    w, out = 0, []
    for c in s:
        cw = _cw(c)
        if w + cw > max_w:
            while out and w + 3 > max_w:
                w -= _cw(out.pop())
            return "".join(out) + "..."
        out.append(c)
        w += cw
    return s


def _pad(s, w, align="left"):
    s = str(s)
    p = max(0, w - _dw(s))
    return (" " * p + s) if align == "right" else (s + " " * p)


_W_NAME = 22  # "总办事处及主要营业地点" = 11 CJK × 2
_W_VAL = 50   # 内容展示宽度上限


def _print_profile_table(df):
    print(_pad("标签名", _W_NAME, "right") + "  " + _pad("内容", _W_VAL) + "  类型")
    for _, row in df.iterrows():
        name = _trunc(row["标签名"], _W_NAME)
        val = str(row["内容"])
        ftype = str(row["类型"])
        if ftype == "独立标题":
            print()
            print(row["标签名"])
            print(val)
        else:
            print(_pad(name, _W_NAME, "right") + "  " + _pad(_trunc(val, _W_VAL), _W_VAL) + "  " + ftype)


def get_company_profile(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_company_profile(code)
        check_ret(ret, data, ctx, "获取公司详情")

        row_count = 0 if is_empty(data) else len(data)

        if output_json:
            if is_empty(data):
                print(json.dumps({"code": code, "data": {}}, ensure_ascii=False))
            else:
                print(json.dumps({"code": code, "data": df_to_records(data)}, ensure_ascii=False))
            return

        # 非 JSON 路径
        if is_empty(data):
            print("无数据")
        else:
            print(SEP)
            print(f"公司详情  标的：{code}")
            print(SEP2)
            disp = data.copy()
            if "field_type" in disp.columns:
                disp["field_type"] = disp["field_type"].apply(_translate_field_type)
            disp = disp.rename(columns={
                "name":       "标签名",
                "value":      "内容",
                "field_type": "类型",
            })
            _print_profile_table(disp)
            print(SEP2)
            print(f"返回条数：{row_count}")
            print(SEP)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取公司概况")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_company_profile(args.code, output_json=args.output_json)
