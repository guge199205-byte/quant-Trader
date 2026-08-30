#!/usr/bin/env python3
"""
获取期权异动告警设置（get_option_event_alert）

功能：查询当前账户已配置的期权异动提醒列表，支持分页获取。
用法：python get_option_event_alert.py [--count 50] [--json]

接口限制：
- count 范围 [1, 500]，默认 200
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def get_option_event_alert(count=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()

        kwargs = {}
        if count:
            kwargs["count"] = count

        all_rows = []
        page = None
        total_count = 0
        while True:
            if page:
                kwargs["page"] = page
            ret, data = ctx.get_option_event_alert(**kwargs)
            check_ret(ret, data, ctx, "获取期权异动告警设置")

            total_count = data.get("all_count", 0)
            alert_list = data.get("alert_list")
            if not is_empty(alert_list):
                all_rows.append(alert_list)
            next_page = data.get("next_page")
            if not next_page:
                break
            page = next_page

        if not all_rows:
            if output_json:
                print(json.dumps({"all_count": 0, "data": []}))
            else:
                print("无告警设置")
            return

        import pandas as pd
        df = pd.concat(all_rows, ignore_index=True)

        if output_json:
            print(json.dumps({
                "all_count": total_count,
                "data": df_to_records(df),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"期权异动告警设置 - 共 {total_count} 条")
            print("=" * 70)
            print(df.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取期权异动告警设置 (get_option_event_alert)")
    parser.add_argument("--count", type=int, help="每页数量 [1,500]")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_event_alert(args.count, args.output_json)
