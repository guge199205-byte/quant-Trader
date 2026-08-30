#!/usr/bin/env python3
"""
获取评级汇总

功能：获取指定股票的机构或分析师评级汇总列表，或指定机构/分析师的评级详情
用法：python get_research_rating_summary.py [-h] [--rating-dimension-type RATING_DIMENSION_TYPE] [--uid UID] [--next-key NEXT_KEY] [--num NUM] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持美股正股及 REIT

参数说明：
- code: 股票代码，如 US.AAPL
- --rating-dimension-type: 评级维度类型：1=机构维度（默认） 2=分析师维度
- --uid: 空=汇总列表；非空=指定机构/分析师的评级详情（如分析师 uid 须搭配 --rating- dimension-type 2）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~20

返回字段说明：
- inst_rating_summary_list:    机构评级汇总列表（未指定 uid 且 rating_dimension_type=1 时），每项含 institution_info（institution_uid/institution_name 等）和 rating_item_list（每条含 rating/target_price/recommendation_date_str/rating_url 等）
- analyst_rating_summary_list: 分析师评级汇总列表（未指定 uid 且 rating_dimension_type=2 时），每项含 analyst_info（analyst_uid/analyst_name/num_of_stars/success_rate 等）和 rating_item_list
- inst_rating_detail:          机构评级详情（指定 uid 且 rating_dimension_type=1 时），含 institution_info/analyst_info_list/rating_item_list
- analyst_rating_detail:       分析师评级详情（指定 uid 且 rating_dimension_type=2 时），含 analyst_info/rating_item_list
- next_key:                    分页标识，"-1" 表示无更多数据
"""
import argparse
import json
import sys
import os as _os

import pandas as pd

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty, print_display_df

SEP = "=" * 64
DASH = "-" * 64

# ResearchRatingType：1=Sell 2=Underperform 3=Hold 4=Buy 5=StrongBuy；3230 仅返回 1/3/4
_RATING_LABEL = {1: "Sell(卖出)", 2: "Underperform(跑输)", 3: "Hold(持有)", 4: "Buy(买入)", 5: "StrongBuy(强力推荐)"}

# 近一年变动方向
_CHANGE_TYPE_LABEL = {0: "持平", 1: "上调", 2: "下调", 3: "新进"}


def _rating_str(rating):
    if rating is None:
        return "—"
    label = _RATING_LABEL.get(rating)
    if label:
        return f"{label}"
    return str(rating)


def _parse_change_type(items):
    """
    根据 ratingItemList 计算近一年变动方向：
      - 0条 → 0（维持，实际无数据）
      - 1条 → 3（首次评级）
      - ≥2条 → 比较 items[0]（最新）和 items[1]（前一条）
    """
    n = len(items)
    if n == 0:
        return 0
    if n == 1:
        return 3  # 首次
    curr = items[0]
    prev = items[1]
    curr_r = curr.get("rating") or 0
    prev_r = prev.get("rating") or 0
    if curr_r != prev_r:
        return 1 if curr_r > prev_r else 2
    curr_p = curr.get("target_price") or 0
    prev_p = prev.get("target_price") or 0
    _EPS = 1e-9
    if curr_p and prev_p:
        diff = curr_p - prev_p
        if diff > _EPS:
            return 1
        if diff < -_EPS:
            return 2
    return 0


def _fmt_price(raw):
    """目标价 double 值 → 显示字符串"""
    if raw is None or raw == 0:
        return "—"
    return f"{raw:.3f}"


def _format_target_price(items):
    """
    取 items[0] 作为当前目标价；向后找第一个 targetPrice 非零的作为前一次目标价。
    """
    if not items:
        return "—"
    curr_p = items[0].get("target_price") or 0
    prev_p = None
    for it in items[1:]:
        p = it.get("target_price")
        if p:
            prev_p = p
            break
    if prev_p is not None:
        display_curr = curr_p if curr_p else prev_p
        return f"{_fmt_price(prev_p)}→{_fmt_price(display_curr)}"
    return _fmt_price(curr_p) if curr_p else "—"


def _print_inst_info(info, prefix="  "):
    if not info:
        return
    print(f"{prefix}机构名称:   {info.get('institution_name', '—')}")
    print(f"{prefix}机构英文名: {info.get('institution_en_name', '—')}")
    print(f"{prefix}机构UID:    {info.get('institution_uid', '—')}")
    print(f"{prefix}来源:       {info.get('institution_source_name', '—')}")
    print(f"{prefix}更新日期:   {info.get('update_time_str', '—')}")


