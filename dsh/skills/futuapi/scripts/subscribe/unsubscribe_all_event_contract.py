#!/usr/bin/env python3
"""
取消当前连接所有预测市场订阅

功能：一键取消当前连接的所有预测市场订阅
用法：python unsubscribe_all_event_contract.py [--json]

接口：OpenQuoteContext.unsubscribe_all_event_contract()
返回：(ret, err_message)

接口限制：
- 订阅后至少 1 分钟才能反订阅
- 取消指定订阅用 unsubscribe_event_contract.py
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
    assert_event_contract_support,
)


def unsubscribe_all_event_contract(output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ret, err = ctx.unsubscribe_all_event_contract()
        check_ret(ret, err, ctx, "取消所有预测市场订阅")

        if output_json:
            print(json.dumps({"result": "ok"}, ensure_ascii=False))
        else:
            print("已取消当前连接所有预测市场订阅")

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="取消当前连接所有预测市场订阅")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    unsubscribe_all_event_contract(args.output_json)
