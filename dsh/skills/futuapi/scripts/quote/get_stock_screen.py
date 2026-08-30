#!/usr/bin/env python3
"""
筛选正股 V2（get_stock_screen）— 协议号 3252

返回三元组：data = (last_page, all_count, items)
  - items 为 list[dict]，每条 dict 字段名取自 enum 名（如 'PRICE'/'MARKET_CAP'）
  - 不是 DataFrame

接口限制：
- 港股 BMP 权限不支持
- 每页最多 200，分页用 page_from / page_count
- 港股仅 Q1 + ANNUAL，Q2/Q3/Q4 财务数据通常缺失
- Term.SURPRISE_LATEST(200~204) HK/US 当前与 ANNUAL 一致，慎用

数值单位：所有 lower/upper 直接传原始值，OpenD 负责倍率换算。
  例如 PRICE 传 10.0 = 10 元；MARKET_CAP 传 1e10 = 100 亿；涨跌幅 5% 传 5.0（不是 0.05）

JSON 配置（完整支持枚举名 / 数字双向）：
{
  "filters": [
    {"type": "simple_field", "field": "MARKET", "values": ["HK"]},
    {"type": "plate", "plate_ids": ["BK1001"], "parent_plate_id": null},
    {"type": "simple_property", "name": "PRICE", "lower": 10.0, "upper": 100.0},
    {"type": "simple_property", "name": "MARKET_CAP", "lower": 10000000000.0},
    {"type": "cumulative_property", "name": "PRICE_CHANGE_PCT", "days": 5, "lower": 5.0},
    {"type": "financial_property", "name": "NET_PROFIT", "term": "ANNUAL", "lower": 100000000.0},
    {"type": "indicator_positional",
        "first_indicator_name": "MA5", "period_type": "DAY",
        "position": "CROSS_UP", "second_indicator": "MA20"},
    {"type": "indicator_pattern", "name": "MACD_GOLD_CROSS", "period_type": "DAY"},
    {"type": "featured_property", "name": "CHIPS_PROFIT_RATIO",
        "intervals": [{"filterMin": {"value": 50.0, "includes": true},
                       "filterMax": {"value": 100.0, "includes": true}}]},
    {"type": "broker_holdings", "name": "CONCENTRATED_DISTRIBUTION",
        "days": 30, "param": "10",
        "intervals": [{"filterMin": {"value": 50.0, "includes": true}}]},
    {"type": "kline_shape", "name": "SHAPE_TYPE", "period": "DAY",
        "value_set": ["DOUBLE_BOTTOMS", "HEAD_SHOULDERS_BOTTOM"]},
    {"type": "option", "name": "STOCK_IV", "period": "HV_30D",
        "intervals": [{"filterMin": {"value": 20.0, "includes": true},
                       "filterMax": {"value": 100.0, "includes": true}}]}
  ],
  "retrieves": [
    {"type": "basic",  "name": "CODE"},
    {"type": "basic",  "name": "NAME"},
    {"type": "simple", "name": "PRICE"},
    {"type": "simple", "name": "MARKET_CAP"},
    {"type": "cumulative", "name": "PRICE_CHANGE_PCT", "days": 5},
    {"type": "financial",  "name": "NET_PROFIT", "term": "ANNUAL"},
    {"type": "kline_shape","name": "SHAPE_TYPE", "period": "DAY"}
  ],
  "sort":  {"direction": "DESC", "property_type": "simple",
            "property_params": {"name": "MARKET_CAP"}},
  "sorts": [{"direction": "ASC",  "property_type": "simple",
             "property_params": {"name": "PE_TTM"}}]
}

用法：
  python get_stock_screen.py --config config.json [--page-from 0] [--page-count 200] [--json]
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close


def _resolve(enum_cls, value):
    """将 JSON 中的字符串/整数转换为 IntEnum 整数值"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and enum_cls is not None and hasattr(enum_cls, value):
        return int(getattr(enum_cls, value))
    return value


def _resolve_property_params(params, enum_cls):
    if not params:
        return params
    out = dict(params)
    if "name" in out:
        out["name"] = int(_resolve(enum_cls, out["name"]))
    return out


