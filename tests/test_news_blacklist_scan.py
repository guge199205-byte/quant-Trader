"""新闻库黑名单扫描（news_blacklist_scan）：从 RSS 全量里捞「坏公司」代码清单。

背景（2026-09-11 用户口径）：「从今年的 rss 新闻里面找代码，那些黑名单公司、
不好的公司，都给列出来」。

这份测试主要锁**降噪**——扫描工具的输出是人工复核清单，误报多到一定程度就没人看了。
三类误报是实测踩出来的：
  1. 媒体名 = 上市公司名（东方财富/同花顺/新华网）：它们是 RSS 源，名号出现在
     每篇稿子里 → 必须按"出处"洗掉
  2. Google News 的 description 是「相关报道合集」，每条都带自己的出处 →
     名称只认标题，不认 description
  3. 公司名与关键词离得远（行业综述里的"均在列"）→ 用就近判据降权

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_news_blacklist_scan.py -q
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from news_blacklist_scan import (_near, build_name_re, load_source_tokens,  # noqa: E402
                                 match_names, sanitize, scan)

IDX = {"东方财富": ["300059.SZ"], "同花顺": ["300033.SZ"], "萃华珠宝": ["002731.SZ"],
       "神剑股份": ["002361.SZ"], "*ST萃华": ["002731.SZ"]}


def _db(tmp_path, rows, connectors=(), sites=()):
    """最小 Huntly 快照：page + connector + source 三张表（扫描只碰这三张）。"""
    p = tmp_path / "snap.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE page (title TEXT, description TEXT, connected_at TEXT)")
    con.execute("CREATE TABLE connector (name TEXT)")
    con.execute("CREATE TABLE source (site_name TEXT)")
    con.executemany("INSERT INTO page VALUES (?,?,?)", rows)
    con.executemany("INSERT INTO connector VALUES (?)", [(n,) for n in connectors])
    con.executemany("INSERT INTO source VALUES (?)", [(n,) for n in sites])
    con.commit()
    con.close()
    return str(p)


# ---------------------------------------------------------------- 出处清洗

def test_sanitize_strips_trailing_source_suffix():
    assert sanitize("神剑股份遭立案 - 东方财富", {"东方财富"}) == "神剑股份遭立案"


def test_sanitize_removes_source_tokens_inline():
    assert "东方财富" not in sanitize("东方财富 神剑股份遭立案", {"东方财富"})


def test_sanitize_keeps_untouched_text():
    assert sanitize("神剑股份遭立案", {"东方财富"}) == "神剑股份遭立案"


def test_load_source_tokens_splits_connector_and_site_names(tmp_path):
    db = _db(tmp_path, [], connectors=["东方财富网-行业研报"], sites=["新浪财经"])
    toks = load_source_tokens(db)
    assert "东方财富网" in toks and "行业研报" in toks and "新浪财经" in toks


def test_load_source_tokens_drops_generic_words(tmp_path):
    db = _db(tmp_path, [], connectors=["新闻"], sites=["财经"])
    assert load_source_tokens(db) == set()


# ---------------------------------------------------------------- 名称匹配

def test_match_names_prefers_longest():
    """「*ST萃华」与「萃华珠宝」并存时不要把同一只票重复计入。"""
    got = match_names("百年萃华珠宝正式退市", build_name_re(IDX), IDX)
    assert got == ["002731.SZ"]


def test_match_names_multiple_companies():
    got = match_names("神剑股份、东方财富双双公告", build_name_re(IDX), IDX)
    assert set(got) == {"002361.SZ", "300059.SZ"}


# ---------------------------------------------------------------- 就近判据

def test_near_true_when_adjacent():
    assert _near("神剑股份遭证监会立案", "神剑股份", "立案")


def test_near_false_when_far_apart():
    """行业综述：公司名与关键词隔着几百字 → 不算当事方。"""
    text = "神剑股份发布年报" + "。" * 200 + "另有公司遭立案"
    assert not _near(text, "神剑股份", "立案")


# ---------------------------------------------------------------- 端到端扫描

def test_scan_finds_company_in_headline(tmp_path):
    db = _db(tmp_path, [("神剑股份遭证监会立案", "详情略", "2026-09-08 09:00:00.000")])
    out = scan("2026-01-01", "2026-09-11", IDX, db=db)
    assert "002361.SZ" in out
    assert "立案" in out["002361.SZ"]["cats"]
    assert out["002361.SZ"]["near_n"] == 1


def test_scan_ignores_source_name_only(tmp_path):
    """回归：媒体名（东方财富）出现在标题后缀 → 不能把东方财富捞成黑名单。"""
    db = _db(tmp_path,
             [("两市成交额放量 - 东方财富", "东方财富讯", "2026-09-08 09:00:00.000")],
             connectors=["东方财富网-行业研报"])
    out = scan("2026-01-01", "2026-09-11", IDX, db=db)
    assert "300059.SZ" not in out


def test_scan_ignores_company_only_in_description(tmp_path):
    """回归：Google News 的 description 是相关报道合集，不能当正文匹配。"""
    db = _db(tmp_path,
             [("两市成交额放量", "相关：东方财富 立案调查", "2026-09-08 09:00:00.000")])
    assert "300059.SZ" not in scan("2026-01-01", "2026-09-11", IDX, db=db)


def test_scan_dedupes_repeated_headline(tmp_path):
    """同一头条被多家源转载 → 只计一次，否则条数会把榜单带偏。"""
    row = ("神剑股份遭证监会立案", "略", "2026-09-08 09:00:00.000")
    db = _db(tmp_path, [row, row, row])
    assert scan("2026-01-01", "2026-09-11", IDX, db=db)["002361.SZ"]["n"] == 1


def test_scan_respects_date_window(tmp_path):
    db = _db(tmp_path, [("神剑股份遭证监会立案", "略", "2025-06-01 09:00:00.000")])
    assert scan("2026-01-01", "2026-09-11", IDX, db=db) == {}


def test_scan_requires_keyword(tmp_path):
    """只有公司名、没有负面关键词的稿子不进榜（这不是黑名单，是全部新闻）。"""
    db = _db(tmp_path, [("神剑股份发布年度分红方案", "略", "2026-09-08 09:00:00.000")])
    assert scan("2026-01-01", "2026-09-11", IDX, db=db) == {}


def test_scan_survives_empty_db(tmp_path):
    assert scan("2026-01-01", "2026-09-11", IDX, db=_db(tmp_path, [])) == {}


def test_scan_keeps_company_with_distinct_name_from_source(tmp_path):
    """交易违规类：内幕交易在标题里、公司名相邻 → 正常进榜。"""
    db = _db(tmp_path, [("神剑股份副总经理因涉嫌内幕交易被立案",
                         "略", "2026-09-08 09:00:00.000")])
    out = scan("2026-01-01", "2026-09-11", IDX, db=db)
    assert {"立案", "交易违规"} <= out["002361.SZ"]["cats"]
