"""复盘事实收集不得把「未确认委托」算成成交（2026-09-12 审查 MEDIUM-1），
且轮次摘要不得被 new_messages 里的非 dict 项炸穿（同日 review-p05 LOW）。

部分成交时同一笔委托在成交流水里有**两行**：fill 行（实际成交 100 股）+ pending
行（volume=整单 300 股）。`collect_facts` 原本见行就按 `r.get("volume")` 记账 →
100 股实成交被复盘写成 400 股，LLM 复盘基于假事实。untracked 行（桥缺委托号，
同样只有委托没有成交确认）一并钉住。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_post_review_facts.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import post_review as P  # noqa: E402

AGENT = "deepseek-v4-pro"
DATE = "2026-09-12"
CODE = "001312.SZ"
TS = "2026-09-12T10:30:00+08:00"


def _write_flow(tmp_path: Path, rows: list) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"live_trade_{DATE.replace('-', '')}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


def _facts(monkeypatch, tmp_path, rows) -> str:
    monkeypatch.setattr(P, "ROOT", tmp_path)
    _write_flow(tmp_path, rows)
    return P.collect_facts(AGENT, DATE)


def test_pending_row_is_not_counted_as_fill(monkeypatch, tmp_path):
    """部分成交：fill 行 100 股 + pending 行 volume=300 → 只记 100。"""
    text = _facts(monkeypatch, tmp_path, [
        {"ts": TS, "mode": "execute", "agent": AGENT, "code": CODE, "side": "sell",
         "volume": 100, "price": 17.0,
         "fill": {"order_id": "T1", "filled_price": 17.0, "filled_volume": 100}},
        {"ts": TS, "mode": "execute", "agent": AGENT, "code": CODE, "side": "sell",
         "volume": 300, "price": 15.5, "pending": True, "result": {"order_id": "T1"}},
    ])

    assert "100股" in text and "300股" not in text


def test_untracked_row_is_not_counted_as_fill(monkeypatch, tmp_path):
    """桥缺委托号的委托行没有成交确认 → 同样不算成交。"""
    text = _facts(monkeypatch, tmp_path, [
        {"ts": TS, "mode": "execute", "agent": AGENT, "code": CODE, "side": "sell",
         "volume": 300, "price": 15.5, "pending": False, "untracked": True,
         "result": {"status": "submitted"}},
    ])

    assert "无（或全部被拒单）" in text
    assert "300股" not in text


def test_confirmed_fill_row_still_counted(monkeypatch, tmp_path):
    """对照：真成交行照旧入账（过滤不能把正常成交一起吃掉）。"""
    text = _facts(monkeypatch, tmp_path, [
        {"ts": TS, "mode": "execute", "agent": AGENT, "code": CODE, "side": "sell",
         "volume": 300, "price": 17.5,
         "fill": {"order_id": "T1", "filled_price": 17.5, "filled_volume": 300}},
    ])

    assert "sell 001312.SZ 300股 @ 17.5" in text


def test_other_agents_fills_not_attributed(monkeypatch, tmp_path):
    text = _facts(monkeypatch, tmp_path, [
        {"ts": TS, "mode": "execute", "agent": "glm-5.3-flash", "code": CODE,
         "side": "sell", "volume": 300, "price": 17.5,
         "fill": {"order_id": "T9", "filled_price": 17.5, "filled_volume": 300}},
    ])

    assert "无（或全部被拒单）" in text


def _write_agent_log(tmp_path: Path, rows: list) -> None:
    d = tmp_path / "data" / "agent_data_astock" / AGENT / "log" / DATE
    d.mkdir(parents=True, exist_ok=True)
    (d / "log.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_non_dict_message_item_does_not_crash_summary(monkeypatch, tmp_path):
    """new_messages 混入非 dict 项不得炸穿整份复盘事实（2026-09-12 review-p05 LOW）。

    轮次摘要的遍历循环在读取段的 try **之外**：旧实现 `m.get("role")` 遇非 dict 项抛
    AttributeError 穿出 collect_facts → 该 agent 当日复盘整体失败。同族读方
    daily_report_agent / decision_track 已有 isinstance 守卫，此处补齐属防御一致性
    （当前唯一写入方 base_agent_astock 只产 dict）。
    """
    monkeypatch.setattr(P, "ROOT", tmp_path)
    _write_flow(tmp_path, [])
    _write_agent_log(tmp_path, [
        {"timestamp": TS, "new_messages": ["裸字符串", 123, None,
                                           {"role": "assistant", "content": "第1轮：减仓三成"}]},
        # 整个 new_messages 被写成字符串（历史脏数据的典型形态）：逐字符遍历也须安全
        {"timestamp": TS, "new_messages": "第2轮原文本"},
    ])

    text = P.collect_facts(AGENT, DATE)

    assert "第1轮：减仓三成" in text
