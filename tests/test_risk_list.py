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

from risk_list import (DEFAULTS, build, grave_items, load_conf, load_risk,  # noqa: E402
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
    assert it["expire"] == (ASOF + timedelta(days=3)
                            + timedelta(days=DEFAULTS["unlock_after_days"])).isoformat()


def test_unlock_beyond_horizon_not_blocked():
    assert unlock_items([_unlock_row(days=30)], ASOF, DEFAULTS) == {}


def test_unlock_small_ratio_not_blocked():
    """0.3% 流通盘解禁不构成买入级风险（阈值 1%）。"""
    assert unlock_items([_unlock_row(ratio=0.003)], ASOF, DEFAULTS) == {}


def test_unlock_after_day_still_blocked():
    """解禁日已过 2 天 → 仍拦。抛压是在解禁**当天及之后**实际兑现的
    （用户口径 2026-09-11「还有个解禁的也是超级利空」）——
    原来 expire=解禁日+1，等于抛压一落地就解除封锁，方向反了。"""
    line = _unlock_row(days=-2)
    items = unlock_items([line], ASOF, DEFAULTS)
    assert "688795" in items and "解禁" in items["688795"]["reason"]


def test_unlock_long_past_released():
    """解禁已过 unlock_after_days 天 → 释放（抛压窗口走完，不能永久拉黑）。"""
    line = _unlock_row(days=-(DEFAULTS["unlock_after_days"] + 1))
    assert unlock_items([line], ASOF, DEFAULTS) == {}


def test_unlock_expire_covers_realized_pressure():
    items = unlock_items([_unlock_row(days=3)], ASOF, DEFAULTS)
    assert items["688795"]["expire"] == (
        ASOF + timedelta(days=3 + DEFAULTS["unlock_after_days"])).isoformat()


def test_unlock_after_days_configurable():
    conf = dict(DEFAULTS, unlock_after_days=1)
    assert unlock_items([_unlock_row(days=-2)], ASOF, conf) == {}
    assert unlock_items([_unlock_row(days=-1)], ASOF, conf)


def test_unlock_past_but_recent_still_blocked():
    """已过解禁日但在兑现期内 → 仍拦（2026-09-11 口径改为双向窗口）。

    旧口径「解禁日一过风险即兑现」只对**抢跑行情**成立：解禁盘要到解禁日才
    真的可卖，抛压从那天起才落地。release 由 unlock_after_days 控制。
    """
    assert "688795" in unlock_items([_unlock_row(days=-1)], ASOF, DEFAULTS)


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


# ---------------------------------------------------------------- 重大违规（立案/造假）
# 2026-09-11 用户口径：「垃圾股、财务造假……这些股票，都黑名单」。负面新闻按
# 事件处理（3 天热度），但立案调查/财务造假是**存续状态**——调查可持续数月，
# 期间退市风险一直在；同一事件要按 grave_days 长窗口留档，且不依赖 sentiment
# （关键词本身就是信号，AI 给的 sentiment 可能是 0 或缺省）。

def _grave_line(ts="2026-09-08T09:25:00+08:00", tickers=("002674.SZ",),
                event_type="问询处罚", name="兴业科技",
                note="曾7连涨停，遭证监会立案", sentiment=-0.8):
    return {"ts": ts, "segments": {"news-micro": {"events": [
        {"tickers": list(tickers), "name": name, "event_type": event_type,
         "sentiment": sentiment, "note": note}]}}}


def test_grave_investigation_blocks_for_long_window():
    items = grave_items([_grave_line()], ASOF, DEFAULTS)
    assert "002674" in items
    it = items["002674"]
    assert it["kind"] == "grave" and "立案" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["grave_days"])).isoformat()


def test_grave_event_older_than_news_window_still_blocked():
    """5 天前的立案公告：news_items 已放过（3 天窗口），grave 仍要拦（60 天窗口）。"""
    old = (ASOF - timedelta(days=5)).isoformat() + "T10:00:00+08:00"
    line = _grave_line(ts=old)
    assert news_items([line], ASOF, DEFAULTS) == {}
    assert "002674" in grave_items([line], ASOF, DEFAULTS)


