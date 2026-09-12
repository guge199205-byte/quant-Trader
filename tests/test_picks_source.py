"""候选池取用与新鲜度（2026-09-12 P1-3）。

事故形态：池链路两处各自 `sorted(glob("*_picks.json"))[-1]` 裸取字典序最后 →
①`{d}_picks.json`（quantmind 盘后池，按**数据会话日**命名）与
②`{d}_agent_picks.json`（晚间研究池，P1-4 后按**目标交易日**命名）混放时
选择规则不明；②最新文件整份缺失（如 20260911 无 agent 池）无人知晓；
③池到底多旧（滞后几个自然日）没有任何地方标注。

本模块钉住 picks_source 的判据：
  - 文件名解析（两类池 + 拒收 backtest/杂名）；
  - 按**日期**取最新，同日并列 → agent（晚间研究，更晚产出）优先；
  - 新鲜度基准按池类型给：agent 池应等于「应服务交易日」（今天/下一交易日），
    quantmind 池应等于「最近已收盘会话」（其命名口径就是会话日）；
  - 缺失/读不出/空池三种形态的说明文案（空池属设计内，不看齐缺失）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_picks_source.py -q
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_fills as F                # noqa: E402
import picks_source as P              # noqa: E402


def _write(base: Path, name: str, doc) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    p = base / name
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False),
                 encoding="utf-8")
    return p


AGENT_DOC = {"date": "20260911", "session": "20260910",
             "market_direction": {"direction": "震荡"},
             "picks": [{"code": "600817.SH", "name": "上工申贝", "side": "HOLD", "score": 9.0}]}
QM_DOC = {"date": "20260910", "candidates": [{"symbol": "600519", "name": "贵州茅台",
                                              "side": "BUY", "score": 0.9}]}


# ---------- 文件名解析 ----------

def test_parse_picks_name_two_kinds():
    assert P.parse_picks_name("20260911_picks.json") == ("20260911", P.KIND_QUANTMIND)
    assert P.parse_picks_name("20260910_agent_picks.json") == ("20260910", P.KIND_AGENT)


@pytest.mark.parametrize("name", [
    "backtest_20260105_20260820.json",   # 回测产物，无 _picks 后缀
    "20260821_picks_news.md",            # 资讯附属文件
    "20260911_picks.json.bak",           # 备份
    "2026091_picks.json",                # 7 位日期
    "20260911_x_picks.json",             # 中间多了一段
    "20260911_picks.json.tmp",           # 半写文件
])
def test_parse_picks_name_rejects_junk(name):
    assert P.parse_picks_name(name) is None


# ---------- 取最新 ----------

def test_latest_picks_picks_by_date_not_lexicographic(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260821_picks.json", QM_DOC)
    _write(tmp_path, "20260910_agent_picks.json", AGENT_DOC)
    _write(tmp_path, "20260911_picks.json", QM_DOC)
    path, d8, doc, kind = P.latest_picks()
    assert path.name == "20260911_picks.json" and d8 == "20260911"
    assert kind == P.KIND_QUANTMIND and doc["date"] == "20260910"


def test_latest_picks_prefers_agent_pool_on_same_date(tmp_path, monkeypatch):
    """同日两类池都在 → 晚间研究池（更晚产出、含新闻管线结论）优先。"""
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260910_picks.json", QM_DOC)
    _write(tmp_path, "20260910_agent_picks.json", AGENT_DOC)
    _, d8, _, kind = P.latest_picks()
    assert (d8, kind) == ("20260910", P.KIND_AGENT)


def test_latest_picks_none_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    assert P.latest_picks() is None


def test_latest_picks_unreadable_doc_is_not_silently_skipped(tmp_path, monkeypatch):
    """最新一期坏掉 → 不许静默回退旧池（那正是"静默取旧"事故形态），doc=None 上报。"""
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260909_picks.json", QM_DOC)
    _write(tmp_path, "20260910_picks.json", "{坏 JSON")
    path, d8, doc, kind = P.latest_picks()
    assert path.name == "20260910_picks.json" and doc is None


# ---------- 新鲜度基准（按池类型） ----------

def test_expected_label_agent_pool_is_target_trading_day():
    assert P.expected_label(P.KIND_AGENT, date(2026, 9, 11)) == "20260911"   # 周五盘中
    assert P.expected_label(P.KIND_AGENT, date(2026, 9, 12)) == "20260914"   # 周六 → 下周一
    assert P.expected_label(P.KIND_AGENT, date(2026, 10, 1)) == "20261008"   # 国庆假期 → 复市日


def test_expected_label_quantmind_pool_is_last_closed_session():
    assert P.expected_label(P.KIND_QUANTMIND, date(2026, 9, 11)) == "20260910"  # 周五盘中 → 周四收盘
    assert P.expected_label(P.KIND_QUANTMIND, date(2026, 9, 12)) == "20260911"  # 周六 → 周五收盘
    assert P.expected_label(P.KIND_QUANTMIND, date(2026, 10, 1)) == "20260930"  # 假期 → 节前最后收盘


def test_freshness_note_silent_when_fresh_and_flags_lag():
    assert P.freshness_note("20260911", P.KIND_AGENT, date(2026, 9, 11)) == ""
    assert P.freshness_note("20260910", P.KIND_QUANTMIND, date(2026, 9, 11)) == ""
    note = P.freshness_note("20260910", P.KIND_AGENT, date(2026, 9, 11))
    assert "滞后 1 个自然日" in note and "20260910" in note
    # 未来日期（提前产出/时钟错位）不按滞后报
    assert P.freshness_note("20260914", P.KIND_AGENT, date(2026, 9, 11)) == ""


# ---------- 消费元数据 ----------

def test_pool_meta_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    meta = P.pool_meta(P.latest_picks(), today=date(2026, 9, 11))
    assert meta["missing"] is True and meta["empty"] is True
    assert "缺失" in meta["note"]


def test_pool_meta_unreadable_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260910_picks.json", "{坏 JSON")
    meta = P.pool_meta(P.latest_picks(), today=date(2026, 9, 11))
    assert meta["missing"] is False and meta["unreadable"] is True
    assert "解析" in meta["note"] and "20260910_picks.json" in meta["note"]


def test_pool_meta_empty_pool_carries_gate_reason(tmp_path, monkeypatch):
    """空池=设计内（复盘判空仓），不告警，但要把池文件里的 gate 原因带出来。"""
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260911_picks.json",
           {"date": "20260911", "gate": "复盘判强烈看空（-3.95/11）→ 清空选股",
            "candidates": []})
    meta = P.pool_meta(P.latest_picks(), today=date(2026, 9, 11))
    assert meta["empty"] is True and meta["unreadable"] is False and meta["missing"] is False
    assert "强烈看空" in meta["note"] and "设计内" in meta["note"]


def test_pool_meta_normal_carries_file_date_and_stale_days(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260910_agent_picks.json", AGENT_DOC)
    meta = P.pool_meta(P.latest_picks(), today=date(2026, 9, 11))
    assert (meta["file"], meta["date"], meta["kind"]) == ("20260910_agent_picks.json",
                                                          "20260910", P.KIND_AGENT)
    assert meta["stale_days"] == 1 and "滞后" in meta["note"] and meta["empty"] is False


# ---------- 缺失告警（事件面） ----------

def test_note_missing_pool_records_alert_event():
    P.note_missing_pool()
    doc = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    hits = [v for v in doc.values() if v.get("kind") == "pool_missing"]
    assert len(hits) == 1 and hits[0]["alert"] is True
    assert "缺失" in hits[0]["msg"]


def test_note_missing_pool_dedupes_within_day():
    P.note_missing_pool()
    P.note_missing_pool()
    doc = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    assert len([v for v in doc.values() if v.get("kind") == "pool_missing"]) == 1


def test_note_missing_pool_carries_custom_reason():
    """读不出（unreadable）与整份缺失同属「无池可用」，但文案要能区分。"""
    P.note_missing_pool("⚠️ 候选池文件无法解析：20260910_picks.json——本轮无池可用")
    doc = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    hits = [v for v in doc.values() if v.get("kind") == "pool_missing"]
    assert len(hits) == 1 and "无法解析" in hits[0]["msg"]


# ---------- 提示词/log 行 ----------

def test_prompt_line_names_file_date_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260910_agent_picks.json", AGENT_DOC)
    line = P.prompt_line(P.pool_meta(P.latest_picks(), today=date(2026, 9, 10)))
    assert "20260910_agent_picks.json" in line and "20260910" in line
    assert "晚间研究池" in line


def test_prompt_line_quantmind_pool_and_stale_note(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    _write(tmp_path, "20260909_picks.json", QM_DOC)
    line = P.prompt_line(P.pool_meta(P.latest_picks(), today=date(2026, 9, 11)))
    assert "盘后研究池" in line and "20260909" in line
    assert "滞后" in line                     # 新鲜度说明随行进提示词


def test_prompt_line_missing_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PICKS_DIR", tmp_path)
    missing = P.prompt_line(P.pool_meta(None, today=date(2026, 9, 11)))
    assert "缺失" in missing

    _write(tmp_path, "20260911_picks.json",
           {"date": "20260911", "gate": "复盘判强烈看空（-3.95/11）→ 清空选股",
            "candidates": []})
    empty = P.prompt_line(P.pool_meta(P.latest_picks(), today=date(2026, 9, 11)))
    assert "为空" in empty and "强烈看空" in empty


# ---------- 接线：select_from_reports 取池 ----------

def test_select_latest_picks_file_delegates_to_picks_source(tmp_path, monkeypatch):
    import select_from_reports as S

    monkeypatch.setattr(S, "REPORTS", tmp_path)
    _write(tmp_path / "stock_picks", "20260909_picks.json", QM_DOC)
    _write(tmp_path / "stock_picks", "20260910_agent_picks.json", AGENT_DOC)
    assert S.latest_picks_file().name == "20260910_agent_picks.json"

    # 目录里一个池都没有 → None（调用方自己决定报错文案）
    monkeypatch.setattr(S, "REPORTS", tmp_path / "nowhere")
    assert S.latest_picks_file() is None


def test_select_date_lookup_prefers_agent_then_falls_back(tmp_path, monkeypatch):
    import select_from_reports as S

    monkeypatch.setattr(S, "REPORTS", tmp_path)
    _write(tmp_path / "stock_picks", "20260910_picks.json", QM_DOC)
    assert S.picks_file_for_date("20260910").name == "20260910_picks.json"
    _write(tmp_path / "stock_picks", "20260910_agent_picks.json", AGENT_DOC)
    assert S.picks_file_for_date("20260910").name == "20260910_agent_picks.json"
    # 两个都没有 → 回退约定的盘后池名（由调用方判 exists）
    assert S.picks_file_for_date("20260915").name == "20260915_picks.json"
