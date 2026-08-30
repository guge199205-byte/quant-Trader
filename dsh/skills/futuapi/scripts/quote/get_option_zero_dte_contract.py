#!/usr/bin/env python3
"""
获取末日期权合约列表（get_option_zero_dte_contract）

功能：获取末日期权合约列表，返回指定标的在指定行权日的 0DTE 期权合约详情，包含希腊值、盈亏平衡点及盈利概率等。
用法：
  python get_option_zero_dte_contract.py --owner US.TSLA --chain-info chain.json
  python get_option_zero_dte_contract.py --owner US.TSLA --chain-info chain.json --sort-type VOLUME --config filters.json

chain.json（来自 get_option_zero_dte_screener 返回的 chain_info）：
{
  "strike_date_timestamp": 1781755200,
  "product_code": "TSLA",
  "multiplier": 100.0,
  "contract_share_size": 100.0,
  "expiration_type": 1,
  "underlying": "US.TSLA"
}

filters.json 示例：
{
  "filters": [
    {"indicator_type": "OPTION_TYPE", "value_list": [1]},
    {"indicator_type": "DELTA", "interval_min": 0.3, "interval_max": 0.7}
  ]
}

接口限制：
- chain_info 必须来自 get_option_zero_dte_screener 的返回结果
- 支持 4 种排序字段 + 15 种筛选因子，无分页
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, df_to_records


def _resolve_sort_type(sort_str):
    if not sort_str:
        return None
    from futu import ZeroDteContractSortType
    key = str(sort_str).strip().upper()
    if hasattr(ZeroDteContractSortType, key):
        return getattr(ZeroDteContractSortType, key)
    raise ValueError(f"无效的 ZeroDteContractSortType: {sort_str}")


def _build_filters(spec):
    if not spec or not spec.get("filters"):
        return None
    from futu import ZeroDteContractIndicatorType
    from futu.quote.quote_option_event_info import ZeroDteContractFilter

    filters = []
    for f in spec["filters"]:
        indicator_str = f.get("indicator_type")
        if not indicator_str:
            raise ValueError("filter 配置缺少 indicator_type")
        indicator_type = getattr(ZeroDteContractIndicatorType, str(indicator_str).upper())

        kwargs = {"indicator_type": indicator_type}
        if "value_list" in f:
            kwargs["value_list"] = f["value_list"]
        if "interval_min" in f:
            kwargs["interval_min"] = f["interval_min"]
        if "interval_max" in f:
            kwargs["interval_max"] = f["interval_max"]
        if "min_inclusive" in f:
            kwargs["min_inclusive"] = f["min_inclusive"]
        if "max_inclusive" in f:
            kwargs["max_inclusive"] = f["max_inclusive"]

        filters.append(ZeroDteContractFilter(**kwargs))
    return filters


def get_option_zero_dte_contract(owner, chain_info_path, sort_type=None, is_asc=None,
                                  config_path=None, output_json=False):
    with open(chain_info_path, "r", encoding="utf-8") as fh:
        chain_info = json.load(fh)

    spec = {}
    if config_path:
        with open(config_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

    ctx = None
    try:
        ctx = create_quote_context()
        sort_t = _resolve_sort_type(sort_type)
        filter_list = _build_filters(spec)

        strike_date_timestamp = chain_info["strike_date_timestamp"]

        kwargs = {
            "owner": owner,
            "strike_date_timestamp": strike_date_timestamp,
            "chain_info": chain_info,
        }
        if sort_t is not None:
            kwargs["sort_type"] = sort_t
        if is_asc is not None:
            kwargs["is_asc"] = is_asc
        if filter_list:
            kwargs["filter_list"] = filter_list

        ret, data = ctx.get_option_zero_dte_contract(**kwargs)
        check_ret(ret, data, ctx, "获取末日期权合约列表")

        if is_empty(data):
            if output_json:
                print(json.dumps({"owner": owner, "data": []}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({
                "owner": owner,
                "count": len(data),
                "data": df_to_records(data),
            }, ensure_ascii=False, default=str))
        else:
            print("=" * 70)
            print(f"末日期权合约列表 - {owner} 共 {len(data)} 条")
            print("=" * 70)
            print(data.to_string(index=False))
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取末日期权合约列表 (get_option_zero_dte_contract)")
    parser.add_argument("--owner", required=True, help="标的股票代码，如 US.TSLA")
    parser.add_argument("--chain-info", required=True, help="chain_info JSON 文件路径")
    parser.add_argument("--sort-type", help="排序类型: VOLUME, OPEN_INTEREST, IV, DELTA")
    parser.add_argument("--asc", action="store_true", dest="is_asc", help="升序排列")
    parser.add_argument("--config", help="JSON 筛选配置文件路径")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_option_zero_dte_contract(args.owner, args.chain_info, args.sort_type,
                                  args.is_asc if args.is_asc else None, args.config, args.output_json)
