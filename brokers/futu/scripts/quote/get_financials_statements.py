#!/usr/bin/env python3
"""
获取财务报表

功能：获取指定股票的财务报表（利润表/资产负债表/现金流量表/关键指标）
用法：python get_financials_statements.py [-h] [--statement-type STATEMENT_TYPE] [--financial-type FINANCIAL_TYPE] [--currency-code CURRENCY_CODE] [--next-key KEY] [--num N] [--json] code

接口限制：
- 每 30 秒内最多请求 30 次
- 支持正股及基金

参数说明：
- code: 股票代码，如 HK.00700
- --statement-type: 财务报表类型（必填可选）：1=利润表(Income) 2=资产负债表(BalanceSheet) 3=现金流量表(CashFlow) 4=关键指标(MainIndex)；（默认：1=利润表）
- --financial-type: 财报类型：1=Q1单季报 2=Q2单季报 3=Q3单季报 4=Q4单季报 5=Q6累计报(Q1+Q2) 6=Q9累计报(Q1+Q2+Q3) 7=年报 9=单季报组合(Q1/Q2/Q3/Q4) 10=单季报+年报 11=累计季报(Q1/Q6/Q9/年报)；（默认：10=单季报+年报）
- --currency-code: 币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据（默认：空=原始货币）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

返回字段说明：
- structure_list: 字段结构列表，每项含 field_id 和 display_name（字段展示名）
- report_list:    财报数据列表，每项含 period_text/date_time_str/fiscal_year/financial_type/currency_code/currency_info/accounting_standards/auditor_report 等元信息，及 item_list（每条含 field_id/data/yoy/qoq）
- next_key:       分页标识，"-1" 表示无更多数据
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
    print_display_df,
    format_big_number,
)

SEP64  = "=" * 64
DASH64 = "-" * 64

# 报表类型映射
STATEMENT_TYPE_MAP = {
    1: "利润表",
    2: "资产负债表",
    3: "现金流量表",
    4: "关键指标",
}

# 财报类型映射
F10_TYPE_MAP = {
    1:  "Q1单季报",
    2:  "Q2单季报",
    3:  "Q3单季报",
    4:  "Q4单季报",
    5:  "Q6累计报（Q1+Q2）",
    6:  "Q9累计报（Q1+Q2+Q3）",
    7:  "年报",
    9:  "单季报组合（Q1/Q2/Q3/Q4）",
    10: "单季报+年报",
    11: "累计季报（Q1/Q6/Q9/年报）",
}


def _fmt_value(v):
    """财务数据格式化：大数字统一转万/亿，小数保留4位有效数字。"""
    if v is None:
        return "--"
    try:
        f = float(v)
        abs_f = abs(f)
        if abs_f >= 1e8:
            return f"{f / 1e8:.2f}亿"
        if abs_f >= 1e4:
            return f"{f / 1e4:.2f}万"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v):
    """百分比格式化：保留2位小数，加 % 符号。"""
    if v is None:
        return "--"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "--"


def _build_display_output(code, statement_type, financial_type, currency_code, result):
    """非 JSON 路径"""

    lines = []
    lines.append(SEP64)
    lines.append(f"财务报表  标的：{code}")
    lines.append(DASH64)

    structure_list = result.get("structure_list", [])
    report_list    = result.get("report_list", [])

    if not report_list:
        lines.append("无数据")
    else:
        # 字段 ID -> 展示名映射
        id_to_name = {e["field_id"]: e["display_name"] or f"字段{e['field_id']}" for e in structure_list}

        # 每期输出一块
        for rpt in report_list:
            period = rpt.get("period_text") or "--"
            date_s = rpt.get("date_time_str") or "--"
            cur_info   = rpt.get("currency_info") or ""
            acc_std    = rpt.get("accounting_standards") or ""
            aud_report = rpt.get("auditor_report") or ""
            cur_code   = rpt.get("currency_code") or ""

            meta_parts = [f"截止日：{date_s}"]
            if cur_info:
                meta_parts.append(f"货币：{cur_info}")
            if cur_code and cur_code != cur_info:
                meta_parts.append(f"币种：{cur_code}")
            if acc_std:
                meta_parts.append(f"会计准则：{acc_std}")
            if aud_report:
                meta_parts.append(f"审计意见：{aud_report}")

            lines.append(f"【{period}】  " + "  ".join(meta_parts))

            item_map = {item["field_id"]: item for item in rpt.get("item_list", [])}
            for fid, fname in sorted(id_to_name.items()):
                item = item_map.get(fid)
                if item is None:
                    continue
                d   = item.get("data")
                yoy = item.get("yoy")
                qoq = item.get("qoq")
                val_str = _fmt_value(d)
                extras = []
                if yoy is not None:
                    extras.append(f"同比：{_fmt_pct(yoy)}")
                if qoq is not None:
                    extras.append(f"环比：{_fmt_pct(qoq)}")
                extra_str = "  " + "  ".join(extras) if extras else ""
                lines.append(f"  {fname}：{val_str}{extra_str}")

    lines.append(DASH64)
    nk = result.get("next_key", "-1")
    nk_disp = "已结束(-1)" if nk == "-1" else nk
    lines.append(f"返回条数：{len(report_list)}  --next-key：{nk_disp}")
    lines.append(SEP64)
    return "\n".join(lines)


def get_financials_statements(code, statement_type=None, financial_type=None, currency_code=None, next_key=None, num=None, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_financials_statements(code, statement_type=statement_type, financial_type=financial_type,
                                                  currency_code=currency_code, next_key=next_key, num=num)
        check_ret(ret, data, ctx, "获取财务报表")

        report_list = data.get("report_list", []) if isinstance(data, dict) else None
        no_data = is_empty(data) or (isinstance(data, dict) and not report_list)
        if no_data:
            if output_json:
                print(json.dumps({"code": code, "data": {}}))
            else:
                print("无数据")
            return

        if output_json:
            print(json.dumps({"code": code, "data": data}, ensure_ascii=False, default=str))
        else:
            print(_build_display_output(code, statement_type, financial_type, currency_code, data))

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
    parser = argparse.ArgumentParser(description="获取财务报表数据，支持分页拉取")
    parser.add_argument("code", help="股票代码，如 HK.00700")
    parser.add_argument(
        "--statement-type", type=int, default=None,
        help=(
            "财务报表类型（必填可选）："
            "1=利润表(Income)  2=资产负债表(BalanceSheet)  3=现金流量表(CashFlow)  4=关键指标(MainIndex)"
            "；（默认：1=利润表）"
        ),
    )
    parser.add_argument(
        "--financial-type", type=int, default=None, dest="financial_type",
        help=(
            "财报类型："
            "1=Q1单季报  2=Q2单季报  3=Q3单季报  4=Q4单季报  "
            "5=Q6累计报(Q1+Q2)  6=Q9累计报(Q1+Q2+Q3)  7=年报  "
            "9=单季报组合(Q1/Q2/Q3/Q4)  10=单季报+年报  11=累计季报(Q1/Q6/Q9/年报)"
            "；（默认：10=单季报+年报）"
        ),
    )
    parser.add_argument(
        "--currency-code", type=str, default=None,
        help="币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据（默认：空=原始货币）",
    )
    parser.add_argument(
        "--next-key", type=str, default=None, metavar="KEY",
        help="分页标识，首次不传，续拉填上次返回的 next_key；\"-1\" 表示无更多数据",
    )
    parser.add_argument(
        "--num", type=int, default=None, metavar="N",
        help="每页返回数量，默认 10，范围 1~50",
    )
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")

    args = parser.parse_args()

    get_financials_statements(args.code, statement_type=args.statement_type,
                              financial_type=args.financial_type, currency_code=args.currency_code,
                              next_key=args.next_key, num=args.num, output_json=args.output_json)
