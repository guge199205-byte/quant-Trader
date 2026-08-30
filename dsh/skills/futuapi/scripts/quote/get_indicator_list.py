#!/usr/bin/env python3
"""
获取全部可用指标列表

功能：列出所有已加载指标条目，每个 entry 可同时含 MyLang 与 Python 两版，
      展示 short_name/full_name + 每个 input 的 value + outputs 列表。
      可通过 --search 子串过滤（大小写不敏感，仅匹配 short_name）。
      --lang  过滤语言：0=Unknown(不过滤), 1=MyLang, 2=Python
      --mode  搜索模式：0=Partial 部分匹配（默认），1=Exact 完全匹配并返回 script
              （Exact 必须配合 --search；命中条目带 script 字段）
用法：python get_indicator_list.py [--search SUB] [--lang 0|1|2] [--mode 0|1] [--json]
"""
import argparse
import json
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close

ENTRY_SEP = "-" * 80
INFO_SEP = "-" * 40


def _fmt_value(v):
    """把 {'type': str, 'value': ...} 渲染成字符串"""
    if v is None:
        return "<none>"
    t = v.get("type", "?")
    val = v.get("value")
    if t == "COLOR" and isinstance(val, dict):
        return "COLOR(a={alpha},r={red},g={green},b={blue})".format(**val)
    return f"{t}:{val}"


def _print_info(label, info):
    """打印一个语言版本的 info dict"""
    print(f"  [{label}] short_name={info['short_name']}  full_name={info['full_name']}")
    inputs = info.get("inputs") or []
    if inputs:
        print(f"    inputs ({len(inputs)}):")
        for ipt in inputs:
            v = ipt.get("value")
            var_name = ipt.get("var_name")
            suffix = f"  [{var_name}]" if var_name else ""
            print(f"      #{ipt['index']:>2} {ipt['name']}{suffix}: {_fmt_value(v)}")
    outputs = info.get("outputs") or []
    if outputs:
        print(f"    outputs ({len(outputs)}):")
        for o in outputs:
            print(f"      #{o['index']:>2} {o['name']}")
    script = info.get("script") or ""
    if script:
        fence_lang = "python" if label.lower() == "python" else "mylang"
        print(f"    script ({len(script)} chars):")
        print(f"```{fence_lang}")
        print(script)
        print("```")


def get_indicator_list(output_json=False, search_key=None, lang_type=0, search_mode=0):
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_indicator_list(
            search_key=search_key,
            lang_type=lang_type,
            search_mode=search_mode,
        )
        check_ret(ret, data, ctx, "获取指标列表")

        if output_json:
            print(json.dumps({"data": data}, ensure_ascii=False))
            return

        print("=" * 80)
        lang_label = {0: "All", 1: "MyLang", 2: "Python"}.get(lang_type, str(lang_type))
        mode_label = {0: "Partial", 1: "Exact"}.get(search_mode, str(search_mode))
        print(f"指标列表  lang={lang_label}  mode={mode_label}  search={search_key or '<none>'}")
        print("=" * 80)

        count_my = 0
        count_py = 0
        for idx, entry in enumerate(data):
            my = entry.get("my_lang")
            py = entry.get("python")
            if my:
                count_my += 1
            if py:
                count_py += 1

            ident_parts = []
            if my:
                ident_parts.append(f"MyLang:{my['short_name']}")
            if py:
                ident_parts.append(f"Python:{py['short_name']}")
            ident = " | ".join(ident_parts) if ident_parts else "<empty>"

            # 不同 GUID（entry）之间用长横线分隔
            print(ENTRY_SEP)
            print(f"[{idx}] {ident}")

            if my:
                _print_info("MyLang", my)
            # 同一 entry 内 MyLang 与 Python 两块之间用短横线分隔
            if my and py:
                print(INFO_SEP)
            if py:
                _print_info("Python", py)

        print("=" * 80)
        print(f"合计 entry: {len(data)}（MyLang={count_my}, Python={count_py}）")
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "获取全部可用指标列表（每个 entry 可同时含 MyLang/Python 两版）。"
            "支持按子串过滤 short_name、按语言过滤、Exact 模式直接拿 script。"
        )
    )
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式（原始 dict 列表）")
    parser.add_argument("--search", dest="search", default=None, metavar="SUB",
                        help="搜索子串（大小写不敏感）；空值或未指定返回全部")
    parser.add_argument("--lang", dest="lang", type=int, default=0, choices=[0, 1, 2],
                        help="语言过滤：0=Unknown 不过滤（默认），1=MyLang，2=Python")
    parser.add_argument("--mode", dest="mode", type=int, default=0, choices=[0, 1],
                        help="搜索模式：0=Partial 部分匹配（默认），1=Exact 完全匹配且每条返回 script")
    args = parser.parse_args()
    get_indicator_list(
        output_json=args.output_json,
        search_key=args.search,
        lang_type=args.lang,
        search_mode=args.mode,
    )
