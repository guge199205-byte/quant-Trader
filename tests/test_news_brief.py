"""新闻 Agent 管线纯逻辑单测：窗口/游标、源分层、聚类去重、JSON 容错解析、
brief 渲染/字段、watch codes 合并。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_news_brief.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import news_brief as N  # noqa: E402
import live_hourly_analysis as L  # noqa: E402


def test_parse_llm_json_fence_and_prose():
    txt = '好的，分析如下：\n```json\n{"bias": 0.3, "ok": true}\n```\n完'
    assert N.parse_llm_json(txt) == {"bias": 0.3, "ok": True}
    assert N.parse_llm_json('直接 {"a":1} 结束') == {"a": 1}
    assert N.parse_llm_json("不是 json") is None
    assert N.parse_llm_json("") is None


def test_tier_classify_and_exclude():
    # S/A/B 层源进管线；排除源（社会桶/crypto）不进
    assert N.source_tier("财联社 - 电报 - 看盘") == "S"
    assert N.source_tier("7x24小时快讯") == "A"
    assert N.source_tier("路透社最新报道") == "B"
    assert N.source_tier("World - Latest - Google News") == "X"
    assert N.source_tier("Crypto Briefing") == "X"
    assert N.source_tier("新浪财经－国内滚动") == "X"
    assert N.source_tier("某新源") == "B"  # 未知源保守放 B（正文级标签会兜底）


def test_pick_relevant_uses_enrichment_and_title():
    a_fin = {"title": "某公司涨停", "source_name": "财联社 - 电报 - 看盘",
             "enrichment": {"tickers": ["600519.SH"], "event_tags": ["涨停"]}}
    a_social = {"title": "某地发生泥石流", "source_name": "财联社 - 电报 - 看盘",
                "enrichment": {}}
    a_noise_src_but_tagged = {"title": "道路救援现场", "source_name": "新浪财经－国内滚动",
                              "enrichment": {"tickers": ["600519.SH"]}}
    assert N.pick_relevant(a_fin)
    assert not N.pick_relevant(a_social)
    # 排除源：即使带公司标签也要求标题财经词佐证（防路况/社会新闻误伤）
    assert not N.pick_relevant(a_noise_src_but_tagged)


def test_holdings_relevant_split():
    arts = [
        {"title": "贵州茅台：拟回购股份", "source_name": "财联社 - 电报 - 看盘",
         "enrichment": {"tickers": ["600519.SH"], "event_tags": ["回购"]}},
        {"title": "英伟达大涨", "source_name": "路透社最新报道",
         "enrichment": {"tickers": ["NVDA"], "event_tags": []}},
        {"title": "某地泥石流", "source_name": "新浪财经－国内滚动",
         "enrichment": {"tickers": []}},
    ]
    watch = {"600519.SH", "688183.SH"}
    h, other = N.split_holdings_related(arts, watch)
    assert [a["title"] for a in h] == ["贵州茅台：拟回购股份"]
    assert len(other) == 2


def test_dedup_event_verb():
    arts = [
        {"title": "康欣新材被立案调查", "source_name": "7x24小时快讯",
         "enrichment": {"tickers": ["600076.SH"]}},
        {"title": "康欣新材：涉嫌信披违规被立案", "source_name": "格隆汇快讯-7x24小时市场快讯-财经市场热点",
         "enrichment": {"tickers": ["600076.SH"]}},
        {"title": "康欣新材：关于回购股份的公告", "source_name": "财联社 - 电报 - 看盘",
         "enrichment": {"tickers": ["600076.SH"]}},
        {"title": "半导体板块异动拉升", "source_name": "7x24小时快讯", "enrichment": {}},
    ]
    out = N.dedup_articles(arts)
    # 立案×2 合并为 1；回购保留；无 ticker 板块条独立保留
    assert len(out) == 3


def test_incr_window_semantics():
    # 游标语义：窗口 = (last_end, now]，兜底最长 MAX_WINDOW_HOURS
    last = "2026-09-07T10:00:00+08:00"
    w = N.window_for(last, "2026-09-07T10:55:00+08:00")
    assert w[0] == last and w[1] == "2026-09-07T10:55:00+08:00"
    # 跨天兜底收缩：上次 3 天前 → 只取最近 N 小时
    old = "2026-09-04T15:00:00+08:00"
    now = "2026-09-07T09:25:00+08:00"
    w = N.window_for(old, now)
    assert w[1] == now
    assert (__import__("datetime").datetime.fromisoformat(w[0])
            - __import__("datetime").datetime.fromisoformat(now)).total_seconds() < 0
    # 无游标（首次）→ 同样兜底窗口
    w2 = N.window_for("", now)
    assert w2[1] == now and w2[0] != ""


def test_window_never_future():
    assert N.window_for("", "2026-09-07T09:25:00+08:00")[1] == "2026-09-07T09:25:00+08:00"


def test_brief_to_text_renders_key_sections():
    brief = {
        "ts": "2026-09-07T10:55:00+08:00",
        "window": {"start": "2026-09-07T10:00:00+08:00", "end": "2026-09-07T10:55:00+08:00"},
        "macro": {"bias": -0.3, "view": "中性偏空", "drivers": [{"item": "美联储鹰派"}], "risks": []},
        "themes": [{"name": "半导体国产化", "logic": "大基金三期", "strength": 2}],
        "watch_list": [],
        "open_risks": ["汇率波动"],
        "holdings": [{"code": "600519.SH", "name": "贵州茅台", "verdict": "中性",
                      "impact": 0, "event_type": "回购公告", "source": "财联社", "note": ""}],
        "market_notes": "",
        "confidence": 0.8,
        "refs": [],
    }
    t = N.brief_to_text(brief)
    assert "新闻分子" in t and "半导体国产化" in t and "600519.SH" in t
    assert "美联储鹰派" in t and "汇率波动" in t


def test_empty_window_short_circuit_text():
    assert N.empty_window_markup("2026-09-07T10:55:00+08:00") != ""


def test_watch_codes_merge_and_fallback(tmp_path):
    codes = N.merge_watch_codes(
        positions=[{"stock_code": "600309.SH", "total_volume": "100"}],
        pool=[{"code": "688183.SH"}], pool_codes=None)
    assert "600309.SH" in codes and "688183.SH" in codes


def test_parse_gate_lines_clean_and_salvage():
    clean = "12 macro 降准\n33 micro 涨停\n7 holdings 回购"
    d = N.parse_gate_lines(clean)
    assert d["related"] == [{"i": 12, "dir": "macro"},
                            {"i": 33, "dir": "micro"},
                            {"i": 7, "dir": "holdings"}]
    # 模型写散文时若出现 `序号 方向` 相邻形态也可救捞
    prose = ("逐条判断：12 macro 降准类；33 micro 涨停类保留，其余不列。")
    d2 = N.parse_gate_lines(prose)
    assert d2 and {x["i"] for x in d2["related"]} == {12, 33}
    assert N.parse_gate_lines("全是废话没有序号") is None
    assert N.parse_gate_lines("") is None


def test_parse_pipe_macro():
    txt = ("V | 中性偏多，政策托底 | 0.2\n"
           "D | 3000亿特别国债 | 利好金融权重\n"
           "R | 中东地缘 | 2\n"
           "C | 0.7\n"
           "（散文噪声行忽略）")
    d = N.parse_pipe_lines(txt, "macro")
    assert d["view"].startswith("中性偏多") and d["bias"] == 0.2
    assert d["drivers"][0]["item"] == "3000亿特别国债"
    assert d["risks"][0]["severity"] == 2 and d["confidence"] == 0.7


def test_parse_pipe_micro_and_holdings():
    mt = ("T | 存储芯片 | 涨价 | 2 | 三星涨价\n"
          "E | 688123.SH,688416.SH | 聚辰股份 | 涨停 | 0.8 | 板块带动\n"
          "O | 高低切\nC | 0.6")
    d = N.parse_pipe_lines(mt, "micro")
    assert d["themes"][0]["name"] == "存储芯片" and d["themes"][0]["strength"] == 2
    assert d["events"][0]["tickers"] == ["688123.SH", "688416.SH"]
    assert d["events"][0]["sentiment"] == 0.8 and d["rotation"] == "高低切"

    ht = ("H | 600309.SH | 万华化学 | 利好 | 1 | 日内 | 涨价 | MDI挂牌价上调 | 财联社 | 直接逻辑\n"
          "X | 600309.SH | 油价联动\nA | 挂条件\nC | 0.8")
    d = N.parse_pipe_lines(ht, "holdings")
    assert d["per_stock"][0]["code"] == "600309.SH" and d["per_stock"][0]["impact"] == 1
    assert d["per_stock"][0]["verdict"] == "利好"
    assert d["cross_risks"][0]["code"] == "600309.SH"
    assert d["action_hints"] == ["挂条件"]


def test_parse_pipe_rejects_garbage():
    assert N.parse_pipe_lines("全是散文没有格式", "macro") is None
    assert N.parse_pipe_lines("", "micro") is None


def test_parse_pipe_chief():
    txt = ("T | 存储芯片 | 涨价周期\n"
           "W | 688123.SH | 聚辰股份 | 板块带动 | 放量突破\n"
           "R | 中东地缘反复\n"
           "H | 600309.SH | 万华化学 | 中性 | 0 | 短期 | 油价 | 油价波动 | 财联社 |\n"
           "N | 可深挖存储链涨价持续性\nE | 结构性行情\nC | 0.7")
    d = N.parse_pipe_lines(txt, "chief")
    assert d["themes"][0]["name"] == "存储芯片"
    assert d["watch_list"][0]["code"] == "688123.SH" and d["watch_list"][0]["trigger"] == "放量突破"
    assert d["open_risks"] == ["中东地缘反复"]
    assert d["holdings"][0]["verdict"] == "中性" and d["holdings"][0]["impact"] == 0
    assert d["market_notes"].startswith("可深挖") and d["confidence"] == 0.7


def test_parse_pipe_rejects_truncated_draft_rows():
    """回归 2026-09-08 事故：chief 思考被 max_tokens 截断，救捞器把草稿里的
    半成品 H 行捞进了交易提示词（名称"(是)"、headline 截尾...、利空却 impact+0）。
    这些特征必须在 _is_placeholder_row 层被丢弃。"""
    # 09:55 实录的两行垃圾（字段裁剪为 chief H 协议长度）
    junk = ("H | 001312.SZ | (是) | 中性 | 0.0 | 今日 | 板块轮动联动 | 板块分析 | 无新持仓信号\n"
            "H | 600309.SH | 万华化学 | 利空 | 0.0 | 1-5日 | 宏观 | 宏观经济传导... | 宏观研究/板块跟踪\n"
            # 正常行必须保留
            "H | 600309.SH | 万华化学 | 利好 | 1.0 | 日内 | 涨价 | MDI挂牌价上调 | 财联社\n")
    d = N.parse_pipe_lines(junk, "chief")
    assert d is not None
    names = [h["name"] for h in d["holdings"]]
    assert names == ["万华化学"]  # (是) 占位行与 ... 截尾行都丢弃
    verdicts = [(h["verdict"], h["impact"]) for h in d["holdings"]]
    assert ("利空", 0.0) not in verdicts  # 利空 impact=0 矛盾行丢弃


def test_watch_line_carries_names():
    """回归：关注代码曾裸注入不带公司名 → 模型瞎猜次新股浪费 token 致截断。"""
    line = N._watch_line({"001312.SZ": "福恩股份", "600309.SH": ""})
    assert "福恩股份" in line and "600309.SH" in line
    assert "福恩股份" not in N._watch_line({})  # 空池 → 无


def test_truncated_output_error_class():
    exc = N.TruncatedOutputError("半截草稿", {"total_tokens": 7311})
    assert "截断" in str(exc)
    assert exc.content == "半截草稿" and exc.usage == {"total_tokens": 7311}


def _reset_run_stats():
    N._RUN_STATS.clear()
    N._RUN_STATS.update({"truncations": 0, "trunc_retries_ok": 0,
                         "gate_fallback_batches": 0})


def test_hits_watch_by_ticker_and_name():
    """2026-09-08 回归：全天标题 0 次点名关注代码 → holdings 恒 0。
    代码与公司名两种确定性命中都必须成立，与门卫 LLM 无关。"""
    watch = {"600309.SH": "万华化学", "001312.SZ": ""}
    assert N._hits_watch({"title": "某公司公告", "enrichment": {"tickers": ["600309.SH"]}}, watch)
    assert N._hits_watch({"title": "万华化学：MDI挂牌价上调", "enrichment": {"tickers": []}}, watch)
    assert not N._hits_watch({"title": "万州港股价波动", "enrichment": {"tickers": []}}, watch)
    assert not N._hits_watch({"title": "无关新闻", "enrichment": {"tickers": ["000001.SZ"]}}, watch)


def test_split_holdings_related_matches_names():
    """名称命中（无 enrichment 兜底）也应进 holdings。"""
    arts = [
        {"title": "福恩股份中标公告", "enrichment": {}},
        {"title": "无关新闻", "enrichment": {"tickers": []}},
    ]
    h, other = N.split_holdings_related(arts, {"001312.SZ": "福恩股份"})
    assert [a["title"] for a in h] == ["福恩股份中标公告"]
    assert len(other) == 1


def test_rule_direction_name_hit():
    """门卫降级兜底：名称命中 → holdings（此前只认 enrichment.tickers）。"""
    art = {"title": "万华化学发布提价函", "enrichment": {"tickers": []}}
    assert N.rule_direction(art, {"600309.SH": "万华化学"}) == "holdings"


def test_truncation_retry_recovers(monkeypatch):
    """截断后放宽 max_tokens 重试一次应救回整段（2026-09-08 午后两期全灭回归）。"""
    _reset_run_stats()
    calls = []

    def fake_llm(user, system, stage="", max_tokens=None):
        calls.append(max_tokens or N._MAX_TOKENS.get(stage, 8000))
        if len(calls) == 1:
            raise N.TruncatedOutputError("半截", {"total_tokens": 9999})
        return "V | 重试成功 | 0.1\nC | 0.5", {"prompt_tokens": 10, "completion_tokens": 5,
                                               "total_tokens": 15}

    monkeypatch.setattr(N, "call_llm", fake_llm)
    fake_log = N.ROOT / "test_fake_log.jsonl"
    monkeypatch.setattr(N, "append_log",
                        lambda user, content, sig, usage=None: fake_log)
    d, ok = N._run_stage(N.MACRO, "测试输入")
    assert ok and d and d["view"] == "重试成功"
    assert calls == [N._MAX_TOKENS[N.MACRO], N.TRUNC_RETRY_MAX_TOKENS]
    assert N._RUN_STATS["truncations"] == 1 and N._RUN_STATS["trunc_retries_ok"] == 1


def test_truncation_retry_still_truncated_gives_up(monkeypatch):
    """重试仍截断 → 整轮作废返回 (None, False)，不解析救捞。"""
    _reset_run_stats()

    def fake_llm(user, system, stage="", max_tokens=None):
        raise N.TruncatedOutputError("半截", {"total_tokens": 9999})

    monkeypatch.setattr(N, "call_llm", fake_llm)
    fake_log = N.ROOT / "test_fake_log.jsonl"
    monkeypatch.setattr(N, "append_log",
                        lambda user, content, sig, usage=None: fake_log)
    d, ok = N._run_stage(N.CHIEF, "测试输入")
    assert d is None and not ok
    assert N._RUN_STATS["truncations"] == 1 and N._RUN_STATS["trunc_retries_ok"] == 0


# ---------- 晚间复盘（news_review） ----------

import news_review as R  # noqa: E402


def test_review_classify():
    assert R.classify("利好", 1, 2.5) == "hit"
    assert R.classify("利好", 1, -2.0) == "reverse"
    assert R.classify("利空", -1, -3.0) == "hit"
    assert R.classify("利空", -1, 3.0) == "reverse"
    assert R.classify("利好", 1, 0.5) == "flat"      # ±1% 内=无反应
    assert R.classify("利好", 1, None) == "nodata"


def test_review_collect_rows_dedup_and_skip_neutral():
    briefs = [{"holdings": [
        {"code": "600309.SH", "verdict": "利好", "impact": 1, "event_type": "涨价"},
        {"code": "600309.SH", "verdict": "利好", "impact": 2, "event_type": "涨价"},  # 同键取强
        {"code": "688183.SH", "verdict": "中性", "impact": 0, "event_type": "x"},     # 跳过
    ]}]
    rows = R.collect_rows(briefs)
    assert len(rows) == 1 and rows[0]["impact"] == 2


def test_review_update_lessons_tmp(tmp_path):
    rows = [{"code": "600309.SH", "verdict": "利好", "impact": 1,
             "event_type": "涨价", "source": "财联社"}]
    lessons = R.update_lessons(rows, {"600309.SH": 3.2}, path=tmp_path / "lessons.json")
    assert lessons["by_event_type"]["涨价"]["hit"] == 1
    assert lessons["by_source"]["财联社"]["hit"] == 1
    # 累计合并
    R.update_lessons(rows, {"600309.SH": -3.0}, path=tmp_path / "lessons.json")
    lessons2 = R.update_lessons([], {}, path=tmp_path / "lessons.json")
    assert lessons2["by_event_type"]["涨价"]["hit"] == 1
    assert lessons2["by_event_type"]["涨价"]["reverse"] == 1


def test_review_lessons_text_threshold():
    t = R.lessons_text({"by_event_type": {"涨价": {"hit": 3, "reverse": 1, "flat": 0},
                                          "小样本": {"hit": 1, "reverse": 0, "flat": 0}}})
    assert "涨价" in t and "小样本" not in t


# ---------- 生产级优化：L2 新鲜度/清理、盘中异动关注、竞价防护 ----------

def test_l2_fresh_filter():
    from datetime import datetime as _dt
    now = _dt.fromisoformat("2026-09-07T14:00:00+08:00")
    rows = {
        "600309.SH": {"ts": "2026-09-07T13:58:00+08:00", "factors": {"x": 1}},   # 新
        "688183.SH": {"ts": "2026-09-07T10:00:00+08:00", "factors": {"x": 2}},   # 4h 旧
        "000039.SZ": {"ts": "2026-09-03T15:00:00+08:00", "factors": {"x": 3}},   # 4天 旧
        "603976.SH": {"ts": "", "factors": {}},                                   # 坏 ts
    }
    fresh = L.l2_fresh_filter(rows, now)
    assert set(fresh) == {"600309.SH"}


def test_l2_prune_stale():
    factors = {"600309.SH": {"ts": "t"}, "300308.SZ": {"ts": "t2"}, "605369.SH": {}}
    out = L.l2_prune_stale(factors, ["600309.SH", "688183.SH"])
    assert set(out) == {"600309.SH"}


def test_intraday_watch_merge_and_cooldown(tmp_path, monkeypatch):
    import news_brief as NB
    monkeypatch.setattr(NB, "INTRA_WATCH_FILE", tmp_path / "iw.json")
    micro = {"events": [
        {"tickers": ["688123.SH"], "name": "聚辰股份", "event_type": "涨停异动"},
        {"tickers": ["300308.SZ"], "name": "中际旭创", "event_type": "主力净流入"},
    ]}
    watch = [{"code": "600309.SH", "name": "万华化学", "trigger": "油价异动"}]
    NB.update_intraday_watch(micro, watch)
    d = NB.update_intraday_watch({"events": []}, [])
    codes = {x["code"] for x in d["items"]}
    assert {"688123.SH", "300308.SZ", "600309.SH"} <= codes
    assert all("why" in x for x in d["items"])


def test_price_watch_auction_guard():
    from live_price_watch import in_close_auction
    from datetime import datetime as _dt
    # 收盘集合竞价 14:57-15:00 不触发实单
    assert in_close_auction(_dt.fromisoformat("2026-09-07T14:58:30+08:00"))
    assert not in_close_auction(_dt.fromisoformat("2026-09-07T14:50:00+08:00"))
    assert not in_close_auction(_dt.fromisoformat("2026-09-07T15:01:00+08:00"))


def test_trade_recap_intent_note(monkeypatch, tmp_path):
    """回归 2026-09-08：卖出 600×33% 意图 199 → 整手合规实卖 100，模型下一轮
    对不上账。成交日志记 intent_volume，回顾块对偏差交易注入对照说明。"""
    import live_prompt_context as PC

    monkeypatch.setattr(PC, "ROOT", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    rows = [
        {"ts": "2026-09-08T10:05:00+08:00", "agent": "t-agent", "code": "600309.SH",
         "side": "sell", "mode": "execute_intraday", "volume": 100, "price": 77.68,
         "intent_volume": 199, "fill": {"filled_volume": 100, "filled_price": 77.68}},
        {"ts": "2026-09-08T10:06:00+08:00", "agent": "t-agent", "code": "688183.SH",
         "side": "sell", "mode": "execute_intraday", "volume": 350, "price": 131.0,
         "intent_volume": 350, "fill": {"filled_volume": 350, "filled_price": 131.0}},
        {"ts": "2026-09-08T10:07:00+08:00", "agent": "t-agent", "code": "600309.SH",
         "side": "sell", "mode": "execute_intraday", "volume": 50, "price": 77.0,
         "fill": {"filled_volume": 50, "filled_price": 77.0}},  # 旧行无 intent → 无对照
    ]
    (logs / "live_trade_test.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = PC.build_trade_recap("t-agent")
    assert "600309.SH" in out and "688183.SH" in out
    assert "申报意图 199 股" in out
    assert out.count("申报意图") == 1  # 意图==成交、或缺 intent 字段的行不加注


def test_cli_parser_exposes_all_run_pipeline_args():
    """回归：cli 曾访问 a.force 但 parser 未定义 → 每次调用必崩。
    保护：cli 传给 run_pipeline 的属性必须在 parser 里都能解析出来。"""
    ns = N.build_arg_parser().parse_args([])
    for attr in ("since", "until", "stage", "force"):
        assert hasattr(ns, attr), f"parser 缺少 --{attr}"
    assert ns.force is False  # 默认不强制
    assert N.build_arg_parser().parse_args(["--force"]).force is True
    assert N.build_arg_parser().parse_args(["--stage", "macro"]).stage == "macro"