def _print_analyst_info(info, prefix="  "):
    if not info:
        return
    print(f"{prefix}分析师姓名:   {info.get('analyst_name', '—')}")
    print(f"{prefix}分析师UID:    {info.get('analyst_uid', '—')}")
    stars = info.get('num_of_stars')
    if stars is not None:
        print(f"{prefix}星级:         {stars}")
    success = info.get('success_rate')
    if success is not None:
        print(f"{prefix}成功率:       {float(success):.2f}%")
    excess = info.get('excess_return')
    if excess is not None:
        print(f"{prefix}超额收益:     {float(excess):.2f}%")
    stock_success = info.get('stock_success_rate')
    if stock_success is not None:
        print(f"{prefix}个股成功率:   {float(stock_success):.2f}%")
    stock_avg = info.get('stock_avg_return')
    if stock_avg is not None:
        print(f"{prefix}个股平均收益: {float(stock_avg):.2f}%")
    update_str = info.get('update_time_str')
    if update_str:
        print(f"{prefix}更新日期:     {update_str}")
    inst = info.get("institution_info")
    if inst:
        print(f"{prefix}所属机构:     {inst.get('institution_name', '—')} ({inst.get('institution_uid', '—')})")


def _inst_detail_items_df(items, analyst_info_list):
    stars_map = {an.get("analyst_uid", ""): an.get("num_of_stars", "—")
                 for an in analyst_info_list if an.get("analyst_uid")}
    rows = []
    for it in items:
        uid_val = it.get("analyst_uid", "—")
        rows.append({
            "分析师Uid":    uid_val,
            "星级":         stars_map.get(uid_val, "—"),
            "评级":         _rating_str(it.get("rating")),
            "目标价":       _fmt_price(it.get("target_price")),
            "评级日期":     it.get("recommendation_date_str", "—"),
            "评级链接":     it.get("rating_url", "—"),
        })
    return pd.DataFrame(rows)


def _analyst_detail_items_df(items, num_of_stars):
    rows = []
    for it in items:
        rows.append({
            "星级":        num_of_stars,
            "评级":        _rating_str(it.get("rating")),
            "目标价":      _fmt_price(it.get("target_price")),
            "评级日期":    it.get("recommendation_date_str", "—"),
            "评级链接":    it.get("rating_url", "—"),
        })
    return pd.DataFrame(rows)


