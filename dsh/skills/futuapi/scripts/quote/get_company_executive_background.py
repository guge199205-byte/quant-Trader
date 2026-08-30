#!/usr/bin/env python3
"""
获取公司高管背景

功能：获取指定股票某位高管的背景介绍
用法：python get_company_executive_background.py [-h] [--json] code leader_name

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- leader_name: 高管姓名，使用 get_company_executives.py 返回的 leader_name 字段值；支持直接传中文（如 "张三"）或 Unicode 转义序列（如 "\u5f20\u4e09"），两种方式等价

返回字段说明：
- data.leader_name:      高管姓名（与请求参数一致）
- data.brief_background: 高管背景简介（文本）
"""
import argparse
import json
import sys
import os as _os
import textwrap

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    check_ret,
    safe_close,
    is_empty,
)

SEP64 = "=" * 64
DASH64 = "-" * 64


def get_company_executive_background(code, leader_name, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_company_executive_background(code, leader_name=leader_name)
        check_ret(ret, data, ctx, "获取公司高管背景")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        bg = data.get("brief_background", "") if isinstance(data, dict) else ""

        if not bg:
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({
                "code": code,
                "data": {"leader_name": leader_name, "brief_background": bg},
            }, ensure_ascii=False))
        else:
            print(SEP64)
            print(f"公司高管背景  标的：{code}")
            print(DASH64)
            wrapped = textwrap.fill(bg, width=60)
            print(f"高管背景简介：\n{wrapped}")
            print(DASH64)
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
    parser = argparse.ArgumentParser(description="获取高管背景简介")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("leader_name",
                        help="高管姓名，使用 get_company_executives.py 返回的 leader_name 字段值")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    if args.leader_name:
        try:
            args.leader_name = args.leader_name.encode('raw_unicode_escape').decode('unicode_escape')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    get_company_executive_background(args.code, leader_name=args.leader_name, output_json=args.output_json)
