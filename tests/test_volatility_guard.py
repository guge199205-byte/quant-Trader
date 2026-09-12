"""波动触发防护单测：坏 tick 丢弃 / 好 tick 保留 / 同向冷却 / 反向不受限。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_volatility_guard.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_hourly_analysis as L  # noqa: E402

ROWS = [{"code": "688183.SH", "name": "生益电子", "price": 118.3,
         "pnl_pct": -11.5, "day_chg": -5.13}]


class FakeBroker:
    def get_klines(self, *a, **k):
        return []


def setup_module(module):
    module._orig_state = L.STATE_PATH
    module._orig_rows, module._orig_qf = L.build_rows, L.quote_fallback
    L.STATE_PATH = Path("/tmp/test_state_guard.json")
    L.build_rows = lambda broker, positions, names: [dict(r) for r in ROWS]
    L.MIN_ANALYSIS_INTERVAL_MIN = 0
    # check_volatility 的新闻触发读真实 data/news_brief/latest.json（news_brief.BRIEF_FILE）。
    # 2026-09-09 持仓情报复活后该文件真的有了 |impact|≥1 的行 → 坏 tick 用例被新闻信号
    # 顶掉而失败。测试必须隔离外部数据，指向不存在的路径（→ brief={}）。
    import news_brief
    module._news_brief = news_brief
    module._orig_brief = news_brief.BRIEF_FILE
    news_brief.BRIEF_FILE = Path("/tmp/test_guard_brief_absent.json")


def teardown_module(module):
    L.STATE_PATH = module._orig_state
    L.build_rows, L.quote_fallback = module._orig_rows, module._orig_qf
    module._news_brief.BRIEF_FILE = module._orig_brief
    Path("/tmp/test_state_guard.json").unlink(missing_ok=True)


def _set_state(d):
    Path("/tmp/test_state_guard.json").write_text(json.dumps(d), encoding="utf-8")


def _base():
    _set_state({"last_pnl": {"688183.SH": -5.0}, "last_day": {}, "last_ts": ""})


def test_bad_tick_dropped(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code, ref=0.0: 131.5)  # 偏差 11%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_good_tick_kept(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code, ref=0.0: 118.1)  # 偏差 0.17%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])


def test_same_direction_cooldown(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code, ref=0.0: 118.1)
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    # 60 分钟内同向再触发 → 冷却丢弃
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_reverse_direction_not_cooled(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code, ref=0.0: 118.1)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    _set_state({"last_pnl": {"688183.SH": -20.0}, "last_day": {}, "last_ts": "",
                "last_trigger": {"688183.SH": {"dir": "down", "ts": now.isoformat()}}})
    r = L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    assert r and "盈亏" in r  # 反向（盈亏转好）不受冷却限制，pnl 触发保留


# ---------- 方案 C v2：板块联动 / L2 资金流 / 新闻持仓信号 ----------

def test_sector_trigger_delta_and_abs():
    ctx = {"position_sectors": {"600309.SH": "881034.SH"},
           "sectors": {"881034.SH": {"name": "化学制品", "chg_pct": 2.0}}}
    # 变化 5pp ≥3pp → 触发
    hits = L.sector_triggers(ctx, {"881034.SH": -3.0}, ["600309.SH"])
    assert hits and hits[0][2].startswith("板块联动")
    # 变化 1pp 但绝对 ≥5% → 触发
    ctx["sectors"]["881034.SH"]["chg_pct"] = 6.0
    hits2 = L.sector_triggers(ctx, {"881034.SH": 5.0}, ["600309.SH"])
    assert hits2 and "板块绝对涨跌" in hits2[0][2]
    # 变化 1pp、绝对 2% → 不触发
    ctx["sectors"]["881034.SH"]["chg_pct"] = 2.0
    assert L.sector_triggers(ctx, {"881034.SH": 1.0}, ["600309.SH"]) == []
    # 持仓无板块映射 → 不触发
    assert L.sector_triggers(ctx, {}, ["600309.SH"]) == []


def test_l2_trigger_inout_delta():
    factors = {"600309.SH": {"factors": {"inout": 0.5}}}
    assert L.l2_triggers(factors, {"600309.SH": 0.1}, ["600309.SH"])  # 0.4 ≥0.3
    assert not L.l2_triggers(factors, {"600309.SH": 0.4}, ["600309.SH"])  # 0.1 <0.3
    assert not L.l2_triggers(factors, {}, ["600309.SH"])  # 无基线不触发
    assert not L.l2_triggers({}, {"600309.SH": 0.1}, ["600309.SH"])  # 无数据


def test_news_trigger_impact_and_dedup():
    brief = {"ts": "2026-09-07T14:42:00+08:00",
             "holdings": [{"code": "600309.SH", "name": "万华化学", "verdict": "利空",
                           "impact": -1.0, "event_type": "监管"}]}
    hits, key = L.news_triggers(brief, "")
    assert hits and "impact-1" in hits[0][2] and key == brief["ts"]
    # 同一分子不重复唤醒
    hits2, _ = L.news_triggers(brief, key)
    assert hits2 == []
    # impact 0 不触发
    brief2 = {"ts": "x2", "holdings": [{"code": "c", "impact": 0}]}
    assert L.news_triggers(brief2, "")[0] == []


def test_news_trigger_filters_to_held_codes():
    """新闻唤醒按持仓过滤（2026-09-12 P1-5）：分子的『盯』/候选个股异动不该
    唤醒整轮完整分析（09-11 一天 78 轮的成本来源之一；候选池异动另有 L2 轮询
    通道，不丢信息）。"""
    brief = {"ts": "t1", "holdings": [
        {"code": "600309.SH", "name": "万华化学", "verdict": "利空", "impact": -1.0,
         "event_type": "监管"},
        {"code": "603019.SH", "name": "中科曙光", "verdict": "利多", "impact": 2.0,
         "event_type": "订单"}]}

    hits, key = L.news_triggers(brief, "", held_codes={"600309.SH"})
    assert [h[0] for h in hits] == ["600309.SH"] and key == "t1"
    # 全是非持仓 → 不触发
    assert L.news_triggers(brief, "", held_codes={"000001.SZ"})[0] == []
    # 不传 held_codes → 兼容旧行为（全量，供纯函数级复用）
    assert len(L.news_triggers(brief, "")[0]) == 2


def test_check_volatility_passes_held_codes_to_news():
    """接线：check_volatility 必须把本轮持仓代码传给新闻触发过滤。"""
    import inspect
    src = inspect.getsource(L.check_volatility)
    assert "held_codes=" in src


def test_non_held_news_marks_seen_without_trigger(monkeypatch, tmp_path):
    """P1-5 接线：只有非持仓命中的新分子 → 不唤醒，但 ts 记账。

    不记账会导致同一 ts 每分钟重扫 + 重打印（news_brief 分子保留到下次再造）；
    记账语义 = 「这个分子已评估过」，与其后是否买入该票无关（新分子会带新 ts）。"""
    import json as _json
    import news_brief

    p = tmp_path / "brief.json"
    p.write_text(_json.dumps({
        "ts": "t-nonheld",
        "holdings": [{"code": "603019.SH", "name": "中科曙光", "verdict": "利多",
                      "impact": 2.0, "event_type": "订单"}]}), encoding="utf-8")
    monkeypatch.setattr(news_brief, "BRIEF_FILE", p)
    monkeypatch.setattr(L, "quote_fallback", lambda code, ref=0.0: 118.1)
    # 个股盈亏/涨跌都停在原位（不制造个股触发），只留新闻源说话
    _set_state({"last_pnl": {"688183.SH": -11.5}, "last_day": {"688183.SH": -5.5},
                "last_ts": "", "last_news_key": ""})

    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None
    st = _json.loads(Path("/tmp/test_state_guard.json").read_text(encoding="utf-8"))
    assert st["last_news_key"] == "t-nonheld"


def test_check_volatility_v2_sources_wired():
    # 三源函数已接入 check_volatility（源码级确认接线存在）
    import inspect
    src = inspect.getsource(L.check_volatility)
    assert "sector_triggers(" in src and "l2_triggers(" in src and "news_triggers(" in src


# ---------- 取价备胎反杀修复（2026-09-12 P0-2）----------
# 09-11 实录：备胎 Fuyao 恒取 items[0]（000001.SZ 平安银行 11.74）→ 与桥价偏差
# 必然 >2% → 坏 tick 校验把**真触发**整行丢掉（当日 156 条）。修法在 live_quote
# 客户端匹配；这里钉住调用侧口径：备胎价先与桥价做 40% 闸，越界按"备胎坏值"
# 丢弃（fb=0，不杀触发），±2% 交叉校验只对可信备胎价生效。

def test_quote_fallback_gets_bridge_price_as_ref(monkeypatch):
    seen = {}

    def _fb(code, ref=None):
        seen["ref"] = ref
        return (118.1, "fuyao")

    monkeypatch.setattr(L.live_quote, "fallback_quote", _fb)
    _base()

    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    assert seen["ref"] == 118.3                       # 桥价进了备胎坏值闸


def test_bad_fallback_source_does_not_kill_real_trigger(monkeypatch):
    """备胎源坏（匹配不到目标 → None）→ fb=0 → 触发保留，不许反向误杀。"""
    monkeypatch.setattr(L.live_quote, "fallback_quote", lambda code, ref=None: (None, ""))
    _base()

    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
