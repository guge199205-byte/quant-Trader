#!/usr/bin/env python3
"""
Combo 询价

功能：提交用户组合的合约腿（ComboLeg 列表）与 MVC 标的，获取组合报价（bid/ask）与报价 ID（下单用），无需订阅
用法：python request_combo_quotes.py '[{"code":"EC.xxx-FRA","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"},{"code":"EC.xxx-ENG","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"}]' --mvc KALSHI.KXMVECROSSCATEGORY-R [--json]

接口：OpenQuoteContext.request_combo_quotes(combo_leg_list, mvc)
返回：(ret, dict)；dict 含 combo_leg_list(回显腿) / bid_price / ask_price / quote_id / should_retry

腿 JSON 字段：
- code: str，必填，合约代码 EC.xxx
- trd_side: BUY/SELL/SELL_SHORT/BUY_BACK，必填
- qty_ratio: 数量比例，必填
- pred_side: YES/NO，必填（缺省报错"事件合约组合缺少必要参数预测方向"）
- position_id: 选填，持仓 ID（仅 moomoo JP 平仓时使用）

备注：
- mvc 必须从 get_valid_combo_list.py 返回值透传，不可自行构造
- 组合腿至少 2 条，可来自不同 event（跨赛事组合）
- bid_price/should_retry 无对应报价时为 N/A，非错误
- quote_id 有时效性，过期后下单失败需重新询价；Combo 下单用 place_combo_order.py 传 quote_id
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
    parse_pred_side,
    format_enum,
    to_jsonable,
    ComboLeg,
    TrdSide,
    assert_event_contract_support,
)


def _parse_combo_trd_side(side_str):
    """解析组合腿交易方向 -> TrdSide（组合腿支持 BUY/SELL/SELL_SHORT/BUY_BACK）"""
    if not side_str:
        raise ValueError("trd_side 不能为空")
    key = str(side_str).strip().upper()
    if not hasattr(TrdSide, key):
        raise ValueError(f"无效的交易方向: {side_str}，必须为 BUY/SELL/SELL_SHORT/BUY_BACK")
    return getattr(TrdSide, key)


def _parse_combo_legs(legs_json):
    """解析腿 JSON 列表 -> ComboLeg 对象列表"""
    assert_event_contract_support()
    try:
        items = json.loads(legs_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"组合腿 JSON 解析失败: {e}")
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("组合腿必须是非空 JSON 数组")
    if len(items) < 2:
        raise ValueError("事件合约组合至少需要包含两条腿")

    legs = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 条组合腿必须是对象")
        code = str(item.get("code", "")).strip()
        if not code:
            raise ValueError(f"第 {idx} 条组合腿缺少 code")
        trd_side = item.get("trd_side")
        if trd_side is None:
            raise ValueError(f"第 {idx} 条组合腿缺少 trd_side")
        qty_ratio = item.get("qty_ratio")
        if qty_ratio is None:
            raise ValueError(f"第 {idx} 条组合腿缺少 qty_ratio")
        pred_side = item.get("pred_side")
        if pred_side is None or str(pred_side).strip() == "":
            raise ValueError(f"第 {idx} 条组合腿缺少 pred_side（YES/NO 必填）")

        leg = ComboLeg()
        leg.code = code
        leg.trd_side = _parse_combo_trd_side(str(trd_side))
        leg.qty_ratio = float(qty_ratio)
        leg.pred_side = parse_pred_side(str(pred_side))
        if "position_id" in item and item["position_id"] not in (None, ""):
            leg.position_id = int(item["position_id"])
        legs.append(leg)
    return legs


def _leg_to_dict(leg):
    """ComboLeg 对象 -> 可 JSON 序列化的 dict"""
    d = {
        "code": getattr(leg, "code", None),
        "trd_side": format_enum(getattr(leg, "trd_side", None)),
        "qty_ratio": to_jsonable(getattr(leg, "qty_ratio", None)),
        "pred_side": format_enum(getattr(leg, "pred_side", None)),
    }
    pos_id = getattr(leg, "position_id", None)
    if pos_id is not None:
        d["position_id"] = pos_id
    return d


def request_combo_quotes(legs_json, mvc, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        assert_event_contract_support(ctx, output_json=output_json)

        if not mvc or not isinstance(mvc, str):
            raise ValueError("mvc 必填，需从 get_valid_combo_list.py 返回值透传")

        legs = _parse_combo_legs(legs_json)

        ret, data = ctx.request_combo_quotes(legs, mvc)
        check_ret(ret, data, ctx, "Combo 询价")

        leg_list = data.get("combo_leg_list", []) if isinstance(data, dict) else []
        result = {
            "combo_leg_list": [_leg_to_dict(l) for l in leg_list],
            "bid_price": to_jsonable(data.get("bid_price")),
            "ask_price": to_jsonable(data.get("ask_price")),
            "quote_id": to_jsonable(data.get("quote_id")),
            "should_retry": to_jsonable(data.get("should_retry")),
        }

        if output_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("=" * 70)
            print("Combo 询价结果")
            print("=" * 70)
            print(f"  YES 买价 (bid): {result['bid_price']}")
            print(f"  YES 卖价 (ask): {result['ask_price']}")
            print(f"  报价 ID (quote_id): {result['quote_id']}")
            print(f"  建议重试 (should_retry): {result['should_retry']}")
            print(f"\n  组合腿 ({len(result['combo_leg_list'])} 条):")
            for leg in result["combo_leg_list"]:
                print(f"    {leg['code']}  {leg['trd_side']}  ratio={leg['qty_ratio']}  pred={leg['pred_side']}")
            print("\n  提示: quote_id 有时效性，下单需尽快使用 place_combo_order.py")
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
    parser = argparse.ArgumentParser(description="Combo 询价（无需订阅）")
    parser.add_argument("legs", help='组合腿 JSON 数组，如 [{"code":"EC.xxx-FRA","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"},...]')
    parser.add_argument("--mvc", required=True, help="MVC 标的，从 get_valid_combo_list.py 返回值透传")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    request_combo_quotes(legs_json=args.legs, mvc=args.mvc, output_json=args.output_json)
