"""事件风险清单（risk_list）：解禁 / 负面新闻 → 买入硬拦 + 持仓告警。

背景（2026-09-08 用户口径）：「A股很多有风险的股票比如解禁这种就是要告警跑的、
负面新闻股票也要避坑掉。模型有的话需要毙掉」——此前解禁/质押只作为提示词标签，
模型照买不误，没有任何硬拦。本测试锁定：
  - 解禁窗口内、比例够大 → 拦；窗口外 / 比例过小 / 已过解禁日 → 放行
  - 负面新闻（sentiment 低于阈值）→ 拦；正面 / 板块级 / 过窗口 → 放行
  - 质押只告警不拦买（慢性状态，非事件）
  - 数据缺失 → 空清单（fail-open，不能因数据故障停掉全天买入）
  - 过期条目在读取时再次过滤（文件可能滞后）

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_risk_list.py -q
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from risk_list import (DEFAULTS, build, load_conf, load_risk,  # noqa: E402
                       news_items, refresh, unlock_items)

ASOF = date(2026, 9, 8)


def _unlock_row(code="688795", days=3, ratio=0.034, kind="首发原股东限售股份"):
    return {"股票代码": code, "股票简称": "摩尔线程",
            "解禁时间": ASOF + timedelta(days=days),
            "限售股类型": kind, "占解禁前流通市值比例": ratio}


def _news_line(ts="2026-09-08T09:55:00+08:00", tickers=("688795.SH",),
               sentiment=-0.8, event_type="解禁跌停", name="摩尔线程",
               note="2577.451万股解禁，市值跌破2000亿"):
    return {"ts": ts, "segments": {"news-micro": {"events": [
        {"tickers": list(tickers), "name": name, "event_type": event_type,
         "sentiment": sentiment, "note": note}]}}}


# ---------------------------------------------------------------- 解禁

def test_unlock_within_window_and_ratio_is_blocked():
    items = unlock_items([_unlock_row()], ASOF, DEFAULTS)
    assert "688795" in items
    it = items["688795"]
    assert it["kind"] == "unlock" and "解禁" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=3) + timedelta(days=1)).isoformat()


def test_unlock_beyond_horizon_not_blocked():
    assert unlock_items([_unlock_row(days=30)], ASOF, DEFAULTS) == {}


def test_unlock_small_ratio_not_blocked():
    """0.3% 流通盘解禁不构成买入级风险（阈值 1%）。"""
    assert unlock_items([_unlock_row(ratio=0.003)], ASOF, DEFAULTS) == {}


def test_unlock_past_date_not_blocked():
    """已过解禁日 → 风险已兑现，不再拦买（否则会永久拉黑）。"""
    assert unlock_items([_unlock_row(days=-1)], ASOF, DEFAULTS) == {}


def test_unlock_bad_rows_do_not_crash():
    rows = [{"股票代码": None}, {"股票代码": "600309", "解禁时间": "坏日期"},
            {"股票代码": "600309", "解禁时间": ASOF + timedelta(days=2),
             "占解禁前流通市值比例": "脏值"}]
    assert isinstance(unlock_items(rows, ASOF, DEFAULTS), dict)


# ---------------------------------------------------------------- 负面新闻

def test_negative_news_blocks_ticker():
    items = news_items([_news_line()], ASOF, DEFAULTS)
    assert "688795" in items
    assert items["688795"]["kind"] == "news" and "解禁跌停" in items["688795"]["reason"]


def test_positive_news_ignored():
    assert news_items([_news_line(sentiment=0.8)], ASOF, DEFAULTS) == {}


def test_sector_wide_news_ignored():
    """涉及标的数超上限 → 板块级事件，不是个股负面，不拦（否则一票板块全禁）。"""
    line = _news_line(tickers=("600309.SH", "600426.SH", "600426.SZ", "601899.SH"))
    assert news_items([line], ASOF, DEFAULTS) == {}


def test_old_news_ignored():
    old = (ASOF - timedelta(days=5)).isoformat() + "T10:00:00+08:00"
    assert news_items([_news_line(ts=old)], ASOF, DEFAULTS) == {}


def test_news_expires_after_window():
    items = news_items([_news_line()], ASOF, DEFAULTS)
    assert items["688795"]["expire"] == (ASOF + timedelta(days=3)).isoformat()


# ---------------------------------------------------------------- 质押：只告警

def test_pledge_is_warn_only():
    from risk_list import pledge_warns
    warns = pledge_warns([{"股票代码": "600309", "质押比例": 62.0}], DEFAULTS)
    assert "600309" in warns and "质押" in warns["600309"]


def test_pledge_below_threshold_silent():
    from risk_list import pledge_warns
    assert pledge_warns([{"股票代码": "600309", "质押比例": 10.0}], DEFAULTS) == {}


# ---------------------------------------------------------------- 读取 / 落盘

def test_load_risk_missing_file_is_empty(tmp_path):
    assert load_risk(tmp_path / "nope.json") == {}


def test_load_risk_corrupt_file_is_empty(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    assert load_risk(f) == {}


def test_load_risk_filters_expired_items(tmp_path):
    f = tmp_path / "risk_block.json"
    f.write_text(json.dumps({"items": {
        "688795": {"reason": "已过期", "kind": "unlock", "expire": "2026-09-01"},
        "600309": {"reason": "仍在窗口", "kind": "news", "expire": "2026-09-30"},
    }}), encoding="utf-8")
    out = load_risk(f, asof=ASOF)
    assert list(out) == ["600309"]


def test_refresh_writes_and_roundtrips(tmp_path):
    out = tmp_path / "risk_block.json"
    stats = refresh(out, asof=ASOF, unlock_rows=[_unlock_row()],
                    news_lines=[_news_line()], pledge_rows=[])
    assert stats["items"] == 1 and stats["warns"] == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["asof"] == ASOF.isoformat() and "688795" in doc["items"]


def test_build_merges_unlock_and_news_for_same_code():
    """同一只股票既是解禁又是负面新闻 → 只留一条，理由合并，取更早的失效日。"""
    items = build(asof=ASOF, unlock_rows=[_unlock_row(days=2)],
                  news_lines=[_news_line()], pledge_rows=[])
    it = items["688795"]
    assert "解禁" in it["reason"] and "解禁跌停" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=2) + timedelta(days=1)).isoformat()


def test_build_survives_missing_sources():
    """三路数据全缺 → 空清单，不抛异常（fail-open）。"""
    assert build(asof=ASOF, unlock_rows=[], news_lines=[], pledge_rows=[]) == {}


def test_load_conf_defaults_and_override(tmp_path):
    f = tmp_path / "live_symbols.json"
    f.write_text(json.dumps({"risk": {"unlock_days": 3, "news_sentiment_max": -0.9}}),
                 encoding="utf-8")
    cfg = load_conf(f)
    assert cfg["unlock_days"] == 3 and cfg["news_sentiment_max"] == -0.9
    assert cfg["unlock_ratio_min"] == DEFAULTS["unlock_ratio_min"]


def test_load_conf_bad_types_fall_back(tmp_path):
    f = tmp_path / "live_symbols.json"
    f.write_text(json.dumps({"risk": {"unlock_days": "十天"}}), encoding="utf-8")
    assert load_conf(f)["unlock_days"] == DEFAULTS["unlock_days"]


# ---------------------------------------------------------------- 提示词注入

def _risk_file(tmp_path):
    f = tmp_path / "risk_block.json"
    f.write_text(json.dumps({"items": {
        "688795": {"reason": "解禁跌停", "kind": "news", "expire": "2099-01-01"},
        "601799": {"reason": "09/12解禁5.0%流通盘", "kind": "unlock", "expire": "2099-01-01"},
    }}), encoding="utf-8")
    return f


def test_prompt_block_marks_held_vs_pool(tmp_path):
    from live_prompt_context import risk_warning_block
    txt = risk_warning_block(["688795.SH"], ["601799.SH"],
                             {"688795.SH": "摩尔线程", "601799.SH": "星宇股份"},
                             path=_risk_file(tmp_path))
    assert "事件风险警示" in txt
    assert "持仓命中" in txt and "禁止加仓" in txt
    assert "候选命中" in txt and "禁止买入" in txt


def test_prompt_block_empty_when_no_hit(tmp_path):
    from live_prompt_context import risk_warning_block
    assert risk_warning_block(["600309.SH"], ["001312.SZ"],
                              path=_risk_file(tmp_path)) == ""


def test_prompt_block_survives_missing_file(tmp_path):
    from live_prompt_context import risk_warning_block
    assert risk_warning_block(["688795.SH"], path=tmp_path / "nope.json") == ""


def test_prompt_block_matches_without_suffix(tmp_path):
    """池内代码无后缀（6 位）也要能命中——数据源后缀口径不统一。"""
    from live_prompt_context import risk_warning_block
    assert "688795" in risk_warning_block(["688795"], path=_risk_file(tmp_path))


# ---- 夜间候选池过滤（night_pool_agent.filter_risk_candidates）--------------
# 用户口径「模型有的话需要毙掉」：风险标的在**候选池生成阶段**就剔除，
# 模型看不见就不会选，比"选了再被买入闸门毙掉"少一轮无效决策。
# 闸门仍独立拦一次（夜间过滤失效时兜底）。

def _cands():
    return [{"code": "600309.SH", "name": "万华化学"},
            {"code": "688795.SH", "name": "摩尔线程"},
            {"code": "001312.SZ", "name": "福恩股份"}]


def _risk_map():
    return {"688795": {"reason": "解禁窗口内（09-11 解禁 3.4%）", "expire": "2026-09-11"}}


def test_filter_risk_drops_hit_and_keeps_rest():
    from night_pool_agent import filter_risk_candidates
    kept, dropped = filter_risk_candidates(_cands(), _risk_map())
    assert [c["code"] for c in kept] == ["600309.SH", "001312.SZ"]
    assert len(dropped) == 1
    c, hit = dropped[0]
    assert c["code"] == "688795.SH"
    assert "解禁" in hit["reason"]


def test_filter_risk_matches_bare_six_digit_code():
    """候选代码无后缀（6 位）也要命中——数据源后缀口径不统一。"""
    from night_pool_agent import filter_risk_candidates
    kept, dropped = filter_risk_candidates(
        [{"code": "688795", "name": "摩尔线程"}], _risk_map())
    assert kept == [] and len(dropped) == 1


def test_filter_risk_empty_risk_keeps_all():
    from night_pool_agent import filter_risk_candidates
    kept, dropped = filter_risk_candidates(_cands(), {})
    assert len(kept) == 3 and dropped == []


def test_filter_risk_handles_empty_and_bad_input():
    from night_pool_agent import filter_risk_candidates
    assert filter_risk_candidates([], _risk_map()) == ([], [])
    assert filter_risk_candidates(None, None) == ([], [])
    kept, dropped = filter_risk_candidates([{"name": "无代码"}], _risk_map())
    assert len(kept) == 1 and dropped == []


def test_filter_risk_does_not_mutate_input():
    from night_pool_agent import filter_risk_candidates
    cands = _cands()
    filter_risk_candidates(cands, _risk_map())
    assert len(cands) == 3
