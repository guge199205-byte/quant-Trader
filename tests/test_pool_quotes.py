"""候选池实时行情注入 + 日级新开仓上限（2026-09-08 实盘解锁）。

背景：候选池此前只注入评分不注入价格 → 模型无法定价 → 只能 hold 或把买入意图
写成 watch（哨兵不执行买单），成交流水 10 卖 1 买。这两个函数是解锁链路的关键件。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_pool_quotes.py -q
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_hourly_analysis as L  # noqa: E402
import live_prompt_context as P  # noqa: E402

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 8, 10, 30, tzinfo=BJ)


def _factors_file(tmp_path: Path, recs: dict) -> Path:
    f = tmp_path / "l2_factors_live.json"
    f.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return f


def _rec(ts="2026-09-08T10:25:00+08:00", price=11.5, pre=11.0, sig=42.0):
    return {"ts": ts, "name": "", "now_price": price, "pre_close": pre,
            "open": price, "volume": 1000.0, "factors": {}, "signal_score": sig}


POOL = [{"code": "600362.SH", "name": "江西铜业"},
        {"code": "002144.SZ", "name": "宏达高科"}]


# ---------------------------------------------------------------- 行情注入

def test_fresh_quote_rendered_with_day_change(tmp_path):
    f = _factors_file(tmp_path, {"600362.SH": _rec(price=48.53, pre=49.1)})
    out = P.build_pool_quote_block(POOL, path=f, now=NOW)
    assert "江西铜业 600362.SH 现价 ¥48.53（-1.16%）" in out
    assert "信号分 42.0" in out
    assert "最新 5 分钟前" in out


def test_missing_code_is_skipped_not_faked(tmp_path):
    f = _factors_file(tmp_path, {"600362.SH": _rec()})
    out = P.build_pool_quote_block(POOL, path=f, now=NOW)
    assert "600362.SH" in out
    assert "宏达高科" not in out  # 无行情 → 整行剔除，绝不臆造价格


def test_stale_quote_excluded_and_counted(tmp_path):
    f = _factors_file(tmp_path, {"600362.SH": _rec(ts="2026-09-08T09:00:00+08:00"),
                                 "002144.SZ": _rec()})
    out = P.build_pool_quote_block(POOL, path=f, now=NOW, max_age_min=20)
    assert "600362.SH" not in out
    assert "宏达高科" in out
    assert "1 只池内标的行情已过期" in out


def test_zero_or_broken_price_skipped(tmp_path):
    f = _factors_file(tmp_path, {"600362.SH": _rec(price=0),
                                 "002144.SZ": _rec(price="bad")})
    assert P.build_pool_quote_block(POOL, path=f, now=NOW) == ""


def test_no_pre_close_omits_change_not_crashes(tmp_path):
    rec = _rec()
    rec.pop("pre_close")
    f = _factors_file(tmp_path, {"600362.SH": rec})
    out = P.build_pool_quote_block(POOL, path=f, now=NOW)
    assert "现价 ¥11.50" in out
    assert "%）" not in out


def test_empty_pool_or_missing_file_is_empty(tmp_path):
    assert P.build_pool_quote_block([], path=tmp_path / "nope.json", now=NOW) == ""
    assert P.build_pool_quote_block(POOL, path=tmp_path / "nope.json", now=NOW) == ""


def test_non_dict_payload_is_empty(tmp_path):
    f = tmp_path / "l2_factors_live.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    assert P.build_pool_quote_block(POOL, path=f, now=NOW) == ""


def test_block_carries_actionable_pricing_note(tmp_path):
    """尾部必须说明"可直接下单"——否则模型仍会因缺价而空转（本次修复的初衷）。"""
    f = _factors_file(tmp_path, {"600362.SH": _rec()})
    out = P.build_pool_quote_block(POOL, path=f, now=NOW)
    assert "可直接下单的定价依据" in out


def test_llm_trade_prompt_injects_pool_quotes(tmp_path, monkeypatch):
    """09:35 开盘轮（主入口）同样必须拿到池内价——否则整点轮修好了它还在空转。"""
    import live_llm_trade as T

    (tmp_path / "data").mkdir()
    # ts 用"现在"：build_prompt 内部不传 now，过期剔除会按真实时钟判（同生产）
    fresh_ts = datetime.now().astimezone().isoformat(timespec="seconds")
    (tmp_path / "data" / "l2_factors_live.json").write_text(
        json.dumps({"600362.SH": _rec(ts=fresh_ts, price=48.53, pre=49.1)}),
        encoding="utf-8")
    monkeypatch.setattr(P, "ROOT", tmp_path)
    out = T.build_prompt("a1", [], ["| 1 | 600362.SH | 江西铜业 | 有色 | 8 | 7 | — |"],
                         {}, 100000.0, pool=POOL)
    assert "江西铜业 600362.SH 现价 ¥48.53" in out


def test_llm_trade_prompt_without_pool_is_unchanged(tmp_path, monkeypatch):
    import live_llm_trade as T

    monkeypatch.setattr(P, "ROOT", tmp_path / "empty")
    out = T.build_prompt("a1", [], ["| 1 | 600362.SH | 江西铜业 | 有色 | 8 | 7 | — |"],
                         {}, 100000.0)
    assert "候选池实时行情" not in out
    assert "江西铜业" in out


# ---------------------------------------------------------------- 资金量裁剪
# 2026-09-09：每 agent ¥10 万额度、单票 ≤20% 剩余额度 → 单票预算 ~¥2 万。
# 高价票（宁德时代 ¥363、中际旭创 ¥845）在池子里模型点不动，纯浪费一轮决策。

def test_drops_unaffordable_and_keeps_rest(tmp_path):
    # 600362 @¥48.53：一手 100 股 ≈¥4,853 ≤ ¥20,000 → 保留
    # 688183 @¥120：科创板最小 200 股 ≈¥24,000 > ¥20,000 → 剔除
    f = _factors_file(tmp_path, {"600362.SH": _rec(price=48.53),
                                 "688183.SH": _rec(price=120.0)})
    pool = [{"code": "600362.SH", "name": "江西铜业"},
            {"code": "688183.SH", "name": "生益电子"}]
    kept, dropped = P.filter_affordable(pool, 20000.0, path=f, now=NOW)
    assert [p["code"] for p in kept] == ["600362.SH"]
    assert dropped == [("688183.SH", "生益电子", 24000.0, 200)]


def test_main_board_pricey_dropped_by_lot_minimum(tmp_path):
    # 主板 100 股一手：¥300 的票一手 ¥30,000 > ¥20,000 → 剔除
    f = _factors_file(tmp_path, {"600309.SH": _rec(price=300.0)})
    kept, dropped = P.filter_affordable([{"code": "600309.SH", "name": "万华化学"}],
                                        20000.0, path=f, now=NOW)
    assert kept == [] and dropped[0][2] == 30000.0


def test_keeps_when_price_missing_or_stale(tmp_path):
    """没价/行情过期 → 无法判资金 → 保留（fail-open），由下单侧 compute_order 兜。"""
    f = _factors_file(tmp_path, {"600362.SH": _rec(ts="2026-09-08T09:00:00+08:00")})
    pool = [{"code": "600362.SH", "name": "江西铜业"},   # 过期
            {"code": "002144.SZ", "name": "宏达高科"}]   # 无记录
    kept, dropped = P.filter_affordable(pool, 1.0, path=f, now=NOW)
    assert [p["code"] for p in kept] == ["600362.SH", "002144.SZ"]
    assert dropped == []


def test_slack_allows_one_lot_just_over_budget(tmp_path):
    # 科创板 ¥100×200 股 = ¥20,000 恰在预算线上；限价买按 +1% 报 → 容差内放行
    f = _factors_file(tmp_path, {"688183.SH": _rec(price=100.0)})
    kept, dropped = P.filter_affordable([{"code": "688183.SH", "name": "生益电子"}],
                                        20000.0, path=f, now=NOW)
    assert len(kept) == 1 and dropped == []


def test_empty_pool_or_broken_file_is_noop(tmp_path):
    assert P.filter_affordable([], 20000.0, path=tmp_path / "nope.json", now=NOW) == ([], [])
    pool = [{"code": "600362.SH", "name": "江西铜业"}]
    kept, dropped = P.filter_affordable(pool, 20000.0, path=tmp_path / "nope.json", now=NOW)
    assert kept == pool and dropped == []
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2,3]", encoding="utf-8")
    assert P.filter_affordable(pool, 20000.0, path=bad, now=NOW) == (pool, [])


def test_two_layer_guard_agrees_on_same_price(tmp_path):
    """提示词侧剔除与下单侧硬拦必须同口径：同一只票、同一个价，两边结论一致。
    （两层各自实现会漂移——2026-09-09 把最小可买量抽到 ashare_rules 就是为此）"""
    import live_trade_picks as T

    f = _factors_file(tmp_path, {"688183.SH": _rec(price=120.0)})
    pool = [{"code": "688183.SH", "name": "生益电子"}]
    kept, dropped = P.filter_affordable(pool, 20000.0, path=f, now=NOW)
    assert kept == [] and dropped[0][2] == 24000.0
    bars = [{"close": 120.0, "open": 120.0, "volume": 1000},
            {"close": 120.0, "open": 120.0, "volume": 1000}]
    o = T.compute_order(bars, 100000.0, 0.2, "688183.SH")  # 预算同为 ¥20,000
    assert o["ok"] is False and "最小可买 200 股需 ¥24,000" in o["reason"]


def test_llm_trade_prompt_declares_budget_filter():
    """提示词必须说明池子已按资金量裁过——否则模型会把"表里没有"当成不存在。"""
    import live_llm_trade as T

    out = T.build_prompt("a1", [], ["| 1 | 600362.SH | 江西铜业 | 有色 | 8 | 7 | — |"],
                         {}, 100000.0)
    assert "候选池已按你的资金量剔除买不起的标的" in out


# ---------------------------------------------------------------- 日级新开仓上限

def _trade_file(tmp_path: Path, recs: list) -> Path:
    f = tmp_path / "live_trade_20260908.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    return f


def test_counts_own_filled_buys_only(tmp_path):
    f = _trade_file(tmp_path, [
        {"agent": "a1", "side": "buy", "code": "600362.SH",
         "fill": {"filled_volume": 500, "filled_price": 48.5}},
        {"agent": "a1", "side": "buy", "code": "002144.SZ",
         "fill": {"filled_volume": 300}},
        {"agent": "a1", "side": "sell", "code": "600309.SH",
         "fill": {"filled_volume": 500}},                      # 卖出不计
        {"agent": "a2", "side": "buy", "code": "603107.SH",
         "fill": {"filled_volume": 100}},                      # 别家 agent 不计
        {"agent": "a1", "side": "buy", "code": "600000.SH",
         "error": "rejected"},                                 # 失败单不计
        {"agent": "a1", "side": "buy", "code": "600001.SH",
         "fill": {"filled_volume": 0}},                        # 未成交不计
    ])
    assert L.daily_buy_codes("a1", day="2026-09-08", path=f) == {"600362.SH", "002144.SZ"}


def test_fill_confirm_mode_counted(tmp_path):
    f = _trade_file(tmp_path, [
        {"agent": "a1", "side": "buy", "code": "600362.SH", "mode": "fill_confirm",
         "volume": 500, "price": 48.5},
    ])
    assert L.daily_buy_codes("a1", day="2026-09-08", path=f) == {"600362.SH"}


def test_missing_file_and_garbage_lines_are_safe(tmp_path):
    assert L.daily_buy_codes("a1", day="2026-09-08", path=tmp_path / "nope.jsonl") == set()
    f = tmp_path / "live_trade_20260908.jsonl"
    f.write_text('not json\n{"agent":"a1","side":"buy","code":"600362.SH",'
                 '"fill":{"filled_volume":100}}\n', encoding="utf-8")
    assert L.daily_buy_codes("a1", day="2026-09-08", path=f) == {"600362.SH"}
