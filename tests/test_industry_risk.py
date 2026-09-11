"""行业风险榜（industry_risk）：赛道级**软提示**的数据与提示词接线。

口径（2026-09-11 用户）：「这些行业也要谨慎点」——某行业在长期排除清单里的
占比远高于全市场基线 = 整条赛道在沉。锁定四件事：
  - 榜单阈值（剔除率/行业总数/榜长）生效，小样本行业不上榜
  - warn_codes 只收**上榜行业**的成分股（提示词查命中只需这一个映射）
  - 产物缺失/脏/过期 → 一律 fail-open 返回空，不阻塞提示词构建
  - 提示词段落只提醒**不写硬约束**（行业是统计口径，一刀切会误伤）

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_industry_risk.py -q
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exclusion_report as er  # noqa: E402
import industry_risk as ir  # noqa: E402

TODAY = ir.datetime.now(ir.BJ).date().isoformat()


def _rows(*pairs):
    """[(code, industry)] → exclusion_report.build 的行结构（只留用到的键）。"""
    return [{"code": c, "name": "", "industry": ind, "layers": ["财务差"], "reason": ""}
            for c, ind in pairs]


def _artifact(tmp_path, items=None, warn=None, asof=TODAY, name="industry_risk.json"):
    p = tmp_path / name
    p.write_text(json.dumps({
        "asof": asof, "ts": f"{asof}T18:00:00+08:00", "rule": "测试",
        "items": [{"industry": "软件服务", "n": 3, "total": 10, "rate": 0.3}]
        if items is None else items,
        "warn_codes": {"600011": "软件服务"} if warn is None else warn,
    }, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 聚合

def test_load_industries_fail_open_without_quantdb(tmp_path, monkeypatch):
    """quantdb 不可达 → ({}, {})：报表少一列、提示词少一段，不抛异常。"""
    monkeypatch.setattr(ir, "QUANTDB", tmp_path / "nope")
    ind, totals = ir.load_industries()
    assert ind == {} and totals == Counter()


def test_caution_items_applies_thresholds():
    """行业总数 < 下限不上榜（小样本），剔除率 < 下限不上榜。"""
    rows = _rows(("600010", "软件服务"), ("600011", "软件服务"), ("600012", "软件服务"),
                 ("600013", "银行"), ("600014", "酿酒"))
    totals = Counter({"软件服务": 10, "银行": 5, "酿酒": 4})
    conf = {"industry_warn_rate_min": 0.4, "industry_warn_total_min": 10,
            "industry_warn_top_n": 5}
    assert ir.caution_items(rows, totals, conf) == []   # 3/10=30% 未达 40% 线
    got = ir.caution_items(rows, totals, {**conf, "industry_warn_rate_min": 0.3})
    # 银行 1/5=20% 低于线；酿酒 1/4=25% 低于线**且**样本 < 10 → 只有软件服务上榜
    assert got == [("软件服务", 3, 10, 0.3)]


def test_caution_items_ranks_by_rate_and_caps_top_n():
    conf = {"industry_warn_rate_min": 0.4, "industry_warn_total_min": 10,
            "industry_warn_top_n": 1}
    rows = _rows(*[(f"6000{i:02d}", "软件服务") for i in range(3)],
                 *[(f"6100{i:02d}", "航空机场") for i in range(9)])
    totals = Counter({"软件服务": 10, "航空机场": 10})
    got = ir.caution_items(rows, totals, conf)
    assert got == [("航空机场", 9, 10, 0.9)]          # 90% > 30%，且只取前 1


def test_build_writes_artifact_with_board_codes_only(tmp_path, monkeypatch):
    """warn_codes 收上榜行业的**全部**成分股（判据干净的也要提示），
    未上榜行业（银行）的票不进去。"""
    monkeypatch.setattr(ir, "load_industries", lambda: (
        {"600010": "软件服务", "600011": "软件服务", "600012": "软件服务",
         "600099": "软件服务",          # 判据干净但同属榜上行业 → 仍要提示
         "600013": "银行"},
        Counter({"软件服务": 10, "银行": 6})))
    monkeypatch.setattr(er, "build", lambda names, ind: (
        _rows(("600010", "软件服务"), ("600011", "软件服务"), ("600012", "软件服务"),
              ("600013", "银行")), []))
    out = tmp_path / "industry_risk.json"
    doc = ir.build(conf={"industry_warn_rate_min": 0.3, "industry_warn_total_min": 10,
                         "industry_warn_top_n": 5}, out=out)
    assert [it["industry"] for it in doc["items"]] == ["软件服务"]
    assert set(doc["warn_codes"]) == {"600010", "600011", "600012", "600099"}
    assert json.loads(out.read_text(encoding="utf-8"))["warn_codes"]["600099"] == "软件服务"


# ---------------------------------------------------------------- 产物读取（fail-open）

def test_load_doc_fail_open_cases(tmp_path):
    assert ir.load_doc(tmp_path / "nope.json") == {}                    # 缺失
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ir.load_doc(bad) == {}                                       # 损坏
    empty = _artifact(tmp_path, items=[], warn={}, name="empty.json")
    assert ir.load_doc(empty) == {}                                     # 没榜单 = 没得提示
    dirty = _artifact(tmp_path, items=[{"industry": "银行"}], name="dirty.json")
    assert ir.load_doc(dirty) == {}                                     # 结构不全整份退回
    stale = _artifact(tmp_path, asof="2020-01-01", name="stale.json")
    assert ir.load_doc(stale, days=7) == {}                             # 过期（> flag_days）
    ok = _artifact(tmp_path, name="ok.json")
    assert ir.load_doc(ok, days=7)["items"][0]["industry"] == "软件服务"


# ---------------------------------------------------------------- 提示词段落

def test_prompt_block_marks_pool_and_held_without_hard_ban(tmp_path):
    from live_prompt_context import industry_caution_block
    txt = industry_caution_block(["600010"], ["600011"],
                                 {"600010": "持仓甲", "600011": "候选乙"},
                                 path=_artifact(tmp_path, warn={
                                     "600010": "软件服务", "600011": "软件服务"}))
    assert "【行业整体劣化" in txt
    assert "软件服务 3/10（30%）" in txt          # 榜面陈述
    assert "候选池命中：600011 候选乙" in txt
    assert "持仓命中：600010 持仓甲" in txt
    assert "禁止" not in txt                      # 软提示：不写硬约束字眼


def test_prompt_block_caps_hits(tmp_path):
    from live_prompt_context import industry_caution_block
    codes = [f"6000{i:02d}" for i in range(15)]
    txt = industry_caution_block([], codes, {}, max_items=10,
                                 path=_artifact(tmp_path, warn={
                                     c: "软件服务" for c in codes}))
    assert txt.count("候选池命中") == 10 and "（另有 5 只命中未列）" in txt


def test_prompt_block_empty_without_artifact(tmp_path):
    """产物缺失/过期 → 整段不出（fail-open），调用方无需判空。"""
    from live_prompt_context import industry_caution_block
    assert industry_caution_block(["600010"], ["600011"], {},
                                  path=tmp_path / "nope.json") == ""