def get_research_rating_summary(code, rating_dimension_type=None, uid=None, num=None,
                                next_key=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_research_rating_summary(
            code,
            rating_dimension_type=rating_dimension_type,
            uid=uid,
            num=num,
            next_key=next_key,
        )
        check_ret(ret, data, ctx, "获取评级汇总")

        if is_empty(data):
            if output_json:
                print(json.dumps({"code": code, "data": []}))
            else:
                print("无数据")
            return

        nk = data.get("next_key", "-1")

        # 计算返回条数
        def _count_items(d):
            n = 0
            n += len(d.get("inst_rating_summary_list", []))
            n += len(d.get("analyst_rating_summary_list", []))
            if d.get("inst_rating_detail"):
                n += 1
            if d.get("analyst_rating_detail"):
                n += 1
            return n

        total_items = _count_items(data)
        nk_display = "已结束(-1)" if nk == "-1" else (nk if nk else "已结束(-1)")

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False))
            return

        summary_list = data.get("inst_rating_summary_list", [])
        analyst_summary_list = data.get("analyst_rating_summary_list", [])
        inst_detail = data.get("inst_rating_detail")
        analyst_detail = data.get("analyst_rating_detail")

        if not any([summary_list, analyst_summary_list, inst_detail, analyst_detail]):
            print("无数据")
            return

        print(SEP)
        print(f"[评级汇总]  标的：{code}")
        print(DASH)

        # 机构维度汇总（uid 空, ratingDimensionType=1）
        if summary_list:
            print(f"[机构评级汇总]  共 {len(summary_list)} 条机构行")
            rows = []
            for row in summary_list:
                inst_info = row.get("institution_info", {})
                items = row.get("rating_item_list", [])
                change_type = _parse_change_type(items)
                curr_rating = items[0].get("rating") if items else None
                rows.append({
                    "机构名称":   inst_info.get("institution_name", "—"),
                    "机构Uid":    inst_info.get("institution_uid", "—"),
                    "评级":       _rating_str(curr_rating),
                    "目标价":     _format_target_price(items),
                    "近一年变动": f"{_CHANGE_TYPE_LABEL.get(change_type, change_type)}({len(items)})",
                    "时间":       items[0].get("recommendation_date_str", "—") if items else "—",
                })
            if rows:
                df = pd.DataFrame(rows)
                print_display_df(df, max_colwidth=60)
            else:
                print("  无评级条目")

        # 分析师维度汇总（uid 空, ratingDimensionType=2）
        if analyst_summary_list:
            print(f"[分析师评级汇总]  共 {len(analyst_summary_list)} 条分析师行")
            rows = []
            for row in analyst_summary_list:
                an_info = row.get("analyst_info", {})
                items = row.get("rating_item_list", [])
                change_type = _parse_change_type(items)
                curr_rating = items[0].get("rating") if items else None
                curr_item = items[0] if items else {}
                rows.append({
                    "分析师姓名":  an_info.get("analyst_name", "—"),
                    "星级":        an_info.get("num_of_stars", "—"),
                    "分析师Uid":   an_info.get("analyst_uid", "—"),
                    "评级":        _rating_str(curr_rating),
                    "目标价":      _format_target_price(items),
                    "近一年变动":  f"{_CHANGE_TYPE_LABEL.get(change_type, change_type)}({len(items)})",
                    "时间":        curr_item.get("recommendation_date_str", "—") if items else "—",
                    "链接":        curr_item.get("rating_url", "—"),
                })
            if rows:
                df = pd.DataFrame(rows)
                print_display_df(df, max_colwidth=60)
            else:
                print("  无评级条目")

        # 机构评级详情（uid 非空, ratingDimensionType=1）
        if inst_detail:
            print("[机构评级详情]")
            _print_inst_info(inst_detail.get("institution_info", {}))
            print()
            analyst_list = inst_detail.get("analyst_info_list", [])
            if analyst_list:
                print(f"  旗下分析师（{len(analyst_list)} 位）:")
                for an in analyst_list:
                    stars = an.get('num_of_stars')
                    success = an.get('success_rate')
                    print(f"    - {an.get('analyst_name', '—')}  uid={an.get('analyst_uid', '—')}"
                          + (f"  星级={stars}" if stars is not None else "")
                          + (f"  成功率={success}%" if success is not None else "")
                          + f"  更新={an.get('update_time_str', '—')}")
            items = inst_detail.get("rating_item_list", [])
            if items:
                print(f"\n  评级记录（{len(items)} 条）:")
                df = _inst_detail_items_df(items, analyst_list)
                print_display_df(df, max_colwidth=60)

        # 分析师评级详情（uid 非空, ratingDimensionType=2）
        if analyst_detail:
            print("[分析师评级详情]")
            an_info = analyst_detail.get("analyst_info", {})
            _print_analyst_info(an_info)
            items = analyst_detail.get("rating_item_list", [])
            if items:
                print(f"\n  评级记录（{len(items)} 条）:")
                df = _analyst_detail_items_df(items, an_info.get("num_of_stars", "—"))
                print_display_df(df, max_colwidth=60)

        print(DASH)
        print(f"返回条数：{total_items}   --next-key：{nk_display}")
        print(SEP)

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
    parser = argparse.ArgumentParser(description="获取研究评级汇总，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 US.AAPL")
    parser.add_argument("--rating-dimension-type", type=int, default=None,
                        help="评级维度类型：1=机构维度（默认）  2=分析师维度")
    parser.add_argument("--uid", default=None,
                        help="空=汇总列表；非空=指定机构/分析师的评级详情"
                             "（如分析师 uid 须搭配 --rating-dimension-type 2）")
    parser.add_argument("--next-key", default=None, dest="next_key",
                        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据")
    parser.add_argument("--num", type=int, default=None,
                        help="每页返回数量，默认 10，范围 1~20")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    args = parser.parse_args()
    get_research_rating_summary(
        args.code,
        rating_dimension_type=args.rating_dimension_type,
        uid=args.uid,
        num=args.num,
        next_key=args.next_key,
        output_json=args.output_json,
    )
