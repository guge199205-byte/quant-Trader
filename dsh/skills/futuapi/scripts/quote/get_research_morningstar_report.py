#!/usr/bin/env python3
"""
获取晨星研究报告

功能：获取指定股票的晨星研究报告，含星级评分、公允价值、护城河、财务健康、分析师观点等
用法：python get_research_morningstar_report.py [-h] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及 REIT

参数说明：
- code: 股票代码，如 HK.00700

返回字段说明：
- rating_type:                         评级类型（Qot_Common.MorningstarRatingType：1=定量 2=定性）
- star_rating / star_update_time / star_update_time_str: 晨星星级（1-5）及更新时间戳和日期
- fair_value:                          公允价值
- economic_moat_label / uncertainty_label / capital_allocation_label: 各维度评级文字（financial_health_label 视股票而定）
- [xxx]_content（多个）:              各维度分析文本，涵盖 fair_value/economic_moat/uncertainty/financial_health/capital_allocation/analyst_note/investment_thesis/fundamentals/valuation（部分视数据而定）
- analyst_note_title:                 分析师观点标题
- bull_say / bear_say:                 多空观点列表，每项含 context/update_time_str
- analyst_report_by_line:             分析师署名列表
- analyst_report_update_time / analyst_report_update_time_str: 分析师报告更新时间戳和日期
- pdf_url:                             PDF 报告链接
"""
import argparse
import json
import sys
import os as _os

sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close, is_empty

_SEP64 = "=" * 64
_DIV64 = "-" * 64

# 评级类型翻译（展示型枚举）
_RATING_TYPE_LABEL = {
    1: "定量评级",
    2: "定性评级",
}

_STAR_LABEL = {
    1: "★",
    2: "★★",
    3: "★★★",
    4: "★★★★",
    5: "★★★★★",
}


def _fmt(v, default="—"):
    if v is None or v == "":
        return default
    return v


def _swut_text(d):
    """从 StringWithUpdateTime dict 取 context 文本，d 可能为 None 或 {}"""
    if not d:
        return ""
    return d.get("context") or ""


def _swut_time(d):
    """从 StringWithUpdateTime dict 取 update_time_str"""
    if not d:
        return ""
    return d.get("update_time_str") or ""


def get_research_morningstar_report(code, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_research_morningstar_report(code)
        check_ret(ret, data, ctx, "获取晨星研究报告")

        if is_empty(data) or not any(data.values()):
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False))
            return

        print(_SEP64)
        print(f"晨星研究报告  标的：{code}")
        print(_DIV64)

        # 基础评级信息
        rt = data.get("rating_type")
        if rt is not None:
            print(f"评级类型:             {_RATING_TYPE_LABEL.get(rt, str(rt))}")
        sr = data.get("star_rating")
        if sr is not None:
            print(f"晨星星级:             {_STAR_LABEL.get(sr, str(sr))}")
        star_time = data.get("star_update_time_str")
        if star_time:
            print(f"星级更新日期:         {star_time}")
        fv = data.get("fair_value")
        if fv is not None:
            print(f"公允价值:             {fv:.3f}")
        fvc = data.get("fair_value_content")
        fv_time = _swut_time(fvc)
        if fv_time:
            print(f"公允价值更新日期:     {fv_time}")
        if any([rt, sr, star_time, fv]):
            print()

        # 公允价值分析
        fvc_text = _swut_text(fvc)
        if fvc_text:
            print("[公允价值分析]")
            if fv_time:
                print(f"  更新日期: {fv_time}")
            for line in fvc_text.splitlines():
                print(f"  {line}")
            print()

        # 评级块辅助：label 或 content 任一非空才输出
        def _print_rating_section(title, label, content_d):
            text = _swut_text(content_d)
            time_str = _swut_time(content_d)
            if not label and not text:
                return
            print(f"[{title}]")
            if label:
                print(f"  评级: {label}")
            if time_str:
                print(f"  更新日期: {time_str}")
            if text:
                for line in text.splitlines():
                    print(f"  {line}")
            print()

        _print_rating_section("护城河评级",
                              data.get("economic_moat_label"),
                              data.get("economic_moat_content"))
        _print_rating_section("不确定性评级",
                              data.get("uncertainty_label"),
                              data.get("uncertainty_content"))
        _print_rating_section("财务健康评级",
                              data.get("financial_health_label"),
                              data.get("financial_health_content"))
        _print_rating_section("资本配置评级",
                              data.get("capital_allocation_label"),
                              data.get("capital_allocation_content"))

        # 分析师署名
        by_lines = data.get("analyst_report_by_line") or []
        ar_time = data.get("analyst_report_update_time_str")
        if by_lines or ar_time:
            print("[分析师署名]")
            for name in by_lines:
                print(f"  {name}")
            if ar_time:
                print(f"  报告更新日期: {ar_time}")
            print()

        # 多方观点
        bull = data.get("bull_say") or []
        if bull:
            print("[多方观点]")
            bull_time = _swut_time(bull[0])
            if bull_time:
                print(f"  更新日期: {bull_time}")
            for item in bull:
                print(f"  • {_swut_text(item)}")
            print()

        # 空方观点
        bear = data.get("bear_say") or []
        if bear:
            print("[空方观点]")
            bear_time = _swut_time(bear[0])
            if bear_time:
                print(f"  更新日期: {bear_time}")
            for item in bear:
                print(f"  • {_swut_text(item)}")
            print()

        # 分析师观点
        note_title_d = data.get("analyst_note_title")
        note_content_d = data.get("analyst_note_content")
        note_title = _swut_text(note_title_d)
        note_content = _swut_text(note_content_d)
        note_time = _swut_time(note_title_d) or _swut_time(note_content_d)
        if note_title or note_content:
            print("[分析师观点]")
            if note_title:
                print(f"  标题: {note_title}")
            if note_time:
                print(f"  更新日期: {note_time}")
            if note_content:
                for line in note_content.splitlines():
                    print(f"  {line}")
            print()

        # 投资论点
        thesis_d = data.get("investment_thesis_content")
        thesis_text = _swut_text(thesis_d)
        thesis_time = _swut_time(thesis_d)
        if thesis_text:
            print("[投资论点]")
            if thesis_time:
                print(f"  更新日期: {thesis_time}")
            if thesis_text:
                for line in thesis_text.splitlines():
                    print(f"  {line}")
            print()

        # 基本面报告
        fund_text = _swut_text(data.get("fundamentals_content"))
        if fund_text:
            print("[基本面报告]")
            for line in fund_text.splitlines():
                print(f"  {line}")
            print()

        # 估值报告
        val_text = _swut_text(data.get("valuation_content"))
        if val_text:
            print("[估值报告]")
            for line in val_text.splitlines():
                print(f"  {line}")
            print()

        # PDF链接
        pdf_url = data.get("pdf_url")
        if pdf_url:
            print(f"PDF报告链接: {pdf_url}")

        print(_SEP64)

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
    parser = argparse.ArgumentParser(description="获取晨星研究报告")
    parser.add_argument(
        "code",
        help="股票代码，如 HK.00700",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()
    get_research_morningstar_report(args.code, output_json=args.output_json)
