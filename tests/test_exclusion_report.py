"""长期排除清单报表（exclusion_report）：与闸门同源，不重算判据。

锁定三件事：
  - **命中层数 == 勾选列数**（2026-09-11 修过：漏了「保壳」列，59 只的层数比
    勾选多，打开表格看起来像 bug）
  - 理由**按分句去重**（黑名单文案/基本面缓存/risk_block 合并文本三个来源会
    各带一遍全文，不去重整段重复）
  - risk_list 的合并 kind（"penny+weak"）按成分识别，不等值判
  - 行业列只是补充标签（quantdb rs_hyname），拿不到不影响清单本体；
    行业风险榜 = 剔除数 / 该行业总数，样本 <3 只不上榜

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_exclusion_report.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exclusion_report as er  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """三个产物文件都指向 tmp，互不污染真实 data/。"""
    symbols = tmp_path / "live_symbols.json"
    fund = tmp_path / "fundamental_flags.json"
    risk = tmp_path / "risk_block.json"
    monkeypatch.setattr(er, "SYMBOLS", symbols)
    monkeypatch.setattr(er, "FUND", fund)
    monkeypatch.setattr(er, "RISK", risk)
    monkeypatch.setattr(er, "ROOT", tmp_path)

    def _w(p, doc):
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    return _w, symbols, fund, risk


def test_marks_match_layer_count_and_reasons_deduped(env):
    """命中的层必须每一层都有勾（层数=8 列里的勾数），重复分句只留一份。"""
    _w, symbols, fund, risk = env
    _w(symbols, {"block_buy": ["600001.SH"]})
    _w(fund, {"asof": "2026-09-11", "items": {
        "600001": {"flags": ["fin", "trend"],
                   "reason": "连续3年亏损（2023-2025）；多年阴跌：近3年跑输大盘45%"}}})
    _w(risk, {"items": {
        # risk_list 合并 weak 后，penny 条目里混着基本面的句子（同一来源再带一遍）
        "600001": {"kind": "penny+weak",
                   "reason": "连续3年亏损（2023-2025）；多年阴跌：近3年跑输大盘45%；"
                             "股价1.80元低于2.0元预警线（逼近面值退市）"}}})

    rows, _transient = er.build({}, {})
    assert len(rows) == 1
    r = rows[0]
    assert set(r["layers"]) == {"永久黑名单", "财务差", "长期下跌", "低价股"}
    assert r["reason"].count("连续3年亏损") == 1        # 分句去重
    assert r["reason"].count("多年阴跌") == 1
    assert "股价1.80元" in r["reason"]                  # 低价层独有的句子保留

    md, csv_p = er.write(rows, [], {}, "2026-09-11")
    body = md.read_text(encoding="utf-8")
    # 表格行：| # | 代码 | 名称 | 行业 | 命中层数 | 层 | 理由 |
    assert "| 1 | 600001 |  |  | 4 | 永久黑名单/财务差/长期下跌/低价股 |" in body


def test_csv_marks_column_count_matches_layer_count(env):
    """「命中层数」列必须等于勾选列数——漏列会让表格自相矛盾。"""
    _w, symbols, fund, risk = env
    _w(symbols, {"block_buy": []})
    _w(fund, {"asof": "2026-09-11", "items": {
        "600002": {"flags": ["shell", "flat"], "reason": "保壳特征：扣非连亏2年；长期横盘：两年不动"}}})
    _w(risk, {"items": {}})
    rows, transient = er.build({}, {})
    _md, csv_p = er.write(rows, transient, {}, "2026-09-11")

    lines = csv_p.read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split(",")
    assert "保壳" in header
    cells = lines[1].split(",")[:len(header)]
    n_mark = sum(1 for c in cells if c == "✓")
    assert int(cells[header.index("命中层数")]) == n_mark == 2


def test_missing_sources_fail_open(env):
    """三个产物都不在（首启/数据故障）→ 空表，不抛异常。"""
    rows, transient = er.build({}, {})
    assert rows == [] and transient == []
    md, _csv = er.write(rows, transient, {}, "2026-09-11")
    assert "共 0 只" in md.read_text(encoding="utf-8")


def test_transient_kinds_are_split_by_component(env):
    """临时事件按 kind 成分识别（"unlock+news" 这种拼接不能漏判）。"""
    _w, _s, _f, risk = env
    _w(risk, {"items": {"600003": {"kind": "unlock+news", "reason": "解禁",
                                   "expire": "2026-09-20T00:00:00"}}})
    _rows, transient = er.build({}, {})
    assert transient and transient[0][0] == "600003"
    assert set(transient[0][1].split("+")) == {"unlock", "news"}


def test_industry_column_and_ranking(env):
    """行业是补充标签：进 CSV/md；风险榜按剔除率降序，样本 <3 只不上榜。"""
    _w, symbols, fund, risk = env
    _w(symbols, {"block_buy": []})
    _w(fund, {"asof": "2026-09-11", "items": {
        f"60001{i}": {"flags": ["illiquid"], "reason": "流动性枯竭"} for i in range(4)}})
    _w(risk, {"items": {}})
    ind = {"600010": "软件服务", "600011": "软件服务", "600012": "软件服务",
           "600013": "银行"}
    rows, transient = er.build({}, ind)
    assert {r["industry"] for r in rows} == {"软件服务", "银行"}

    totals = er.Counter({"软件服务": 10, "银行": 4})
    rank = er.industry_ranking(rows, totals)
    assert rank == [("软件服务", 3, 10, 0.3)]  # 银行只剔 1 只 → 不上榜
    assert er.industry_ranking(rows, er.Counter()) == []  # 无行业总数 → 整榜不出

    md, csv_p = er.write(rows, transient, {}, "2026-09-11", totals)
    body = md.read_text(encoding="utf-8")
    assert "行业风险榜" in body and "| 软件服务 | 3 | 10 | 30% |" in body
    rank_sec = body.split("## 行业风险榜")[1]
    assert "银行" not in rank_sec          # 榜里不含（个股表里仍有这列标签）
    assert ",软件服务," in csv_p.read_text(encoding="utf-8-sig")
