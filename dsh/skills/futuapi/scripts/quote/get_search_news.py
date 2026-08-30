#!/usr/bin/env python3
"""
搜索资讯

功能：按关键词搜索新闻、公告、评级等资讯
用法：python get_search_news.py space [--max-count 10] [--news-sub-type ALL] [--json]

接口限制：
- 每 30 秒内最多请求 10 次

参数说明：
- keyword: 搜索词（必填）
- --max-count: 最大返回条数（默认 10，最大 100）
- --news-sub-type: 资讯子类型（ALL/NEWS/NOTICE/RATING，默认 ALL）

返回字段：
- title: 标题
- news_sub_type: 资讯子类型
- source: 来源
- publish_time: 发布时间
- view_count: 浏览量
- related_securities: 关联标的列表
- url: 详情页链接
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
)

NEWS_SUB_TYPE_MAP = {}


def _load_news_sub_type_map():
    global NEWS_SUB_TYPE_MAP
    if NEWS_SUB_TYPE_MAP:
        return NEWS_SUB_TYPE_MAP
    try:
        from futu import NewsSubType
    except ImportError as e:
        raise RuntimeError(f"当前 futu-api 未提供 NewsSubType（{e}），请升级 SDK") from e
    NEWS_SUB_TYPE_MAP.update({
        "ALL": NewsSubType.ALL,
        "NEWS": NewsSubType.NEWS,
        "NOTICE": NewsSubType.NOTICE,
        "RATING": NewsSubType.RATING,
    })
    return NEWS_SUB_TYPE_MAP


def get_search_news(keyword, max_count=10, news_sub_type="ALL", output_json=False):
    if not keyword or not str(keyword).strip():
        raise ValueError("搜索词 keyword 不能为空")
    max_count = max(1, min(int(max_count), 100))
    sub_type_map = _load_news_sub_type_map()
    sub_type = sub_type_map.get(str(news_sub_type).upper())
    if sub_type is None:
        raise ValueError(f"不支持的 news_sub_type: {news_sub_type}，可选: {list(sub_type_map.keys())}")

    ctx = None
    try:
        ctx = create_quote_context()
        if not hasattr(ctx, "get_search_news"):
            raise RuntimeError("当前 OpenD/SDK 未提供 get_search_news，请升级到支持该接口的版本")

        ret, data = ctx.get_search_news(keyword.strip(), max_count, news_sub_type=sub_type)
        check_ret(ret, data, ctx, "搜索资讯")

        if is_empty(data):
            if output_json:
                print(json.dumps({
                    "keyword": keyword,
                    "news_sub_type": news_sub_type.upper(),
                    "data": [],
                }, ensure_ascii=False))
            else:
                print("无匹配结果")
            return

        records = df_to_records(data)
        if output_json:
            print(json.dumps({
                "keyword": keyword,
                "news_sub_type": news_sub_type.upper(),
                "count": len(records),
                "data": records,
            }, ensure_ascii=False))
        else:
            print("=" * 100)
            print(f"搜索资讯: {keyword} (类型={news_sub_type.upper()}, 共 {len(records)} 条)")
            print("=" * 100)
            print_display_df(data, max_colwidth=40)
            print("=" * 100)

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="搜索资讯")
    parser.add_argument("keyword", help="搜索词")
    parser.add_argument("--max-count", type=int, default=10, help="最大返回条数（默认 10，最大 100）")
    parser.add_argument("--news-sub-type", default="ALL",
                        choices=["ALL", "NEWS", "NOTICE", "RATING"],
                        help="资讯子类型（默认 ALL）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    get_search_news(args.keyword, args.max_count, args.news_sub_type, args.output_json)
