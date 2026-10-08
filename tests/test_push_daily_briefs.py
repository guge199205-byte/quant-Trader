"""push_daily_briefs（每日三时段推送）单元测试：纯解析逻辑，无网络。

覆盖：候选池解析、复盘一句话提取、早盘/晚间消息组装容错（产物缺失不崩）。
"""
from __future__ import annotations

import json
from pathlib import Path

import push_daily_briefs as pb


def _write(tmp_path, rel, data):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_latest_picks_picks_most_recent(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260911_picks.json", {"date": "20260911", "picks": []})
    _write(tmp_path, "20260914_agent_picks.json",
           {"date": "20260914", "picks": [{"code": "A", "name": "甲"}]})
    assert pb.latest_picks()["date"] == "20260914"


def test_latest_picks_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path / "nope")
    assert pb.latest_picks() is None


def test_cmd_evening_assembly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260914_agent_picks.json", {
        "date": "20260914",
        "market_direction": {"direction": "外围偏空，仓位防守"},
        "picks": [{"code": f"00{i}000.SZ", "name": f"股{i}", "score": 30 - i,
                   "reason": "理由" * 50} for i in range(12)]})
    sent = []
    monkeypatch.setattr(pb, "notify", lambda t, c: sent.append((t, c)))
    assert pb.cmd_evening() == 0
    title, content = sent[0]
    assert "明日候选 · 20260914" in title
    assert "大盘方向" in content
    assert "1. **股0**(**000000.SZ**)" in content
    assert "11." not in content          # 只推 Top10
    assert len(content) < 1200


def test_cmd_evening_silent_when_no_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path / "empty")
    sent = []
    monkeypatch.setattr(pb, "notify", lambda t, c: sent.append((t, c)))
    assert pb.cmd_evening() == 0
    assert sent == []


def test_cmd_evening_alerts_when_pool_empty(monkeypatch, tmp_path):
    # 2026-09-15 实录：研究 agent 失败落空池——必须出声，不能静默
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260915_agent_picks.json", {
        "date": "20260915",
        "market_direction": {"direction": "（研究 agent 失败：dsh rc=1 STREAM_CLOSED）"},
        "picks": []})
    sent = []
    monkeypatch.setattr(pb, "notify", lambda t, c: sent.append((t, c)))
    assert pb.cmd_evening() == 0
    assert len(sent) == 1
    title, content = sent[0]
    assert "晚间选股无候选" in title
    assert "STREAM_CLOSED" in content
    assert "不开新仓" in content


def test_review_agents_extracts_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "REVIEW_DIR", tmp_path)
    agent_dir = tmp_path / "deepseek-v4-pro"
    agent_dir.mkdir()
    (agent_dir / "2026-09-14.md").write_text(
        "# 盘后复盘\n\n## ① 一句话总评\n**弱市持有日，方向没看错，执行留痕要改**。\n\n"
        "## ② 拆解\n...\n", encoding="utf-8")
    rows = pb._review_agents("2026-09-14")
    assert rows[0][0] == "deepseek-v4-pro"
    assert "执行留痕要改" in rows[0][1]


def test_review_agents_handles_varying_heading_formats(monkeypatch, tmp_path):
    # 2026-09-14 实录：pro/flash/glm 三家标题格式各不相同
    monkeypatch.setattr(pb, "REVIEW_DIR", tmp_path)
    for agent, head in [("deepseek-v4-pro", "### 一句话总评"),
                        ("deepseek-v4-flash", "## 一、一句话总评"),
                        ("glm-5.3-flash", "## 一句话总评")]:
        d = tmp_path / agent
        d.mkdir()
        (d / "2026-09-14.md").write_text(
            f"# 复盘\n\n{head}\n{agent}的总结句。\n\n## 二、其他\n...\n",
            encoding="utf-8")
    rows = pb._review_agents("2026-09-14")
    assert len(rows) == 3
    for agent, summary in rows:
        assert summary.endswith("的总结句。")


def test_review_agents_skips_missing_date(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "REVIEW_DIR", tmp_path)
    assert pb._review_agents("2026-09-14") == []


def test_cmd_postreview_silent_when_no_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "REVIEW_DIR", tmp_path / "none")
    sent = []
    monkeypatch.setattr(pb, "notify", lambda t, c: sent.append((t, c)))
    assert pb.cmd_postreview() == 0
    assert sent == []


def test_cmd_morning_assembly(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260914_agent_picks.json",
           {"date": "20260914", "picks": [{"code": "A", "name": "甲"}]})
    monkeypatch.setattr(pb, "ROOT", tmp_path)
    _write(tmp_path, "configs/risk_budget.json",
           {"label": "谨慎", "budget": {"leverage_max": 1.2, "per_stock_pct": 0.15,
                                        "max_new_buys": 2}})
    _write(tmp_path, "data/news_brief/latest.json",
           {"macro": ["美联储加息预期升温压制成长"], "themes": ["机器人主线走强"]})
    _write(tmp_path, "logs/premarket_probe_state.json", {"down": False})
    sent = []
    monkeypatch.setattr(pb, "notify", lambda t, c: sent.append((t, c)))
    assert pb.cmd_morning() == 0
    title, content = sent[0]
    assert "早盘简报" in title
    assert "风控档位：**谨慎**" in content
    assert "宏观" in content and "美联储" in content
    assert "候选池" in content and "**甲**" in content
    assert "Windows/桥：**在线 ✓**" in content