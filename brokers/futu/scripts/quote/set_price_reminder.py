#!/usr/bin/env python3
"""
设置到价提醒

功能：为股票设置到价提醒
用法：python set_price_reminder.py HK.00700 --op ADD --type PRICE_UP --value 400

接口限制：
- 每 30 秒内最多请求 60 次
- 每只股票最多 10 个提醒

参数说明：
- op: ADD(新增), MODIFY(修改，需配合 --reminder-id), DEL(删除单条，需 --reminder-id),
      DEL_ALL(删除该股票全部), ENABLE(启用), DISABLE(禁用)
- reminder_type: PRICE_UP(升到), PRICE_DOWN(跌到), CHANGE_RATE_UP(日涨幅超),
                 CHANGE_RATE_DOWN(日跌幅超), BID_PRICE_UP(买一升到), ASK_PRICE_DOWN(卖一跌到),
                 TURNOVER_UP(成交量超), TURNOVER_RATE_UP(换手率超), VOLUME_UP(成交量超),
                 FIVE_MIN_CHANGE_RATE_UP/DOWN(5分钟涨/跌幅超), THREE_MIN_CHANGE_RATE_UP/DOWN(3分钟涨/跌幅超),
                 BID_VOL_UP(买一量超), ASK_VOL_UP(卖一量超)
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
    RET_OK,
)


def set_price_reminder(code, op, reminder_type=None, value=None, reminder_id=None, output_json=False):
    ctx = None
    try:
        from futu import SetPriceReminderOp, PriceReminderType

        op_map = {
            "ADD": SetPriceReminderOp.ADD,
            "MODIFY": SetPriceReminderOp.MODIFY,
            "DEL": SetPriceReminderOp.DEL,
            "DEL_ALL": SetPriceReminderOp.DEL_ALL,
            "ENABLE": SetPriceReminderOp.ENABLE,
            "DISABLE": SetPriceReminderOp.DISABLE,
        }
        op_enum = op_map.get(op.upper())
        if op_enum is None:
            raise ValueError(f"不支持的操作: {op}，可选: {list(op_map.keys())}")

        kwargs = {"code": code, "op": op_enum}
        if reminder_id is not None:
            kwargs["key"] = reminder_id
        if reminder_type:
            type_map = {
                "PRICE_UP": PriceReminderType.PRICE_UP,
                "PRICE_DOWN": PriceReminderType.PRICE_DOWN,
                "CHANGE_RATE_UP": PriceReminderType.CHANGE_RATE_UP,
                "CHANGE_RATE_DOWN": PriceReminderType.CHANGE_RATE_DOWN,
                "FIVE_MIN_CHANGE_RATE_UP": PriceReminderType.FIVE_MIN_CHANGE_RATE_UP,
                "FIVE_MIN_CHANGE_RATE_DOWN": PriceReminderType.FIVE_MIN_CHANGE_RATE_DOWN,
                "THREE_MIN_CHANGE_RATE_UP": PriceReminderType.THREE_MIN_CHANGE_RATE_UP,
                "THREE_MIN_CHANGE_RATE_DOWN": PriceReminderType.THREE_MIN_CHANGE_RATE_DOWN,
                "VOLUME_UP": PriceReminderType.VOLUME_UP,
                "TURNOVER_UP": PriceReminderType.TURNOVER_UP,
                "TURNOVER_RATE_UP": PriceReminderType.TURNOVER_RATE_UP,
                "BID_PRICE_UP": PriceReminderType.BID_PRICE_UP,
                "ASK_PRICE_DOWN": PriceReminderType.ASK_PRICE_DOWN,
                "BID_VOL_UP": PriceReminderType.BID_VOL_UP,
                "ASK_VOL_UP": PriceReminderType.ASK_VOL_UP,
            }
            t = type_map.get(reminder_type.upper())
            if t is None:
                raise ValueError(f"不支持的提醒类型: {reminder_type}")
            kwargs["reminder_type"] = t
        if value is not None:
            kwargs["reminder_freq"] = 0  # ALWAYS
            kwargs["value"] = value

        ctx = create_quote_context()
        ret, data = ctx.set_price_reminder(**kwargs)
        check_ret(ret, data, ctx, "设置到价提醒")

        if output_json:
            print(json.dumps({"result": str(data)}, ensure_ascii=False))
        else:
            print(f"设置到价提醒成功: {data}")

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="设置到价提醒")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument("--op", required=True,
                        choices=["ADD", "MODIFY", "DEL", "DEL_ALL", "ENABLE", "DISABLE"],
                        help="操作类型（ADD=新增，MODIFY=修改需 --reminder-id，DEL=删除单条需 --reminder-id，DEL_ALL=删除该股票全部）")
    parser.add_argument("--type", dest="reminder_type", default=None, help="提醒类型")
    parser.add_argument("--value", type=float, default=None, help="提醒值")
    parser.add_argument("--reminder-id", type=int, default=None, help="提醒 ID（修改/删除时使用）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    set_price_reminder(args.code, args.op, args.reminder_type, args.value, args.reminder_id, args.output_json)