def test_grave_ignores_sentiment():
    """情绪值缺失/中性也不放过——关键词（立案/造假）本身就是信号。"""
    for sent in (0, None, 0.5):
        items = grave_items([_grave_line(sentiment=sent)], ASOF, DEFAULTS)
        assert "002674" in items, sent


def test_grave_keywords_cover_fraud_and_delisting_risk():
    for note in ("财务造假被证监会处罚", "年报虚增收入", "信息披露违法违规",
                 "实施退市风险警示"):
        items = grave_items([_grave_line(note=note)], ASOF, DEFAULTS)
        assert "002674" in items, note


def test_warning_letter_is_watch_not_grave():
    """警示函是关注级：只进 watch 提醒，不进 60 天禁买（用户 2026-09-11 口径）。"""
    from risk_list import regulatory_watch
    line = _grave_line(note="收到警示函")
    assert grave_items([line], ASOF, DEFAULTS) == {}
    assert "002674" in regulatory_watch([line], ASOF, DEFAULTS)


def test_grave_normal_news_not_caught():
    """普通负面（减持/大跌）不是重大违规，仍走 3 天新闻口径，不被 60 天窗口放大。"""
    line = _grave_line(event_type="减持", name="欧普康视",
                       note="二股东抛3%减持计划", tickers=("300595.SZ",))
    assert grave_items([line], ASOF, DEFAULTS) == {}
    assert "300595" in news_items([line], ASOF, DEFAULTS)


def test_grave_sector_wide_event_ignored():
    """涉多标的的监管类事件（板块级）不按个股拦——同 news_items 口径。"""
    line = _grave_line(tickers=("002674.SZ", "600076.SH", "605255.SH", "600309.SH"))
    assert grave_items([line], ASOF, DEFAULTS) == {}


def test_grave_days_configurable():
    conf = dict(DEFAULTS, grave_days=10)
    items = grave_items([_grave_line()], ASOF, conf)
    assert items["002674"]["expire"] == (ASOF + timedelta(days=10)).isoformat()


def test_build_merges_grave_without_shortening_by_unlock():
    """同一只票既解禁（2 天后失效）又被立案（60 天）→ 失效日取更晚的 grave。

    取更早会把立案风险提前解除——存续期风险不该被一个两天后到期的解禁条目吞掉。
    """
    line = _grave_line(tickers=("688795.SH",), name="摩尔线程",
                       note="因涉嫌信息披露违法被立案调查")
    items = build(asof=ASOF, unlock_rows=[_unlock_row(days=2)],
                  news_lines=[line], pledge_rows=[], price_rows=[], fundamental={})
    it = items["688795"]
    assert "解禁" in it["reason"] and "立案" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["grave_days"])).isoformat()


# ------------------------------------------------- 监管关注：只提醒不禁买（watch）
# 用户口径（2026-09-11）：「监管的可以提醒，里面有因子，不去拿时[再]黑名单」——
# 问询/警示这类事件的信息含量大于即期风险，进提示词当因子自评，不进买入闸门。

def _reg_line(ts="2026-09-08T09:25:00+08:00", tickers=("002674.SZ",),
              event_type="监管关注", name="兴业科技",
              note="收到深交所问询函，要求说明业绩预告修正", sentiment=-0.2):
    return {"ts": ts, "segments": {"news-micro": {"events": [
        {"tickers": list(tickers), "name": name, "event_type": event_type,
         "sentiment": sentiment, "note": note}]}}}


def test_regulatory_watch_catches_inquiry_and_warning():
    from risk_list import regulatory_watch
    watch = regulatory_watch([_reg_line()], ASOF, DEFAULTS)
    assert "002674" in watch
    it = watch["002674"]
    assert it["kind"] == "regulatory" and "问询" in it["reason"]
    assert it["until"] == (ASOF + timedelta(days=DEFAULTS["regulatory_days"])).isoformat()


def test_regulatory_warning_letter_caught():
    from risk_list import regulatory_watch
    line = _reg_line(event_type="警示函", note="收到证监会警示函")
    assert "002674" in regulatory_watch([line], ASOF, DEFAULTS)


