#!/usr/bin/env python3
"""
拉取预测市场历史 K 线

功能：拉取预测市场历史 K 线，无需先下载历史数据，也无需订阅对应 K 线类型；自动处理分页
用法：python request_history_event_contract_kline.py EC.KXNFLAFCCHAMP-27-CIN --start 2026-07-05 --end 2026-07-09 --pre-side YES --ktype K_DAY [--max-count 10] [--page-req-key KEY] [--json]

接口：OpenQuoteContext.request_history_event_contract_kline(code, start=None, end=None,
      pre_side=None, ktype=KLType.K_DAY, kline_source=None, max_count=1000, page_req_key=None)
返回：(ret, DataFrame, page_req_key)；字段 code / pre_side / name / time_key / open / high / low / close / volume

接口限制：
- ktype 仅支持 K_1M/K_5M/K_60M/K_DAY
- 历史 K 线无需订阅，走历史 K 线额度；单包最大 1000 根，max_count 超过时自动分页循环拉取（公开方法已封装循环）
- start/end 都不传时默认 end=当前，start=end 往前 365 天

start/end 组合：str+str / None+str / str+None / None+None
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
    KLType,
    ECKlineSource,
    assert_event_contract_support,
)


_KLTYPE_KLTYPE_MAP = {
    "K_1M": KLType.K_1M,
    "K_5M": KLType.K_5M,
    "K_60M": KLType.K_60M,
    "K_DAY": KLType.K_DAY,
}


def request_history_event_contract_kline(code, start=None, end=None, ktype="K_DAY",
                                         pre_side=None, kline_source=None, max_count=1000,
                                         page_req_key=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        ktype_key = str(ktype).upper()
        if ktype_key not in _KLTYPE_KLTYPE_MAP:
            raise ValueError(f"ktype 仅支持 {EC_KLTYPE_CHOICES}，收到: {ktype}")
        kl_type = _KLTYPE_KLTYPE_MAP[ktype_key]

        pre_side_enum = parse_pred_side(pre_side)
        kline_source_enum = parse_ec_kline_source(kline_source)

        # 历史 K 线无需订阅，直接拉取。
        ret, data, next_page_req_key = ctx.request_history_event_contract_kline(
            code, start=start, end=end, pre_side=pre_side_enum, ktype=kl_type,
            kline_source=kline_source_enum, max_count=max_count, page_req_key=page_req_key)
        check_ret(ret, data, ctx, "拉取预测市场历史 K 线")

        records = [] if is_empty(data) else df_to_records(data)

        if output_json:
            print(json.dumps({
                "data": records,
                "page_req_key": next_page_req_key or "",
            }, ensure_ascii=False))
        else:
            print("=" * 70)
            print(f"预测市场历史 K 线 - {code} ({ktype_key})")
            print(f"区间: {start or '(默认)'} ~ {end or '(默认)'}")
            print("=" * 70)
            if records:
                cols = [c for c in ['code', 'pre_side', 'name', 'time_key', 'open',
                                    'high', 'low', 'close', 'volume']
                        if c in data.columns]
                print_display_df(data[cols], max_colwidth=30)
                print(f"\n共 {len(data)} 根 K 线")
            else:
                print("无数据")
            print(f"page_req_key: {next_page_req_key or '(无更多)'}")
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
    parser = argparse.ArgumentParser(description="拉取预测市场历史 K 线（无需订阅）")
    parser.add_argument("code", help="预测市场合约代码")
    parser.add_argument("--start", default=None, help="开始时间，如 2025-06-20")
    parser.add_argument("--end", default=None, help="结束时间，如 2025-07-20")
    parser.add_argument("--ktype", choices=EC_KLTYPE_CHOICES, default="K_DAY",
                        help="K 线类型（仅支持 K_1M/K_5M/K_60M/K_DAY，默认 K_DAY）")
    parser.add_argument("--pre-side", choices=["YES", "NO"], default=None, help="合约方向")
    parser.add_argument("--kline-source", choices=["ORDER_BOOK_YES"], default=None,
                        help="K 线来源 ORDER_BOOK_YES；优先级高于 pre-side")
    parser.add_argument("--max-count", type=int, default=1000,
                        help="本次最大返回数据点个数，None 表示全部")
    parser.add_argument("--page-req-key", default=None, help="分页续拉 key，初始不传")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    request_history_event_contract_kline(
        code=args.code, start=args.start, end=args.end, ktype=args.ktype,
        pre_side=args.pre_side, kline_source=args.kline_source, max_count=args.max_count,
        page_req_key=args.page_req_key,
        output_json=args.output_json)
