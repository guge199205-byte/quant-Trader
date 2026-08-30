#!/usr/bin/env python3
"""
获取指标计算结果（calcId + push 模式）

功能：用本地缓存 K 线发起指标计算请求（Qot_RequestIndicatorCalc，3260）；
      req 立即返回 calcId，结果由 OpenD 通过 Qot_PushIndicatorCalc(3261) 推送；
      本脚本注册 IndicatorCalcHandlerBase 同步等待该 calcId 的推送并打印结果。

代码与 K 线周期从 --kl-file JSON 顶层的 code/ktype 字段读取（由 get_kline 写出）。

用法：
    python get_indicator_calc_result.py --short-name MA --lang 1 \
        --kl-file E:/OpenD/Output/test_cache_kl_HK_00700_day_100.json --param 0=5
    python get_indicator_calc_result.py --short-name MA --lang 1 \
        --kl-file E:/OpenD/Output/test_cache_kl_HK_00700_day_100.json --param 0=5 --num 30

参数：
    --short-name  指标短名（对应 IndicatorInfo.shortName）                 [必填]
    --lang        语言类型：1=MyLang, 2=Python（IndicatorLangType）        [必填]
    --kl-file     K 线 JSON 路径（含 code/ktype/data，由 get_kline 写出）   [必填]
    --param       入参覆盖 idx=value（对应 IndicatorInputItem，index 从 0 起）；可多次，留空则使用云配置
    --num         截取前 N 条 K 线参与计算（正整数）；省略表示使用 --kl-file 中的全部 K 线
    --json        输出 JSON
"""
import argparse
import json
import sys
import threading
import os as _os
import pandas as pd
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import create_quote_context, check_ret, safe_close
from futu import IndicatorCalcHandlerBase, RET_OK


# JSON 顶层 ktype 字符串 → Qot_Common.KLType wire 值
KTYPE_WIRE_MAP = {
    "1m": 1,  "3m": 10, "5m": 6,  "15m": 7, "30m": 8, "60m": 9,
    "1d": 2,  "1w": 3,  "1M": 4,  "1Q": 11, "1Y": 5,
}


def _load_kl_payload(kl_file):
    with open(kl_file, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("K 线 JSON 需为对象，含 code/ktype/data 字段")
    code = raw.get("code")
    ktype = raw.get("ktype")
    records = raw.get("data", [])
    if not code:
        raise ValueError("缺少股票代码：JSON 内无 code 字段")
    return code, ktype, records


def _resolve_kl_type(json_ktype):
    if json_ktype is None:
        raise ValueError("缺少 K 线周期：JSON 内无 ktype 字段")
    if isinstance(json_ktype, int):
        return json_ktype
    s = str(json_ktype)
    if s in KTYPE_WIRE_MAP:
        return KTYPE_WIRE_MAP[s]
    raise ValueError(f"无法识别的 ktype: {json_ktype!r}（支持 {list(KTYPE_WIRE_MAP)} 或整数 wire 值）")


def _parse_params(items):
    result = []
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--param 格式应为 idx=value，收到: {it}")
        idx_str, val = it.split("=", 1)
        result.append({"index": int(idx_str), "value": val})
    return result


def _normalize_klines_for_sdk(records):
    """get_kline --json 输出 time，SDK request_indicator_calc_async 需要 time_key。"""
    out = []
    for row in records:
        r = dict(row)
        if "time_key" not in r and "time" in r:
            r["time_key"] = r.pop("time")
        out.append(r)
    return out


class _CalcCollector(IndicatorCalcHandlerBase):
    """按 calcId 路由 push，等待目标 calcId 的结果"""
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._results = {}            # calc_id -> (ret_code, content)
        self._cond = threading.Condition(self._lock)

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        # 失败时 content 仍是 dict（含 calc_id 与 err_msg），按 calcId 路由
        if isinstance(content, dict) and content.get("calc_id"):
            with self._cond:
                self._results[content["calc_id"]] = (ret_code, content)
                self._cond.notify_all()
        elif ret_code != RET_OK:
            print(f"push parse error: {content}", file=sys.stderr)
        return ret_code, content

    def wait_for(self, calc_id, timeout=60):
        with self._cond:
            while calc_id not in self._results:
                if not self._cond.wait(timeout):
                    raise TimeoutError(
                        f"等待推送超时（{timeout}s），calc_id={calc_id!r}"
                    )
            return self._results[calc_id]


def get_indicator_calc_result(short_name, lang, kl_file, param_items, output_json, num=None):
    ctx = None
    try:
        code, kl_type, records = _load_kl_payload(kl_file)
        kl_type = _resolve_kl_type(kl_type)
        # get_kline --json 输出 time；SDK 入参需要 time_key
        klines = _normalize_klines_for_sdk(records)
        input_params = _parse_params(param_items)
        ctx = create_quote_context()
        collector = _CalcCollector()
        ctx.set_handler(collector)

        ret, calc_id = ctx.request_indicator_calc_async(
            short_name=short_name,
            lang_type=lang,
            code=code,
            kl_type=kl_type,
            klines=klines,
            num=num,
            input_params=input_params,
        )
        check_ret(ret, calc_id, ctx, "获取指标计算结果")

        if not output_json:
            print(f"已发送计算请求 calc_id={calc_id}，等待推送（最长 60s）...")

        ret_code, result = collector.wait_for(calc_id)

        if output_json:
            print(json.dumps({
                "calc_id":     calc_id,
                "success":     ret_code == RET_OK,
                "err_msg":     result.get("err_msg", ""),
                "outputs":     result.get("outputs", []),
                "output_rows": result.get("output_rows", []),
            }, ensure_ascii=False))
            return

        if ret_code != RET_OK:
            print(f"calc_id={calc_id}  计算失败: {result.get('err_msg', '')}")
            sys.exit(1)

        outputs = result["outputs"]
        rows = result["output_rows"]
        lang_label = {1: "MyLang", 2: "Python"}.get(lang, str(lang))
        print(f"calc_id={calc_id}")
        print(f"指标: {short_name}  lang={lang_label}  {code}  K线={len(klines)}  输出线={len(outputs)}  （仅展示最后 10 条）")

        col_names = [o.get("name", f"line{i}") for i, o in enumerate(outputs)]
        records = []
        for row in rows:
            vals = row.get("values") or []
            rec = {"time": row.get("time", "")}
            for i, name in enumerate(col_names):
                rec[name] = vals[i] if i < len(vals) else None
            records.append(rec)

        df = pd.DataFrame(records, columns=["time", *col_names])
        with pd.option_context("display.max_columns", None,
                               "display.width", None,
                               "display.float_format", lambda v: f"{v:.3f}"):
            print(df.tail(10).to_string(index=False))
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取指标计算结果：发起计算请求并等待 push 推送结果")
    parser.add_argument("--short-name", required=True)
    parser.add_argument("--lang", type=int, required=True, choices=[1, 2])
    parser.add_argument("--kl-file", required=True, help="K 线 JSON（含 code/ktype）")
    parser.add_argument("--param", action="append", default=[], dest="params", metavar="IDX=VAL")
    parser.add_argument("--num", type=int, default=None,
                        help="截取前 N 条 K 线参与计算（正整数）；省略表示使用 --kl-file 中的全部 K 线")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()
    get_indicator_calc_result(
        short_name=args.short_name,
        lang=args.lang,
        kl_file=args.kl_file,
        param_items=args.params,
        output_json=args.output_json,
        num=args.num,
    )