def test_regulatory_does_not_enter_hard_block():
    """监管关注**不进**禁买清单——同一行数据只落 watch，不落 items。"""
    from risk_list import regulatory_watch
    line = _reg_line()
    assert build(asof=ASOF, unlock_rows=[], news_lines=[line], pledge_rows=[],
                 price_rows=[], fundamental={}) == {}
    assert regulatory_watch([line], ASOF, DEFAULTS)


def test_regulatory_ignores_sentiment():
    """情绪值缺省/偏中性也要提醒——判据是关键词，因子在于事件本身。"""
    from risk_list import regulatory_watch
    line = _reg_line(sentiment=None, event_type="问询函")
    assert "002674" in regulatory_watch([line], ASOF, DEFAULTS)


def test_regulatory_outside_window_silent():
    from risk_list import regulatory_watch
    old = (ASOF - timedelta(days=DEFAULTS["regulatory_days"] + 1)).isoformat()
    assert regulatory_watch([_reg_line(ts=f"{old}T09:00:00+08:00")], ASOF,
                            DEFAULTS) == {}


def test_regulatory_skips_sector_wide_event():
    from risk_list import regulatory_watch
    line = _reg_line(tickers=("002674.SZ", "600076.SH", "605255.SH", "600309.SH"))
    assert regulatory_watch([line], ASOF, DEFAULTS) == {}


def test_regulatory_days_configurable():
    from risk_list import regulatory_watch
    conf = dict(DEFAULTS, regulatory_days=5)
    watch = regulatory_watch([_reg_line()], ASOF, conf)
    assert watch["002674"]["until"] == (ASOF + timedelta(days=5)).isoformat()


def test_refresh_writes_watch_and_excludes_blocked(tmp_path):
    """watch 落盘；已在禁买清单里的代码不重复提醒（硬拦已覆盖）。"""
    from risk_list import load_watch, regulatory_watch
    grave = _grave_line(tickers=("688795.SH",), name="摩尔线程")
    reg = _reg_line(tickers=("688795.SH",), name="摩尔线程", note="收到问询函")
    reg2 = _reg_line(tickers=("600309.SH",), name="万华化学", note="收到监管函")
    out = tmp_path / "risk_block.json"
    stats = refresh(out, asof=ASOF, unlock_rows=[_unlock_row()],
                    news_lines=[grave, reg, reg2], pledge_rows=[], price_rows=[], fundamental={})
    assert stats["items"] == 1 and stats["watch"] == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "600309" in doc["watch"] and "688795" not in doc["watch"]
    assert load_watch(out, asof=ASOF) == doc["watch"]
    # regulatory_watch 自身不做去重（去重是 refresh 组装期的事）
    assert "688795" in regulatory_watch([grave, reg, reg2], ASOF, DEFAULTS)


def test_load_watch_filters_expired_and_survives_missing(tmp_path):
    from risk_list import load_watch
    assert load_watch(tmp_path / "nope.json") == {}
    f = tmp_path / "risk_block.json"
    f.write_text(json.dumps({"watch": {
        "002674": {"reason": "已过期", "kind": "regulatory", "until": "2026-09-01"},
        "600309": {"reason": "仍在窗口", "kind": "regulatory", "until": "2026-09-30"},
    }}), encoding="utf-8")
    assert list(load_watch(f, asof=ASOF)) == ["600309"]


# ---------------------------------------------------------------- 质押：只告警

def test_pledge_is_warn_only():
    from risk_list import pledge_warns
    warns = pledge_warns([{"股票代码": "600309", "质押比例": 62.0}], DEFAULTS)
    assert "600309" in warns and "质押" in warns["600309"]


def test_pledge_below_threshold_silent():
    from risk_list import pledge_warns
    assert pledge_warns([{"股票代码": "600309", "质押比例": 10.0}], DEFAULTS) == {}


# ---------------------------------------------------------------- 低价/面值退市

def _price_row(symbol="000909.SZ", close=1.83, dt="20260908"):
    return {"symbol": symbol, "close": close, "dt": dt}


def test_low_price_is_blocked():
    """面值退市是硬风险：收盘价低于预警线 → 禁买（用户口径「垃圾股……都黑名单」）。"""
    from risk_list import price_items
    it = price_items([_price_row()], ASOF, DEFAULTS)["000909"]
    assert it["kind"] == "penny" and "1.83" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["penny_days"])).isoformat()


