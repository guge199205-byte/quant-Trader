"""LLM 输出解析加固（2026-09-12 P0-5）。

09-11 09:35 实录：deepseek-v4-pro 输出散文（解释文字 + JSON 块），
`live_llm_trade.parse_decision` 只认「整段 / ```json 围栏」→ 解析失败 → `continue`
→ 当天该 agent 0 买 0 卖，且失败没有任何落痕（UI 对话 tab 看不见）。同一天盘中解析
（`live_prompt_context.parse_intraday_decision`）却能从散文里取出决策——两条链的
解析能力不一致，本身就是 bug。

本文件钉住四件事：
  1. 统一抽取器 `llm_json.extract_json`：整段 / 围栏 / 散文夹块 / 嵌套与字符串内
     花括号 / 多块取首个通过校验的 / 全都不行 → None；
  2. 两个解析器都能吃散文输出（不再「解析失败，跳过」）；
  3. pct 脏值（"0.3股"）与非有限数（NaN/Inf）不再抛 ValueError 炸穿整轮，按
     **脏值**处理（pct_given=False + pct_bad_raw，消费点停手留痕）；只有真「没给」
     （None/空）才回退默认。明说 0 仍区分得出来；
  4. 卖出决策缺 pct → 按清仓（`_sell_fraction`），不是静默算 0 股跳过。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_llm_json.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import llm_json  # noqa: E402


# ---------- 抽取器本体 ----------

def test_extract_plain_json():
    assert llm_json.extract_json('{"a": 1, "b": [1, 2]}') == {"a": 1, "b": [1, 2]}


def test_extract_from_json_fence():
    text = '分析如下：\n```json\n{"a": 1}\n```\n以上。'
    assert llm_json.extract_json(text) == {"a": 1}


def test_extract_from_prose_without_fence():
    """散文夹裸 JSON（09-11 真实形态）——围栏没有也要能取出来。"""
    text = ('盘面观察：指数在 3927-3949 区间震荡，我的判断是先减仓。\n'
            '{"decisions": [{"action": "sell", "code": "001312.SZ"}]}\n'
            '以上，请执行。')
    obj = llm_json.extract_json(text)
    assert obj["decisions"][0]["code"] == "001312.SZ"


def test_extract_nested_braces():
    text = '前缀 {"a": {"b": {"c": 1}}, "d": 2} 后缀'
    assert llm_json.extract_json(text) == {"a": {"b": {"c": 1}}, "d": 2}


def test_extract_braces_inside_string_values():
    """字符串里的花括号不参与括号计数（naive 扫描会在这里提前截断）。"""
    text = '说明：{"reason": "涨到 17.90 } 减 50%", "code": "001312.SZ"}'
    obj = llm_json.extract_json(text)
    assert obj["reason"] == "涨到 17.90 } 减 50%"
    assert obj["code"] == "001312.SZ"


def test_extract_skips_blocks_failing_validator():
    """校验器：首个块不是目标结构时继续找下一块（保「首个含 decisions 的块」语义）。"""
    text = '先给个别的 {"note": "x"}，决策在 {"decisions": [{"action": "hold"}]}'
    obj = llm_json.extract_json(
        text, want=lambda d: isinstance(d.get("decisions"), list) and bool(d["decisions"]))
    assert obj["decisions"][0]["action"] == "hold"


def test_extract_returns_none_when_want_never_satisfied():
    assert llm_json.extract_json('{"decisions": []}',
                                 want=lambda d: bool(d.get("decisions"))) is None


def test_extract_returns_none_on_prose_or_empty():
    assert llm_json.extract_json("今天不交易，继续观察。") is None
    assert llm_json.extract_json("") is None
    assert llm_json.extract_json(None) is None


def test_extract_ignores_non_dict_json():
    """整段是数组/字符串（不是决策对象）→ 不算命中，继续往后找。"""
    text = '[1, 2] 然后 {"decisions": [{"action": "hold"}]}'
    obj = llm_json.extract_json(
        text, want=lambda d: bool(d.get("decisions")))
    assert obj["decisions"][0]["action"] == "hold"


# ---------- parse_intraday_decision（盘中） ----------

def test_intraday_parser_reads_prose_json():
    from live_prompt_context import parse_intraday_decision

    text = ('当前持仓分析：001312 跌破支撑。\n\n'
            '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": 1, "reason": "破位"}]}\n\n'
            '（以上为模型输出）')
    ds = parse_intraday_decision(text)
    assert ds and ds[0]["action"] == "sell" and ds[0]["pct"] == 1.0


def test_intraday_parser_dirty_pct_does_not_raise():
    """pct 脏值（"0.3股"）旧实现抛 ValueError → 穿透调用方炸穿整轮分析。"""
    from live_prompt_context import parse_intraday_decision

    ds = parse_intraday_decision(
        '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": "0.3股"}]}')

    assert ds[0]["pct"] == 0.0 and ds[0]["pct_given"] is False


def test_intraday_parser_nan_pct_treated_as_dirty():
    """NaN 不是「没表达」：JSON 允许字面量 NaN（模型/手改都能给），按 missing 处理
    会让卖出链走清仓——「想减 30%」被放大成整仓，方向与脏值停手原则相反
    （2026-09-12 终审 HIGH）。"""
    from live_prompt_context import parse_intraday_decision

    ds = parse_intraday_decision(
        '{"decisions": [{"action": "watch", "code": "600309.SH", "pct": NaN}]}')

    assert ds[0]["pct_given"] is False
    assert ds[0]["pct_bad_raw"]                       # 脏值标记：消费点据此停手
    assert "nan" in ds[0]["pct_bad_raw"].lower()


def test_intraday_parser_explicit_zero_still_distinguishable():
    from live_prompt_context import parse_intraday_decision

    ds = parse_intraday_decision(
        '{"decisions": [{"action": "watch", "code": "600309.SH", "pct": 0}]}')

    assert ds[0]["pct"] == 0.0 and ds[0]["pct_given"] is True


def test_intraday_parser_dirty_confidence_does_not_raise():
    from live_prompt_context import parse_intraday_decision

    ds = parse_intraday_decision(
        '{"decisions": [{"action": "hold", "code": "600309.SH", "confidence": "高"}]}')

    assert ds[0]["confidence"] == 0.0


# ---------- parse_decision（09:35 调仓） ----------

def test_trade_parser_reads_prose_json():
    import live_llm_trade as L

    text = ('先看大盘：弱势。\n{"decisions": [{"action": "sell", "code": "001312.SZ", '
            '"pct": 0.5, "reason": "减半"}]}\n完毕。')
    ds = L.parse_decision(text)

    assert ds and ds[0]["pct"] == 0.5 and ds[0]["pct_given"] is True


def test_trade_parser_missing_pct_marks_not_given():
    import live_llm_trade as L

    ds = L.parse_decision('{"decisions": [{"action": "sell", "code": "001312.SZ"}]}')

    assert ds[0]["pct"] == 0.0 and ds[0]["pct_given"] is False


# 09-11 09:35 实录原文（logs/live_llm_trade.log:418 前 200 字，逐字）：整段散文、
# 没有 JSON。散文容忍**不等于**凭空造决策——这种输出仍必须判为解析失败。
_0911_PROSE = (
    "我需要为当前持仓和候选池生成决策。让我分析一下情况。\n\n当前持仓：\n"
    "- 001312.SZ 福恩股份，1100股，成本17.56，现价17.15，盈亏-2.32%，"
    "今日涨跌-0.41%，可卖量1100\n\n今日大盘方向：看多但总分很低（2.2/11）"
    "——这实际上是一个弱看多信号，接近中性/不确定。\n\n候选池：所有分数都在0.7左右，"
    "融合分都在0.006左右，基本上没有明显差异。所有候选池股票的信号分"
)


def test_0911_real_prose_still_parses_as_failure():
    import live_llm_trade as L

    assert L.parse_decision(_0911_PROSE) is None


def test_empty_decisions_is_not_a_parse_success():
    """`{"decisions": []}` 曾按「解析失败」处理（空数组没有信息量）；
    统一抽取器不得把它当成有效决策（否则闸门段会拿到 [] 静默放行）。"""
    import live_llm_trade as L

    assert L.parse_decision('{"decisions": []}') is None


def test_sell_fraction_defaults_to_full_when_pct_missing():
    """模型说「卖出」但漏了比例：旧代码 min(max(0,0),1)=0 股 → 静默不下单。"""
    from live_prompt_context import sell_fraction

    assert sell_fraction({"pct": 0.0, "pct_given": False}) == 1.0


def test_sell_fraction_keeps_explicit_values():
    from live_prompt_context import sell_fraction

    assert sell_fraction({"pct": 0.3, "pct_given": True}) == 0.3
    assert sell_fraction({"pct": 0.0, "pct_given": True}) == 0.0   # 明说不卖
    assert sell_fraction({"pct": 1.7}) == 1.0                      # 越界收敛
    assert sell_fraction({"pct": 0.5}) == 0.5                      # 无 pct_given 键的旧调用点


# ---------- 脏 pct 三态（2026-09-12 审查 HIGH-2） ----------
# 「给了值但解析不出」不许并进「没给」：卖出链对 missing 按清仓执行，对 dirty
# 必须停下留痕——把「想减 30%」执行成清仓是资金方向上不可逆的放大。

def test_parse_pct_three_states():
    from live_prompt_context import parse_pct

    assert parse_pct(0.3) == (0.3, "given")
    assert parse_pct("0.3") == (0.3, "given")
    assert parse_pct(None) == (0.0, "missing")
    assert parse_pct("") == (0.0, "missing")
    assert parse_pct(float("nan")) == (0.0, "dirty")
    assert parse_pct("三成") == (0.0, "dirty")
    assert parse_pct("0.3股") == (0.0, "dirty")
    assert parse_pct([0.3]) == (0.0, "dirty")


def test_parse_pct_non_finite_is_dirty_not_missing():
    """非有限数（含字符串 `1e999` → inf、`"nan"`）归 dirty：消费点宁可停手，不许
    按 missing 走到清仓（2026-09-12 终审 HIGH）。"""
    from live_prompt_context import parse_pct

    for bad in (float("inf"), float("-inf"), "inf", "1e999", "nan", "NaN"):
        assert parse_pct(bad) == (0.0, "dirty"), bad


def test_parse_pct_normalizes_percent_strings():
    """`"30%"` 是模型明确表达的比例：规范化成 0.3，而不是当脏值/没给。"""
    from live_prompt_context import parse_pct

    assert parse_pct("30%") == (0.3, "given")
    assert parse_pct("30％") == (0.3, "given")     # 全角
    assert parse_pct(" 0.3 ") == (0.3, "given")


def test_dirty_pct_marked_for_both_parsers():
    """两个解析器对脏值都带 pct_bad_raw 标记（执行段据此跳过并留痕）。"""
    from live_prompt_context import parse_intraday_decision
    import live_llm_trade as L

    raw = '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": "0.3股"}]}'
    a = parse_intraday_decision(raw)[0]
    b = L.parse_decision(raw)[0]
    for d in (a, b):
        assert d["pct_given"] is False and "0.3股" in d["pct_bad_raw"]


def test_percent_string_executes_as_ratio_not_full_liquidation():
    """`"pct": "30%"` 走完整链路 → 0.3（旧行为按未表达→清仓，3.3 倍超卖）。"""
    from live_prompt_context import sell_fraction
    import live_llm_trade as L

    d = L.parse_decision(
        '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": "30%"}]}')[0]

    assert d["pct_given"] is True and d["pct"] == 0.3
    assert sell_fraction(d) == 0.3


def test_sell_fraction_returns_zero_for_dirty_pct():
    """兜底：调用点万一漏检 dirty，宁可少卖（下一轮兜住），不可按清仓多卖。"""
    from live_prompt_context import sell_fraction

    assert sell_fraction({"pct": 0.0, "pct_given": False,
                          "pct_bad_raw": "'0.3股'"}) == 0.0


def test_nan_pct_sell_never_liquidates_end_to_end():
    """解析 → 执行比例的完整链路：`pct: NaN` 的卖出决策不许变成整仓卖出。

    2026-09-12 终审 HIGH 的场景：NaN 被判 missing → sell_fraction 返 1.0 →
    `int(avail * 1.0)` 全量下单，且没有 pct_bad_raw/pct_unparsed 任何留痕。"""
    from live_prompt_context import sell_fraction
    import live_llm_trade as L

    d = L.parse_decision(
        '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": Infinity}]}')[0]

    assert d["pct_given"] is False and d["pct_bad_raw"]
    assert sell_fraction(d) == 0.0
    # 非解析器构造的裸 dict（无 pct 标记）同样不许放大
    assert sell_fraction({"pct": float("nan")}) == 0.0
    assert sell_fraction({"pct": "0.3股"}) == 0.0


# ---------- 解析重试与失败留痕（decide_with_retry / _log_parse_failure） ----------

PROSE = "盘面很弱，我认为应该减仓，但先不急着动。"
GOOD = '{"decisions": [{"action": "sell", "code": "001312.SZ", "pct": 0.5}]}'


def _fake_call(monkeypatch, replies):
    """桩掉 live_hourly_analysis.call_llm（decide_with_retry 在函数内导入它）。"""
    import live_hourly_analysis

    calls = []

    def _call(prompt, agent):
        calls.append(prompt)
        content, usage = replies[min(len(calls) - 1, len(replies) - 1)]
        return content, usage

    monkeypatch.setattr(live_hourly_analysis, "call_llm", _call)
    return calls


def test_decide_with_retry_recovers_on_second_attempt(monkeypatch):
    import live_llm_trade as L

    calls = _fake_call(monkeypatch, [
        (PROSE, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        (GOOD, {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}),
    ])

    decisions, content, usage, reason = L.decide_with_retry("原始提示", "agentA")

    assert decisions and decisions[0]["pct"] == 0.5
    assert content == GOOD and reason == ""
    assert len(calls) == 2 and "只输出 JSON" in calls[1]    # 第二次带纠正提示
    assert usage["total_tokens"] == 35                     # 重试的 token 也计入


def test_decide_with_retry_single_call_when_parse_ok(monkeypatch):
    import live_llm_trade as L

    calls = _fake_call(monkeypatch, [(GOOD, None)])

    decisions, _content, _usage, reason = L.decide_with_retry("原始提示", "agentA")

    assert decisions and len(calls) == 1 and reason == ""


def test_decide_with_retry_gives_up_after_one_retry(monkeypatch):
    import live_llm_trade as L

    calls = _fake_call(monkeypatch, [(PROSE, None), ("还是不输出 JSON。", None)])

    decisions, content, _usage, reason = L.decide_with_retry("原始提示", "agentA")

    assert decisions is None and len(calls) == 2 and reason == "parse_failed"
    assert content == "还是不输出 JSON。"                    # 留痕用最后一次原文


def test_decide_with_retry_does_not_retry_empty_response(monkeypatch):
    """空响应 = API 调用失败（call_llm 内部已重试过网络错误）→ 不再浪费一轮。"""
    import live_llm_trade as L

    calls = _fake_call(monkeypatch, [("", None), (GOOD, None)])

    decisions, content, _usage, reason = L.decide_with_retry("原始提示", "agentA")

    assert decisions is None and content == "" and len(calls) == 1
    assert reason == "empty_output"          # 不许写成「模型输出解析不了」（审查 LOW-2）


def test_decide_with_retry_call_exception_is_api_failed(monkeypatch):
    """call_llm 抛异常（网络/桥/无 key）→ api_failed，且不再重试（无输出可重试）。"""
    import live_hourly_analysis
    import live_llm_trade as L

    calls = []

    def _boom(prompt, agent):
        calls.append(prompt)
        raise RuntimeError("connection refused")

    monkeypatch.setattr(live_hourly_analysis, "call_llm", _boom)

    decisions, content, _usage, reason = L.decide_with_retry("原始提示", "agentA")

    assert decisions is None and content == "" and len(calls) == 1
    assert reason == "api_failed"


def test_parse_failure_traces_to_dialog_and_events(monkeypatch, tmp_path):
    """失败必须留痕：对话流 kind=parse_failed + 告警事件（旧行为只有 stdout 一行）。"""
    import live_fills
    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    logs = []
    monkeypatch.setattr(live_hourly_analysis, "append_log",
                        lambda *a, **kw: logs.append((a, kw)))

    L._log_parse_failure("agentA", "提示词", PROSE, None)

    assert logs and logs[0][1].get("kind") == "parse_failed"
    assert PROSE in logs[0][0][1]                          # 原文进了对话流
    assert "重试" in logs[0][0][1]                          # 解析失败才说重试过
    ev = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    ev = [e for e in ev.values() if e["kind"] == "parse_failed"]
    assert ev and ev[0]["alert"] is True and "agentA" in ev[0]["msg"]


def test_api_failure_marked_distinctly(monkeypatch, tmp_path):
    """调用失败（无输出）不许写成「模型输出解析不了」（2026-09-12 审查 LOW-2）：
    两种失败排查方向不同——都在调用链 vs 在模型输出格式。"""
    import live_fills
    import live_hourly_analysis
    import live_llm_trade as L

    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    logs = []
    monkeypatch.setattr(live_hourly_analysis, "append_log",
                        lambda *a, **kw: logs.append((a, kw)))

    L._log_parse_failure("agentA", "提示词", "", None, "api_failed")

    assert logs[0][1].get("kind") == "api_failed"
    assert "调用失败" in logs[0][0][1] and "重试" not in logs[0][0][1]
    ev = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    ev = [e for e in ev.values() if e["kind"] == "api_failed"]
    assert ev and ev[0]["alert"] is True


def test_append_log_writes_kind(monkeypatch, tmp_path):
    """append_log 的 kind 落进 log.jsonl（前端按此渲染/过滤），普通对话不写该键。"""
    import json as _json

    import live_hourly_analysis as H

    monkeypatch.setattr(H, "ROOT", tmp_path)
    H.append_log("提问", "回答", "agentA", None, kind="parse_failed")
    H.append_log("提问2", "回答2", "agentA")

    files = list((tmp_path / "data/agent_data_astock/agentA/log").glob("*/log.jsonl"))
    rows = [_json.loads(ln) for ln in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["kind"] == "parse_failed" and "kind" not in rows[1]
