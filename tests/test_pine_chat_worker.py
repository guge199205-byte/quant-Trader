"""Pine 对话 worker：上下文拼装 + 队列消费（模型调用打桩，不联网）。

关键口径：候选/回答只在宿主上产生，容器只读结果；本轮问题不进历史（否则会重复问两遍）。

运行：.venv/bin/python -m pytest tests/test_pine_chat_worker.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import pine_library as pl          # noqa: E402
from scripts import pine_chat_worker as pcw              # noqa: E402


def _fake_source(item_id: str, source: str = "//@version=5\nstrategy('t')") -> dict:
    return {"id": item_id, "title": "斐波那契云", "file": "001.pine",
            "category": "趋势跟踪", "version": 5, "lines": 120,
            "source_kind": "desktop", "source": source}


@pytest.fixture()
def chat_env(tmp_path, monkeypatch):
    """对话目录指到临时库；源码/备注/报告/模型全部打桩。"""
    d = tmp_path / "chat"
    monkeypatch.setattr(pl, "CHAT_DIR", d)
    monkeypatch.setattr(pl, "CHAT_QUEUE", d / "queue")
    monkeypatch.setattr(pl, "CHAT_JOBS", d / "jobs")
    monkeypatch.setattr(pl, "get_source", lambda item_id: _fake_source(item_id))
    monkeypatch.setattr(pl, "get_note", lambda item_id: {
        "id": item_id, "note": "趋势市有效", "tags": ["趋势"], "rating": 4,
        "status": "观察中", "by": "user"})
    monkeypatch.setattr(pl, "read_report", lambda item_id: {
        "symbol": "600309.SH", "adj": "backward", "trades": 203,
        "stats": {"Net profit": {"pct": 62.3}, "Buy & hold return": {"pct": 555.1},
                  "Max equity drawdown": {"pct": 42.2}}})
    monkeypatch.setattr(pcw, "_chat",
                        lambda messages, model, think=False: ("这是回答", {"total_tokens": 10}, False))
    return d


class TestBuildMessages:
    def test_carries_source_note_report_and_history(self, chat_env):
        msgs = pcw.build_messages("0001", "问题", [
            {"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"}])
        assert msgs[0]["role"] == "system"
        ctx = msgs[0]["content"]
        assert "斐波那契云" in ctx
        assert "趋势市有效" in ctx
        assert "净收益 62.3%" in ctx and "最大回撤 42.2%" in ctx
        assert "strategy('t')" in ctx
        assert [m["content"] for m in msgs[1:]] == ["旧问", "旧答", "问题"]

    def test_says_no_backtest_when_report_missing(self, chat_env, monkeypatch):
        monkeypatch.setattr(pl, "read_report", lambda item_id: {})
        ctx = pcw.build_messages("0001", "问", [])[0]["content"]
        assert "还没有" in ctx

    def test_truncates_long_source(self, chat_env, monkeypatch):
        monkeypatch.setattr(pl, "get_source",
                            lambda item_id: _fake_source(item_id, "x" * (pcw.MAX_SOURCE_CHARS + 500)))
        ctx = pcw.build_messages("0001", "问", [])[0]["content"]
        assert "已截断" in ctx
        assert len(ctx) < pcw.MAX_SOURCE_CHARS + 2000

    def test_drops_rows_with_unexpected_role(self, chat_env):
        msgs = pcw.build_messages("0001", "问", [{"role": "system", "content": "越权"}])
        assert [m["role"] for m in msgs] == ["system", "user"]


class TestProcess:
    def test_ask_flow_writes_thread_and_done_job(self, chat_env):
        req = pl.enqueue_chat("0001", "适合震荡市吗？")["request"]

        job = pcw.process(req)

        assert job["status"] == "done" and job["reply"] == "这是回答"
        thread = pl.chat_thread("0001")["messages"]
        assert [m["role"] for m in thread] == ["user", "assistant"]
        assert thread[0]["content"] == "适合震荡市吗？" and thread[0]["job_id"] == req["job_id"]
        assert not list(pl.CHAT_QUEUE.glob("*.json"))     # 摘牌了
        saved = json.loads((pl.CHAT_JOBS / f"{req['job_id']}.json").read_text(encoding="utf-8"))
        assert saved["status"] == "done"

    def test_current_question_is_not_duplicated_in_history(self, chat_env, monkeypatch):
        seen = {}

        def fake(messages, model, think=False):
            seen["msgs"] = messages
            return ("答", None, False)

        monkeypatch.setattr(pcw, "_chat", fake)
        pcw.process(pl.enqueue_chat("0001", "第一问")["request"])
        pcw.process(pl.enqueue_chat("0001", "第二问")["request"])
        assert [m["content"] for m in seen["msgs"] if m["role"] == "user"] == ["第一问", "第二问"]

    def test_model_called_without_thinking(self, chat_env, monkeypatch):
        seen = {}

        def fake(messages, model, think=False):
            seen.update(model=model, think=think)
            return ("答", None, False)

        monkeypatch.setattr(pcw, "_chat", fake)
        pcw.process(pl.enqueue_chat("0001", "问")["request"])
        assert seen == {"model": pl.CHAT_MODEL, "think": False}

    def test_edit_mode_rejected_until_p3(self, chat_env):
        req = pl.enqueue_chat("0001", "止损改成 3%", mode="edit")["request"]

        job = pcw.process(req)

        assert job["status"] == "failed" and job["stage"] == "mode"
        assert pl.chat_thread("0001")["count"] == 0        # 没被当成问答写进历史

    def test_llm_failure_marks_job_failed(self, chat_env, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(pcw, "_chat", boom)
        req = pl.enqueue_chat("0001", "问")["request"]

        with pytest.raises(RuntimeError):
            pcw.process(req)

        saved = json.loads((pl.CHAT_JOBS / f"{req['job_id']}.json").read_text(encoding="utf-8"))
        assert saved["status"] == "failed" and "boom" in saved["error"]

    def test_empty_reply_marks_failed(self, chat_env, monkeypatch):
        monkeypatch.setattr(pcw, "_chat", lambda *a, **k: ("", None, False))
        job = pcw.process(pl.enqueue_chat("0001", "问")["request"])
        assert job["status"] == "failed" and "空内容" in job["error"]

    def test_missing_fields_rejected(self, chat_env):
        with pytest.raises(ValueError):
            pcw.process({"id": "0001", "message": "问"})