def test_healthy_price_passes():
    from risk_list import price_items
    assert price_items([_price_row(close=12.5)], ASOF, DEFAULTS) == {}


def test_price_floor_can_be_disabled():
    from risk_list import price_items
    assert price_items([_price_row()], ASOF, {**DEFAULTS, "min_price": 0}) == {}


def test_zero_and_missing_close_not_blocked():
    """停牌/脏数据（close 0、缺失、'-'）不能当"低价"拦——那是数据缺口不是风险。"""
    from risk_list import price_items
    rows = [_price_row(close=0), _price_row(symbol="000559.SZ", close=None),
            _price_row(symbol="600519.SH", close="-"), {"close": 1.0}]
    assert price_items(rows, ASOF, DEFAULTS) == {}


def test_stale_price_snapshot_lapses():
    """数据停更超过 penny_days → 不再拦（fail-open，同解禁/新闻口径）。"""
    from risk_list import price_items
    old = (ASOF - timedelta(days=DEFAULTS["penny_days"] + 1)).strftime("%Y%m%d")
    assert price_items([_price_row(dt=old)], ASOF, DEFAULTS) == {}


def test_price_is_sticky_when_merged():
    """低价是**存续状态**（同 grave）：与短事件合并时取更晚失效日，不被提前解除。"""
    items = build(asof=ASOF, unlock_rows=[], news_lines=[_news_line()],
                  pledge_rows=[], price_rows=[_price_row(symbol="688795.SH")], fundamental={})
    it = items["688795"]
    assert "股价" in it["reason"] and "解禁跌停" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["penny_days"])).isoformat()


def test_read_price_rows_picks_latest_partition_not_future(tmp_path):
    """读取器取 dt ≤ asof 的最新分区（防未来函数）；缺目录 → []（fail-open）。"""
    import duckdb
    from risk_list import read_price_rows
    for dt, close in (("20260907", 2.5), ("20260908", 1.83), ("20260909", 1.5)):
        d = tmp_path / f"dt={dt}"
        d.mkdir()
        duckdb.connect().execute(
            f"COPY (SELECT '000909.SZ' AS symbol, {close} AS close, {dt} AS dt) "
            f"TO '{d}/data.parquet' (FORMAT PARQUET)")
    assert [str(r["dt"]) for r in read_price_rows(ASOF, root=tmp_path)] == ["20260908"]
    assert read_price_rows(ASOF, root=tmp_path / "nope") == []


def test_refresh_writes_price_rule(tmp_path):
    """落盘 → load_risk 读回：低价条目走同一条闸门（items）。"""
    out = tmp_path / "risk_block.json"
    stats = refresh(out, asof=ASOF, unlock_rows=[], news_lines=[], pledge_rows=[],
                    price_rows=[_price_row()], fundamental={})
    assert stats["items"] == 1 and "000909" in load_risk(out, asof=ASOF)


# ------------------------------------------- 基本面/长期趋势劣化（weak，缓存消费）

def _fund_doc(asof=ASOF, code="000909", reason="连续3年亏损（2023-2025）",
              flags=("fin",)):
    return {"asof": asof.isoformat(), "generated_at": asof.isoformat(),
            "items": {code: {"reason": reason, "flags": list(flags),
                             "asof": asof.isoformat()}}}


def test_fundamental_fresh_cache_blocks():
    """财务持续差/长期下跌是存续状态 → 进禁买清单（用户口径「也需要排除」）。"""
    from risk_list import fundamental_items
    it = fundamental_items(_fund_doc(), ASOF, DEFAULTS)["000909"]
    assert it["kind"] == "weak" and "连续3年亏损" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["flag_days"])).isoformat()


def test_fundamental_stale_cache_lapses():
    """缓存过期（> flag_days）→ 整份失效：陈旧结论比没有结论更危险（fail-open）。"""
    from risk_list import fundamental_items
    old = ASOF - timedelta(days=DEFAULTS["flag_days"] + 1)
    assert fundamental_items(_fund_doc(asof=old), ASOF, DEFAULTS) == {}


