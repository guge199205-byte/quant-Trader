#!/usr/bin/env python3
"""
获取预测市场实时 K 线

功能：获取预测市场实时 K 线（合约级成交价 K 线或 YES 子合约摆盘 K 线），需先订阅对应 K 线类型
用法：python get_event_contract_kline.py EC.KXODIMATCH-26JUL140600INDENG-IND --ktype K_DAY --pre-side YES [--kline-source ORDER_BOOK_YES] [--max-count 10] [--no-auto-subscribe] [--json]

接口：OpenQuoteContext.get_event_contract_kline(code, pre_side=None, ktype=KLType.K_DAY,
      kline_source=None, max_count=1000)
返回：DataFrame；字段 code / pre_side / name / time_key / open / high / low / close / volume

接口限制：
- ktype 仅支持 K_1M/K_5M/K_60M/K_DAY，其余报错
- 查询前必须先订阅对应 K 线类型，否则报错"请求获取事件合约K线接口前，请先订阅KL_Day数据"
- kline_source 优先级高于 pre_side；指定 kline_source 后 pre_side 由后台解析
- pre_side 仅 kline_source=None（合约级 K 线）时需指定方向
- 脚本默认自动订阅；已订阅场景用 --no-auto-subscribe 跳过
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
    parse_pred_side,
    parse_ec_kline_source,
    EC_KLTYPE_CHOICES,
    SubType,
    KLType,
    ECKlineSource,
    assert_event_contract_support,
    ensure_event_contract_subscribed,
)


# K 线类型字符串 -> SubType（预测市场仅支持这 4 种）
_KLTYPE_SUBTYPE_MAP = {
    "K_1M": SubType.K_1M,
    "K_5M": SubType.K_5M,
    "K_60M": SubType.K_60M,
    "K_DAY": SubType.K_DAY,
}

_KLTYPE_KLTYPE_MAP = {
    "K_1M": KLType.K_1M,
    "K_5M": KLType.K_5M,
    "K_60M": KLType.K_60M,
    "K_DAY": KLType.K_DAY,
}


def get_event_contract_kline(code, ktype="K_DAY", pre_side=None, kline_source=None,
                             max_count=1000, auto_subscribe=True, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ktype_key = str(ktype).upper()
        if ktype_key not in _KLTYPE_KLTYPE_MAP:
            raise ValueError(f"ktype 仅支持 {EC_KLTYPE_CHOICES}，收到: {ktype}")
        kl_type = _KLTYPE_KLTYPE_MAP[ktype_key]
        sub_type = _KLTYPE_SUBTYPE_MAP[ktype_key]

        pre_side_enum = parse_pred_side(pre_side)
        kline_source_enum = parse_ec_kline_source(kline_source)

        # 查询前自动订阅对应 K 线类型（已订阅静默跳过，其他失败退出）。
        # 订阅时透传 kline_source_list，与查询的 kline_source 保持一致，
        # 否则订阅的是合约级 K 线、查询 YES 摆盘 K 线会因来源不匹配而失败。
        if auto_subscribe:
            src_list = [kline_source_enum] if kline_source_enum is not None else None
            ensure_event_contract_subscribed(ctx, code, sub_type,
                                             output_json=output_json,
                                             kline_source_list=src_list,
                                             action="订阅预测市场 K 线")

        ret, data = ctx.get_event_contract_kline(
            code, pre_side=pre_side_enum, ktype=kl_type,
            kline_source=kline_source_enum, max_count=max_count)
        check_ret(ret, data, ctx, "获取预测市场 K 线")

        if is_empty(data):
            if output_json:
                print(json.dumps({"data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"data": df_to_records(data)}, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"预测市场 K 线 - {code} ({ktype_key})")
            print("=" * 70)
            cols = [c for c in ['code', 'pre_side', 'name', 'time_key', 'open',
                                'high', 'low', 'close', 'volume']
                    if c in data.columns]
            print_display_df(data[cols], max_colwidth=30)
            print(f"\n共 {len(data)} 根 K 线")
            print("=" * 70)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取预测市场实时 K 线（需先订阅对应 K 线类型）")
    parser.add_argument("code", help="预测市场合约代码，如 EC.KXODIMATCH-26JUL140600INDENG-IND")
    parser.add_argument("--ktype", choices=EC_KLTYPE_CHOICES, default="K_DAY",
                        help="K 线类型（仅支持 K_1M/K_5M/K_60M/K_DAY，默认 K_DAY）")
    parser.add_argument("--pre-side", choices=["YES", "NO"], default=None,
                        help="合约方向（kline_source 为合约级 K 线时需指定）")
    parser.add_argument("--kline-source", choices=["ORDER_BOOK_YES"], default=None,
                        help="K 线来源 ORDER_BOOK_YES（YES 子合约摆盘 K 线）；优先级高于 pre-side")
    parser.add_argument("--max-count", type=int, default=1000, help="最大返回条数，上限 1000")
    parser.add_argument("--no-auto-subscribe", action="store_true",
                        help="不自动订阅（适用于已订阅场景）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_event_contract_kline(
        code=args.code, ktype=args.ktype, pre_side=args.pre_side,
        kline_source=args.kline_source, max_count=args.max_count,
        auto_subscribe=not args.no_auto_subscribe, output_json=args.output_json)
