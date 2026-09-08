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
    def test_uses_generous_max_tokens(self, monkeypatch):
        """推理模型的 reasoning token 也计入 completion——8k 上限会把长策略的
        输出截断（0001 就是这样失败的），这里锁住这个上限别再被调回去。"""
        import requests

        seen: dict = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.update(json or {})
            return FakeResp()

        monkeypatch.setattr(ptp, "_env", lambda: {"OPENAI_API_BASE": "http://x",
                                                  "OPENAI_API_KEY": "k"})
        monkeypatch.setattr(requests, "post", fake_post)
        ptp.call_llm("system", "user", "m")
        assert seen["max_tokens"] == ptp.MAX_TOKENS
        assert ptp.MAX_TOKENS >= 16000


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