def test_fundamental_missing_and_dirty_sources_are_empty():
    from risk_list import fundamental_items
    assert fundamental_items({}, ASOF, DEFAULTS) == {}
    assert fundamental_items(None, ASOF, DEFAULTS) == {}
    assert fundamental_items({"asof": ASOF.isoformat(), "items": {}}, ASOF, DEFAULTS) == {}
    doc = {"asof": ASOF.isoformat(),
           "items": {"000909": {"reason": "", "flags": ["fin"]}, "BAD": {"reason": "x"}}}
    assert fundamental_items(doc, ASOF, DEFAULTS) == {}


def test_fundamental_trend_reason_kept():
    from risk_list import fundamental_items
    doc = _fund_doc(reason="长期下跌：近1年跑输大盘48%、近2年跑输63%，且现价位于年线下方",
                    flags=("trend",))
    assert "跑输大盘" in fundamental_items(doc, ASOF, DEFAULTS)["000909"]["reason"]


def test_weak_is_sticky_when_merged():
    """弱基本面是存续状态（同 grave/penny）：合并时取更晚失效日，不被提前解除。"""
    items = build(asof=ASOF, unlock_rows=[_unlock_row(code="000909", days=1)],
                  news_lines=[], pledge_rows=[], price_rows=[], fundamental=_fund_doc())
    it = items["000909"]
    assert "解禁" in it["reason"] and "连续3年亏损" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=DEFAULTS["flag_days"])).isoformat()


def test_fundamental_only_entry_survives_refresh(tmp_path):
    out = tmp_path / "risk_block.json"
    stats = refresh(out, asof=ASOF, unlock_rows=[], news_lines=[], pledge_rows=[],
                    price_rows=[], fundamental=_fund_doc())
    assert stats["items"] == 1 and "000909" in load_risk(out, asof=ASOF)


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
                    news_lines=[_news_line()], pledge_rows=[], price_rows=[], fundamental={})
    assert stats["items"] == 1 and stats["warns"] == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["asof"] == ASOF.isoformat() and "688795" in doc["items"]


def test_build_merges_unlock_and_news_for_same_code():
    """同一只股票既是解禁又是负面新闻 → 只留一条，理由合并，取更早的失效日。"""
    items = build(asof=ASOF, unlock_rows=[_unlock_row(days=2)],
                  news_lines=[_news_line()], pledge_rows=[], price_rows=[], fundamental={})
    it = items["688795"]
    assert "解禁" in it["reason"] and "解禁跌停" in it["reason"]
    assert it["expire"] == (ASOF + timedelta(days=2) + timedelta(days=1)).isoformat()


def test_build_survives_missing_sources():
    """三路数据全缺 → 空清单，不抛异常（fail-open）。"""
    assert build(asof=ASOF, unlock_rows=[], news_lines=[], pledge_rows=[],
                 price_rows=[], fundamental={}) == {}


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


# ----------------------------- 监管关注进提示词（软段，不写禁买）

def _risk_file_with_watch(tmp_path):
    f = tmp_path / "risk_block.json"
    f.write_text(json.dumps({
        "items": {"688795": {"reason": "解禁跌停", "kind": "unlock",
                             "expire": "2099-01-01"}},
        "watch": {"600309": {"reason": "监管关注（问询）：收到问询函",
                             "kind": "regulatory", "until": "2099-01-01"}},
    }), encoding="utf-8")
    return f


def test_prompt_block_has_regulatory_soft_tier(tmp_path):
    """监管关注要**提醒**模型（当因子），但那一行不写「禁止买入」硬约束。"""
    from live_prompt_context import risk_warning_block
    txt = risk_warning_block(["688795.SH"], ["600309.SH"], path=_risk_file_with_watch(tmp_path))
    assert "【监管关注" in txt and "只提醒不禁买" in txt
    soft = txt.split("【监管关注")[1]
    assert "600309.SH" in soft and "问询" in soft
    assert "禁止" not in soft


def test_prompt_block_watch_only_still_renders(tmp_path):
    """只有监管关注、没有硬拦命中 → 仍要给提示，但不能虚报硬约束段。"""
    from live_prompt_context import risk_warning_block
    txt = risk_warning_block([], ["600309.SH"], path=_risk_file_with_watch(tmp_path))
    assert "【监管关注" in txt
    assert "事件风险警示" not in txt


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
