"""LLM 输出 → JSON 对象的抽取器（实盘解析链唯一入口，2026-09-12 P0-5）。

为什么统一：盘中解析（`live_prompt_context.parse_intraday_decision`）与收盘调仓解析
（`live_llm_trade.parse_decision`）各写一份，能力还不一样——前者容忍散文里夹 JSON，
后者只认「整段 / ```json 围栏」。09-11 09:35 deepseek-v4-pro 输出散文夹 JSON，调仓
解析直接失败 → 当天该 agent 0 买 0 卖；同一份文本喂盘中解析就能取出来。

抽取顺序（与 live_prompt_context 原实现一致，多块取**首个通过校验的**）：
  1. 整段即 JSON；
  2. ```json 围栏；
  3. 括号平衡块——按字符串状态机扫描，字符串内的花括号不参与计数
     （原实现的朴素计数在 `{"reason": "涨到 17.9 } 减半"}` 这类输出上会截断失败）。

`want(obj) -> bool` 让调用方保留自己的语义校验（如「必须有非空 decisions 数组」），
校验不过的块继续往后找。全部失败返回 None —— **不抛异常**，由调用方决定
重试/留痕（见 `live_llm_trade.decide_with_retry`）。
"""
import json
import re

__all__ = ["extract_json"]


def extract_json(text: str | None, want=None) -> dict | None:
    """从 LLM 输出里抽出第一个满足 `want` 的 JSON 对象；找不到返回 None。

    want=None 时接受任意 dict（非 dict 的合法 JSON 如数组/字符串不算命中）。
    """
    if not text:
        return None

    def _accept(obj):
        if not isinstance(obj, dict):
            return None
        if want is not None and not want(obj):
            return None
        return obj

    def _try(payload: str):
        try:
            return _accept(json.loads(payload))
        except (json.JSONDecodeError, ValueError):
            return None

    hit = _try(text.strip())
    if hit is not None:
        return hit
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        hit = _try(m.group(1).strip())
        if hit is not None:
            return hit
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    hit = _try(text[i:j + 1])
                    if hit is not None:
                        return hit
                    break
    return None
