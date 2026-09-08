""".pine → Pyne-Python 转写：静态检查与产物读取。

转写产物是要被执行的代码，静态检查是唯一一道拦截（危险导入 / 动态执行 / 结构缺失），
漏检等于把模型输出直接喂给回测进程，所以这里逐条锁定。

运行：.venv/bin/python -m pytest tests/test_pine_to_pyne.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import pine_library as pl  # noqa: E402
from scripts import pine_to_pyne as ptp  # noqa: E402

GOOD = '''"""
@pyne

双均线交叉。
"""
from pynecore.lib import close, input, script, strategy, ta
from pynecore.types import Series


@script.strategy("双均线交叉", overlay=True, pyramiding=0)
def main(fast=input(5, "快线"), slow=input(20, "慢线")):
    f = ta.sma(close, fast)
    s = ta.sma(close, slow)
    up: Series[bool] = f > s
    if up and not up[1]:
        strategy.entry('L', strategy.long)
    elif not up and up[1]:
        strategy.close('L')
'''


class TestExtractCode:
    def test_fenced_block(self):
        assert ptp.extract_code("说明\n```python\nx = 1\n```\n尾注") == "x = 1\n"

    def test_plain_text_falls_back(self):
        assert ptp.extract_code("x = 1") == "x = 1\n"


class TestCallLlm:
    @staticmethod
    def _patch(monkeypatch, seen: dict, finish: str = "stop"):
        import requests

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "x"}, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.update(json or {})
            return FakeResp()

        monkeypatch.setattr(ptp, "_env", lambda: {"OPENAI_API_BASE": "http://x",
                                                  "OPENAI_API_KEY": "k"})
        monkeypatch.setattr(requests, "post", fake_post)

    def test_default_disables_thinking(self, monkeypatch):
        """转写是照模板的机械活：开思考会把 completion 额度烧光——0001 烧 23755 token，
        0004 直接 32k 截断。默认必须关思考。"""
        seen: dict = {}
        self._patch(monkeypatch, seen)
        ptp.call_llm("system", "user", "m")
        assert seen["thinking"] == {"type": "disabled"}
        assert seen["max_tokens"] == ptp.MAX_TOKENS_FAST

    def test_think_mode_keeps_generous_cap(self, monkeypatch):
        seen: dict = {}
        self._patch(monkeypatch, seen)
        ptp.call_llm("system", "user", "m", think=True)
        assert "thinking" not in seen
        assert seen["max_tokens"] == ptp.MAX_TOKENS
        assert ptp.MAX_TOKENS >= 16000

    def test_truncation_returned_not_raised(self, monkeypatch):
        """截断要能回传给 transpile 做「开思考重试」，不能在底层就抛掉。"""
        seen: dict = {}
        self._patch(monkeypatch, seen, finish="length")
        content, usage, truncated = ptp.call_llm("system", "user", "m")
        assert truncated is True
        assert content == "x" and usage["completion_tokens"] == 2


class TestTranspileLadder:
    """关思考优先、截断才开思考重试。"""

    @staticmethod
    def _fake_pine(monkeypatch, tmp_path):
        monkeypatch.setattr(ptp, "OUT_DIR", tmp_path)
        monkeypatch.setattr(ptp, "_load_pine", lambda _: {
            "title": "甲", "category": "c", "version": 5, "source": "//@version=5\n"})

    def test_clean_run_does_not_think(self, tmp_path, monkeypatch):
        self._fake_pine(monkeypatch, tmp_path)
        calls: list[bool] = []

        def fake(system, user, model, think=False):
            calls.append(think)
            return f"```python\n{GOOD}```", {"completion_tokens": 9}, False

        monkeypatch.setattr(ptp, "call_llm", fake)
        meta = ptp.transpile("0004")
        assert calls == [False]
        assert meta["think"] is False and meta["problems"] == []

    def test_truncation_retries_with_thinking(self, tmp_path, monkeypatch):
        self._fake_pine(monkeypatch, tmp_path)
        calls: list[bool] = []

        def fake(system, user, model, think=False):
            calls.append(think)
            return f"```python\n{GOOD}```", None, not think

        monkeypatch.setattr(ptp, "call_llm", fake)
        meta = ptp.transpile("0004")
        assert calls == [False, True]
        assert meta["think"] is True and meta["problems"] == []

    def test_double_truncation_raises(self, tmp_path, monkeypatch):
        self._fake_pine(monkeypatch, tmp_path)
        monkeypatch.setattr(ptp, "call_llm", lambda *a, **k: ("", None, True))
        with pytest.raises(RuntimeError, match="截断"):
            ptp.transpile("0004")


class TestRepair:
    """回测报错 → 让模型改一版。只写文件，执行永远归沙箱。"""

    @staticmethod
    def _setup(tmp_path, monkeypatch):
        d = tmp_path / "0004"
        d.mkdir(parents=True)
        (d / "candidate.py").write_text("旧代码\n", encoding="utf-8")
        (d / "meta.json").write_text(json.dumps({"title": "甲", "problems": []}),
                                     encoding="utf-8")
        monkeypatch.setattr(ptp, "OUT_DIR", tmp_path)
        monkeypatch.setattr(ptp, "_load_pine", lambda _: {
            "title": "甲", "category": "c", "version": 5, "source": "//@version=5\n"})
        return d

    def test_rewrites_candidate_and_records_history(self, tmp_path, monkeypatch):
        d = self._setup(tmp_path, monkeypatch)
        seen: dict = {}

        def fake_chat(messages, model, think=False):
            seen["messages"] = messages
            seen["think"] = think
            return f"```python\n{GOOD}```", {"completion_tokens": 7}, False

        monkeypatch.setattr(ptp, "_chat", fake_chat)
        meta = ptp.repair("0004", "TypeError: 'float' object is not subscriptable")

        assert (d / "candidate.py").read_text(encoding="utf-8").strip() == GOOD.strip()
        assert meta["problems"] == []
        assert len(meta["repairs"]) == 1
        assert "TypeError" in meta["repairs"][0]["error"]
        assert seen["think"] is False                      # 修复同样关思考
        assert "TypeError" in seen["messages"][-1]["content"]   # 报错进了提示词

    def test_static_problems_recorded(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(ptp, "_chat",
                            lambda *a, **k: ("```python\ndef main():\n    pass\n```", None, False))
        meta = ptp.repair("0004", "boom")
        assert any("缺少必需结构" in p for p in meta["problems"])

    def test_truncated_repair_raises(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(ptp, "_chat", lambda *a, **k: ("", None, True))
        with pytest.raises(RuntimeError, match="截断"):
            ptp.repair("0004", "boom")

    def test_missing_candidate_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ptp, "OUT_DIR", tmp_path / "nope")
        with pytest.raises(RuntimeError, match="没有候选可修"):
            ptp.repair("0004", "boom")


class TestDepine:
    """模型连着修两轮仍会漏 `? :` 与 `:=`（0400/0778 就这么挂的），改成机械改写。"""

    def test_ternary_becomes_condition_expression(self):
        code = "x: Series[float] = candle_range > 0 ? body / candle_range : 0.0\n"
        got, notes = ptp.depine(code)
        assert got == "x: Series[float] = (body / candle_range) if (candle_range > 0) else (0.0)\n"
        assert notes == ["自动改写 1 处 `? :` → `if/else`"]

    def test_nested_ternary_inside_parens(self):
        code = "s = close > open ? volume : (close < open ? -volume : 0.0)\n"
        got, _ = ptp.depine(code)
        assert "?" not in got and got.count("if") == 2

    def test_trailing_comment_survives(self):
        got, _ = ptp.depine("x = a ? 1 : 2  # 注释里的 ? 不算\n")
        assert got == "x = (1) if (a) else (2)  # 注释里的 ? 不算\n"

    def test_string_with_question_mark_untouched(self):
        code = 'msg = "a ? b"\nx = 1\n'
        assert ptp.depine(code) == (code, [])

    def test_walrus_assign_becomes_equals(self):
        got, notes = ptp.depine("def main():\n    x = 1\n    x := x + 1\n")
        assert ":=" not in got and "x = x + 1" in got
        assert notes == ["自动改写 1 处 `:=` → `=`"]

    def test_clean_code_untouched(self):
        assert ptp.depine(GOOD) == (GOOD, [])


class TestStaticCheck:
    def test_template_passes(self):
        assert ptp.static_check(GOOD) == []

    @pytest.mark.parametrize("code,frag", [
        ("import os\n" + GOOD, "禁止导入"),
        ("from subprocess import run\n" + GOOD, "禁止导入"),
        ("x = eval('1')\n" + GOOD, "禁止动态执行"),
        ("y = open('/etc/passwd')\n" + GOOD, "禁止动态执行"),
        ("z = globals()\n" + GOOD, "禁止反射"),
    ])
    def test_dangerous_calls_blocked(self, code, frag):
        probs = ptp.static_check(code)
        assert any(frag in p for p in probs), probs

    def test_missing_structure_flagged(self):
        probs = ptp.static_check("def main():\n    pass\n")
        assert any("缺少必需结构" in p for p in probs)

    def test_syntax_error_flagged(self):
        probs = ptp.static_check("@pyne\nfrom pynecore.lib import input\ndef main(:\n")
        assert any("语法错误" in p for p in probs)

    def test_pynecore_input_is_not_python_input(self):
        """模板里的 input() 是 pynecore.lib 的，不能误判成 Python 内建 input。"""
        assert not any("禁止动态执行" in p for p in ptp.static_check(GOOD))


class TestImportChecks:
    """漏 import 是实测最高频的运行时失败（NameError），必须在静态闸就抓住。"""

    def test_bare_name_without_import_flagged(self):
        code = GOOD.replace("up: Series[bool] = f > s", "up: Series[bool] = f > s and not na(f)")
        assert any("用了 na 但没从 pynecore.lib 导入" in p for p in ptp.static_check(code))

    def test_imported_name_passes(self):
        code = GOOD.replace("from pynecore.lib import close, input, script, strategy, ta",
                            "from pynecore.lib import close, input, na, script, strategy, ta")
        code = code.replace("up: Series[bool] = f > s", "up: Series[bool] = f > s and not na(f)")
        assert not any("没从 pynecore.lib 导入" in p for p in ptp.static_check(code))

    def test_local_variable_is_not_flagged(self):
        code = GOOD.replace("up: Series[bool] = f > s", "session = 1\n    up: Series[bool] = f > s")
        assert not any("session" in p for p in ptp.static_check(code))

    def test_comment_mention_is_not_flagged(self):
        code = GOOD.replace("    f = ta.sma(close, fast)",
                            "    # 这里要判断 na 但只是注释\n    f = ta.sma(close, fast)")
        assert not any("用了 na" in p for p in ptp.static_check(code))

    def test_ta_bar_index_namespace_flagged(self):
        code = GOOD.replace("up: Series[bool] = f > s", "up: Series[bool] = ta.bar_index > 0")
        assert any("bar_index 不在 ta 下" in p for p in ptp.static_check(code))

    def test_math_isnan_flagged(self):
        code = GOOD.replace("up: Series[bool] = f > s", "up: Series[bool] = math.isnan(f)")
        assert any("没有 isnan" in p for p in ptp.static_check(code))

    def test_single_target_for_multi_return_ta(self):
        """ta.macd 返回元组，赋给单个变量后下标拿到的是「另一个指标」不是历史值。"""
        code = GOOD.replace("up: Series[bool] = f > s",
                            "m: Series[float] = ta.macd(close, 12, 26, 9)")
        assert any("返回元组" in p for p in ptp.static_check(code))

    def test_unpacked_multi_return_passes(self):
        code = GOOD.replace("up: Series[bool] = f > s",
                            "m, sig, hist = ta.macd(close, 12, 26, 9)")
        assert not any("返回元组" in p for p in ptp.static_check(code))

    def test_expression_history_index_flagged(self):
        code = GOOD.replace("up: Series[bool] = f > s",
                            "up: Series[bool] = (high + low + close)[1] > f")
        assert any("对表达式直接取历史" in p for p in ptp.static_check(code))

    def test_list_literal_index_is_not_flagged(self):
        """`arr[0]` 这种普通下标不能被误伤。"""
        code = GOOD.replace("up: Series[bool] = f > s", "up: Series[bool] = f[0] > s")
        assert not any("对表达式直接取历史" in p for p in ptp.static_check(code))


class TestSizingCheck:
    def test_tiny_percent_position_flagged(self):
        code = GOOD.replace(
            '@script.strategy("双均线交叉", overlay=True, pyramiding=0)',
            '@script.strategy("双均线交叉", overlay=True, pyramiding=0,\n'
            '                 default_qty_type=strategy.percent_of_equity, default_qty_value=1,\n'
            '                 initial_capital=1000)')
        assert any("仓位金额过小" in p for p in ptp.static_check(code))

    def test_template_sizing_passes(self):
        code = GOOD.replace(
            '@script.strategy("双均线交叉", overlay=True, pyramiding=0)',
            '@script.strategy("双均线交叉", overlay=True, pyramiding=0,\n'
            '                 default_qty_type=strategy.percent_of_equity, default_qty_value=95,\n'
            '                 initial_capital=100000)')
        assert not any("仓位金额过小" in p for p in ptp.static_check(code))


class TestListTranspile:
    def test_reads_meta_and_report(self, tmp_path, monkeypatch):
        d = tmp_path / "pine_transpile/0001"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps(
            {"title": "甲", "model": "m", "problems": ["缺少必需结构：def main("]}),
            encoding="utf-8")
        (d / "candidate.py").write_text("x", encoding="utf-8")
        (d / "report.json").write_text(json.dumps({
            "symbol": "600309.SH", "trades": 7,
            "stats": {"Net profit": {"pct": 12.5},
                      "Max equity drawdown": {"pct": 8.0},
                      "Buy & hold return": {"pct": 30.0}}}), encoding="utf-8")
        monkeypatch.setattr(pl, "TRANSPILE_DIR", tmp_path / "pine_transpile")

        got = pl.list_transpile()["0001"]
        assert got["title"] == "甲"
        assert got["problems"] == ["缺少必需结构：def main("]
        assert got["has_candidate"] and got["has_report"]
        assert got["net_pct"] == 12.5 and got["bh_pct"] == 30.0 and got["trades"] == 7

    def test_missing_dir_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pl, "TRANSPILE_DIR", tmp_path / "nope")
        assert pl.list_transpile() == {}

    def test_broken_json_is_skipped(self, tmp_path, monkeypatch):
        d = tmp_path / "pine_transpile/0002"
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{ 不是 json", encoding="utf-8")
        monkeypatch.setattr(pl, "TRANSPILE_DIR", tmp_path / "pine_transpile")
        assert pl.list_transpile() == {}
