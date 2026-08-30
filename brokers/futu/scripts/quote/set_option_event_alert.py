#!/usr/bin/env python3
"""
修改期权异动告警条件（set_option_event_alert）

功能：新增、删除、修改、启用、禁用期权异动提醒。
用法：
  python set_option_event_alert.py --op ADD --config alert.json
  python set_option_event_alert.py --op DELETE --key 14694
  python set_option_event_alert.py --op ENABLE --key 14694
  python set_option_event_alert.py --op DISABLE --key 14694
  python set_option_event_alert.py --op DELETE_ALL

JSON 配置示例（alert.json，用于 ADD / MODIFY）：
{
  "option_market": "US_SECURITY",
  "option_type": "CALL",
  "order_type_list": ["SWEEP"],
  "size_range_min": 100,
  "size_min_inclusive": false,
  "note": "大单扫货"
}

监控范围（三选一）：option_market / watchlist_group_name / underlying
开闭区间参数（每个范围独立设置，默认 true 闭区间）：
  market_cap_min_inclusive / market_cap_max_inclusive
  expiry_days_min_inclusive / expiry_days_max_inclusive
  price_min_inclusive / price_max_inclusive
  size_min_inclusive / size_max_inclusive
  premium_min_inclusive / premium_max_inclusive
  iv_min_inclusive / iv_max_inclusive
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close


def _resolve_enum(module, cls_name, value):
    if value is None:
        return None
    cls = getattr(module, cls_name, None)
    if cls is None:
        return value
    if isinstance(value, str) and hasattr(cls, value.upper()):
        return getattr(cls, value.upper())
    return value


def _build_alert_item(spec, key=None):
    import futu
    from futu import OptionEventAlertItem

    kwargs = {}
    if key is not None:
        kwargs["key"] = key
    if spec.get("key"):
        kwargs["key"] = int(spec["key"])
    if "enable" in spec:
        kwargs["enable"] = spec["enable"]
    if "option_market" in spec:
        kwargs["option_market"] = _resolve_enum(futu, "OptionMarket", spec["option_market"])
    if "watchlist_group_name" in spec:
        kwargs["watchlist_group_name"] = spec["watchlist_group_name"]
    if "underlying" in spec:
        kwargs["underlying"] = spec["underlying"]
    if "option_type" in spec:
        kwargs["option_type"] = _resolve_enum(futu, "OptionType", spec["option_type"])
    if "side_type_list" in spec:
        kwargs["side_type_list"] = [_resolve_enum(futu, "EventTickerType", v) for v in spec["side_type_list"]]
    if "order_type_list" in spec:
        kwargs["order_type_list"] = [_resolve_enum(futu, "AlertOrderType", v) for v in spec["order_type_list"]]
    for field in ("market_cap_range_min", "market_cap_range_max",
                  "market_cap_min_inclusive", "market_cap_max_inclusive",
                  "expiry_days_range_min", "expiry_days_range_max",
                  "expiry_days_min_inclusive", "expiry_days_max_inclusive",
                  "price_range_min", "price_range_max",
                  "price_min_inclusive", "price_max_inclusive",
                  "size_range_min", "size_range_max",
                  "size_min_inclusive", "size_max_inclusive",
                  "premium_range_min", "premium_range_max",
                  "premium_min_inclusive", "premium_max_inclusive",
                  "iv_range_min", "iv_range_max",
                  "iv_min_inclusive", "iv_max_inclusive"):
        if field in spec:
            kwargs[field] = spec[field]
    if "earnings_date_begin" in spec:
        kwargs["earnings_date_begin"] = spec["earnings_date_begin"]
    if "earnings_date_end" in spec:
        kwargs["earnings_date_end"] = spec["earnings_date_end"]
    if "note" in spec:
        kwargs["note"] = spec["note"]

    return OptionEventAlertItem(**kwargs)


def set_option_event_alert(op, key=None, config_path=None, output_json=False):
    import futu
    from futu import AlertOpType

    op_type = _resolve_enum(futu, "AlertOpType", op)

    alert_list = None
    if op_type != AlertOpType.DELETE_ALL:
        if config_path:
            with open(config_path, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
        elif key is not None:
            spec = {"key": key}
        else:
            print("错误: 除 DELETE_ALL 外，需要 --key 或 --config 参数")
            sys.exit(1)
        alert_list = _build_alert_item(spec, key)

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.set_option_event_alert(op=op_type, alert_list=alert_list)
        check_ret(ret, data, ctx, "修改期权异动告警条件")

        if output_json:
            print(json.dumps({"op": op, "result": "success"}, ensure_ascii=False))
        else:
            print(f"操作成功: {op}")
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="修改期权异动告警条件 (set_option_event_alert)")
    parser.add_argument("--op", required=True,
                        help="操作类型: ADD, DELETE, MODIFY, ENABLE, DISABLE, DELETE_ALL")
    parser.add_argument("--key", type=int, help="告警唯一标识（DELETE/MODIFY/ENABLE/DISABLE 时使用）")
    parser.add_argument("--config", help="JSON 配置文件路径（ADD/MODIFY 时使用）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    set_option_event_alert(args.op, args.key, args.config, args.output_json)