def _build_request(spec, page_from, page_count):
    try:
        from futu.quote.stock_screen_const import (
            ScrMarket, ScrSortDir, SimpleField, SimpleProperty,
            CumulativeProperty, FinancialProperty, Term,
            Indicator, Period, Position, Pattern,
            FeaturedProperty, BrokerProperty,
            KlineShapeProperty, KlineShapeType,
            OptionProperty, OptionHVPeriod, BasicProperty,
        )
        from futu import StockScreenRequest
    except ImportError as e:
        print(f"错误: 当前 futu-api 未提供 StockScreenRequest 或相关枚举（{e}）。请升级 SDK。")
        sys.exit(1)

    SIMPLE_FIELD_VALUE_ENUM = {
        SimpleField.MARKET: ScrMarket,
    }
    PROPERTY_TYPE_NAME_ENUM = {
        "simple": SimpleProperty,
        "cumulative": CumulativeProperty,
        "financial": FinancialProperty,
        "basic": BasicProperty,
        "featured": FeaturedProperty,
        "broker": BrokerProperty,
        "klineShape": KlineShapeProperty,
        "option": OptionProperty,
    }

    req = StockScreenRequest()
    req.page_from = page_from
    req.page_count = page_count

    for f in spec.get("filters") or []:
        ftype = f.get("type")
        if ftype == "simple_field":
            field_int = _resolve(SimpleField, f["field"])
            value_enum = SIMPLE_FIELD_VALUE_ENUM.get(SimpleField(field_int)) if isinstance(field_int, int) else None
            values = [_resolve(value_enum, v) for v in (f.get("values") or [])]
            req.add_simple_field(field=field_int, values=[int(v) for v in values])
        elif ftype == "plate":
            req.add_plate(plate_ids=f.get("plate_ids") or [],
                          parent_plate_id=f.get("parent_plate_id"))
        elif ftype == "simple_property":
            req.add_simple_property(
                name=_resolve(SimpleProperty, f["name"]),
                lower=f.get("lower"), upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
                unit=f.get("unit"),
            )
        elif ftype == "cumulative_property":
            req.add_cumulative_property(
                name=_resolve(CumulativeProperty, f["name"]),
                days=f.get("days", 1),
                lower=f.get("lower"), upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
                continuous_period=f.get("continuous_period"),
                unit=f.get("unit"),
            )
        elif ftype == "financial_property":
            req.add_financial_property(
                name=_resolve(FinancialProperty, f["name"]),
                term=_resolve(Term, f.get("term")),
                year=f.get("year"),
                lower=f.get("lower"), upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
                duration=f.get("duration"),
                continuous_period=f.get("continuous_period"),
                period_average=f.get("period_average"),
                future_duration=f.get("future_duration"),
                unit=f.get("unit"),
            )
        elif ftype == "indicator_positional":
            req.add_indicator_positional(
                first_indicator_name=_resolve(Indicator, f["first_indicator_name"]),
                period_type=_resolve(Period, f["period_type"]),
                position=_resolve(Position, f["position"]),
                second_indicator=_resolve(Indicator, f.get("second_indicator")),
                second_value=f.get("second_value"),
                first_indicator_params=f.get("first_indicator_params"),
                second_indicator_params=f.get("second_indicator_params"),
                continuous_period=f.get("continuous_period"),
                intervals=f.get("intervals"),
            )
        elif ftype == "indicator_pattern":
            req.add_indicator_pattern(
                name=_resolve(Pattern, f["name"]),
                period_type=_resolve(Period, f["period_type"]),
                continuous_period=f.get("continuous_period"),
                is_matching=f.get("is_matching"),
                sub_patterns=f.get("sub_patterns"),
            )
        elif ftype == "featured_property":
            req.add_featured_property(
                name=_resolve(FeaturedProperty, f["name"]),
                intervals=f.get("intervals"),
                value_set=f.get("value_set"),
                period=f.get("period"),
                range_period=f.get("range_period"),
                first_custom_param=f.get("first_custom_param"),
            )
        elif ftype == "broker_holdings":
            req.add_broker_holdings(
                name=_resolve(BrokerProperty, f["name"]),
                days=f.get("days"),
                param=f.get("param"),
                intervals=f.get("intervals"),
            )
        elif ftype == "kline_shape":
            value_set = [_resolve(KlineShapeType, v) for v in (f.get("value_set") or [])]
            req.add_kline_shape(
                name=_resolve(KlineShapeProperty, f["name"]),
                period=_resolve(Period, f.get("period")),
                value_set=[int(v) for v in value_set] if value_set else None,
            )
        elif ftype == "option":
            req.add_option(
                name=_resolve(OptionProperty, f["name"]),
                intervals=f.get("intervals"),
                param=f.get("param"),
                period=_resolve(OptionHVPeriod, f.get("period")),
            )
        else:
            raise ValueError(f"未知 filter type: {ftype}")

    for r in spec.get("retrieves") or []:
        rtype = r.get("type")
        if rtype == "basic":
            req.add_retrieve_basic(name=_resolve(BasicProperty, r["name"]))
        elif rtype == "simple":
            req.add_retrieve_simple(name=_resolve(SimpleProperty, r["name"]))
        elif rtype == "cumulative":
            req.add_retrieve_cumulative(
                name=_resolve(CumulativeProperty, r["name"]),
                days=r.get("days", 1),
                period_average=r.get("period_average"),
            )
        elif rtype == "financial":
            req.add_retrieve_financial(
                name=_resolve(FinancialProperty, r["name"]),
                term=_resolve(Term, r.get("term")),
                year=r.get("year"),
                duration=r.get("duration"),
                period_average=r.get("period_average"),
                future_duration=r.get("future_duration"),
            )
        elif rtype == "indicator":
            req.add_retrieve_indicator(
                name=_resolve(Indicator, r["name"]),
                period=_resolve(Period, r.get("period")),
                indicator_params=r.get("indicator_params"),
            )
        elif rtype == "featured":
            req.add_retrieve_featured(
                name=_resolve(FeaturedProperty, r["name"]),
                period=r.get("period"),
                range_period=r.get("range_period"),
                first_custom_param=r.get("first_custom_param"),
            )
        elif rtype == "broker":
            req.add_retrieve_broker(
                name=_resolve(BrokerProperty, r["name"]),
                days=r.get("days"),
                param=r.get("param"),
            )
        elif rtype == "option":
            req.add_retrieve_option(
                name=_resolve(OptionProperty, r["name"]),
                param=r.get("param"),
                period=_resolve(OptionHVPeriod, r.get("period")),
            )
        elif rtype == "kline_shape":
            req.add_retrieve_kline_shape(
                name=_resolve(KlineShapeProperty, r["name"]),
                period=_resolve(Period, r.get("period")),
            )
        else:
            raise ValueError(f"未知 retrieve type: {rtype}")

    sort = spec.get("sort")
    if sort:
        ptype = sort["property_type"]
        req.set_sort(
            direction=_resolve(ScrSortDir, sort["direction"]),
            property_type=ptype,
            property_params=_resolve_property_params(
                sort.get("property_params"), PROPERTY_TYPE_NAME_ENUM.get(ptype)),
        )
    for s in spec.get("sorts") or []:
        ptype = s["property_type"]
        req.add_sort(
            direction=_resolve(ScrSortDir, s["direction"]),
            property_type=ptype,
            property_params=_resolve_property_params(
                s.get("property_params"), PROPERTY_TYPE_NAME_ENUM.get(ptype)),
        )
    return req


def get_stock_screen(config_path, page_from=0, page_count=200, output_json=False):
    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        if not hasattr(ctx, "get_stock_screen"):
            print("错误: 当前 OpenD/SDK 未提供 get_stock_screen，请升级到支持该接口的版本")
            sys.exit(1)

        req = _build_request(spec, page_from, page_count)
        ret, data = ctx.get_stock_screen(req)
        check_ret(ret, data, ctx, "筛选正股 V2")

        last_page, all_count, items = data
        if output_json:
            print(json.dumps({
                "last_page": bool(last_page),
                "all_count": int(all_count),
                "data": items or [],
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"筛选正股 V2 结果 - 共 {all_count} 条 (last_page={last_page})")
            print("=" * 70)
            for i, item in enumerate(items or [], start=1):
                print(f"[{i}] {item}")
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="筛选正股 V2 (get_stock_screen)")
    parser.add_argument("--config", help="JSON 配置文件路径（filters/retrieves/sort/sorts）")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-count", type=int, default=200, help="每页最多 200")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_stock_screen(args.config, args.page_from, args.page_count, args.output_json)
