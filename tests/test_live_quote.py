"""取价备胎（scripts/live_quote.py）单测：客户端匹配 / 来源选择 / 坏价闸。

2026-09-11 实录：Fuyao `/api/a-share/prices/snapshot` **忽略 thscode**、返回
全市场 5571 条（item[0] 恒为 000001.SZ 平安银行），老实现直接取
`items[0]["last_price"]` → 桥瞬时不可用时给 agent 喂的备胎价一律 11.x；
波动触发的「桥价 vs 备胎 ±2%」坏 tick 校验因此把**真触发**整行丢掉
（当日丢 156 条）。修法：一律客户端按代码匹配，匹配不到返回 None。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_quote.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_quote as Q  # noqa: E402


def _row(code, last, prev):
    return {"thscode": code, "ticker": code.split(".")[0],
            "last_price": last, "prev_price": prev}


def _payload(rows, code=0, message=""):
    return {"code": code, "message": message,
            "data": {"count": len(rows), "ts": 1789184958000, "item": rows}}


@pytest.fixture
def fuyao(monkeypatch):
    """Fuyao 原始响应可注入；box["params"] 记录最近一次请求参数。"""
    box = {}

    def _get(path, params):
        box["path"], box["params"] = path, params
        d = box.get("payload")
        if isinstance(d, Exception):
            raise d
        return d if d is not None else _payload([])

    monkeypatch.setattr(Q, "_fuyao_get", _get)
    box["set"] = lambda d: box.update(payload=d)
    return box


@pytest.fixture
def tencent(monkeypatch):
    """腾讯原始行情串可注入；box["symbol"] 记录最近一次请求的 qt 代码。"""
    box = {}

    def _raw(symbol):
        box["symbol"] = symbol
        d = box.get("raw")
        if isinstance(d, Exception):
            raise d
        return d if d is not None else ""

    monkeypatch.setattr(Q, "_tencent_raw", _raw)
    box["set"] = lambda s: box.update(raw=s)
    return box


@pytest.fixture
def discards(monkeypatch):
    """收起坏价事件（断言面），不碰真实事件台账。"""
    seen = []
    monkeypatch.setattr(Q, "_record_discard",
                        lambda code, source, px, ref: seen.append((code, source, px, ref)))
    return seen


# ---------- Fuyao：客户端匹配，永不信 items[0] ----------

def test_fuyao_picks_the_target_row_not_the_first(fuyao):
    fuyao["set"](_payload([_row("000001.SZ", 11.74, 11.85),
                           _row("300750.SZ", 402.1, 400.0),
                           _row("600519.SH", 1520.0, 1500.0)]))

    assert Q.fuyao_snapshot("600519.SH") == 1520.0
    assert fuyao["params"] == {"thscode": "600519.SH"}      # 参数照发，兼容上游修复


def test_fuyao_missing_target_returns_none(fuyao):
    """全市场快照里没有目标 → None。退化成 items[0] 就是 09-11 的事故本身。"""
    fuyao["set"](_payload([_row("000001.SZ", 11.74, 11.85)]))

    assert Q.fuyao_snapshot("600309.SH") is None


def test_fuyao_matches_six_digit_form(fuyao):
    """调用方只给 6 位码时按 6 位码匹配（后缀缺失不构成拒绝理由）。"""
    fuyao["set"](_payload([_row("600309.SH", 71.2, 70.0)]))

    assert Q.fuyao_snapshot("600309") == 71.2


def test_fuyao_does_not_cross_market_suffix(fuyao):
    """6 位码相同、市场后缀不同 → 不串行（SH 的票不许拿 SZ 同号行的价）。"""
    fuyao["set"](_payload([_row("600309.SZ", 9.9, 9.9)]))

    assert Q.fuyao_snapshot("600309.SH") is None


@pytest.mark.parametrize("bad", [0, -3.5, None, "abc", ""])
def test_fuyao_invalid_price_returns_none(fuyao, bad):
    fuyao["set"](_payload([_row("600309.SH", bad, 70.0)]))

    assert Q.fuyao_snapshot("600309.SH") is None


def test_fuyao_network_error_returns_none(fuyao):
    fuyao["set"](OSError("fuyao 超时"))

    assert Q.fuyao_snapshot("600309.SH") is None


def test_fuyao_out_of_band_price_discarded_and_recorded(fuyao, discards):
    """09-08 实录的坏形：last 4.789 vs prev 17.5（-73%）→ 丢弃 + 留痕，不喂给 LLM。"""
    fuyao["set"](_payload([_row("001312.SZ", 4.789, 17.5)]))

    assert Q.fuyao_snapshot("001312.SZ") is None
    assert discards == [("001312.SZ", "fuyao", 4.789, 17.5)]


def test_fuyao_accepts_normal_move(fuyao):
    """±10% 涨跌停内的正常价不许被闸门误杀。"""
    fuyao["set"](_payload([_row("600309.SH", 76.9, 70.0)]))

    assert Q.fuyao_snapshot("600309.SH") == 76.9


# ---------- 腾讯：北交所前缀 + 昨收坏价闸 ----------

@pytest.mark.parametrize(("code", "want"), [
    ("600519.SH", "sh600519"), ("600519.SS", "sh600519"), ("600519", "sh600519"),
    ("000001.SZ", "sz000001"), ("300750", "sz300750"), ("002594.SZ", "sz002594"),
    ("430047.BJ", "bj430047"), ("430047", "bj430047"),
    ("830799.BJ", "bj830799"), ("920001", "bj920001"),
    ("", ""),
])
def test_tencent_symbol_prefix(code, want):
    assert Q.tencent_symbol(code) == want


def test_tencent_quote_parses_price(tencent):
    tencent["set"]('v_sh600519="1~贵州茅台~600519~1520.00~1500.00~1501.00~..."')

    assert Q.tencent_quote("600519.SH") == 1520.0
    assert tencent["symbol"] == "sh600519"


def test_tencent_out_of_band_against_prev_close_discarded(tencent, discards):
    tencent["set"]('v_sz001312="1~多浦乐~001312~4.79~17.50~17.40~..."')

    assert Q.tencent_quote("001312.SZ") is None
    assert discards and discards[0][1] == "tencent"


@pytest.mark.parametrize("raw", [
    "<html>限频</html>",                       # 非行情串
    'v_sh600519="1~贵州茅台~600519~0~1500.00~"',   # 0 价（停牌/未开盘）
    'v_sh600519="1~贵州茅台~600519~abc~1500.00~"',  # 非数
])
def test_tencent_bad_payload_returns_none(tencent, raw):
    tencent["set"](raw)

    assert Q.tencent_quote("600519.SH") is None


def test_tencent_network_error_returns_none(tencent):
    tencent["set"](OSError("Connection refused"))

    assert Q.tencent_quote("600519.SH") is None


# ---------- sane_quote：40% 口径（与 live_ledger.sane_fill_price 同源）----------

@pytest.mark.parametrize(("px", "ref", "want"), [
    (17.5, 17.4, 17.5),          # 日内正常波动
    (15.3, 17.5, 15.3),          # -12.6%（跌停内）
    (17.5, 0, 17.5),             # 无参照 → 不判断
    (17.5, None, 17.5),
    (4.789, 17.5, None),         # -73%：09-08 桥坏价同形
    (30.0, 17.5, None),          # +71%：上越界同样丢弃
    (0, 17.5, None),
    (-1, 17.5, None),
    ("abc", 17.5, None),
    (None, 17.5, None),
])
def test_sane_quote_band(px, ref, want):
    assert Q.sane_quote(px, ref) == want


# ---------- fallback_quote：来源链 + 来源字符串 ----------

def test_fallback_prefers_fuyao_then_tencent(fuyao, tencent):
    fuyao["set"](_payload([_row("600519.SH", 1520.0, 1500.0)]))
    tencent["set"]('v_sh600519="1~贵州茅台~600519~1519.0~1500.0~"')
    assert Q.fallback_quote("600519.SH") == (1520.0, "fuyao")

    fuyao["set"](_payload([]))                       # Fuyao 没有这条 → 落腾讯
    assert Q.fallback_quote("600519.SH") == (1519.0, "tencent")


def test_fallback_all_sources_down_returns_empty_source(fuyao, tencent):
    fuyao["set"](OSError("fuyao 超时"))
    tencent["set"](OSError("Connection refused"))

    assert Q.fallback_quote("600519.SH") == (None, "")


def test_fallback_discards_source_breaking_caller_ref(fuyao, tencent, discards):
    """桥价作为参照（ref）：两源都 >40% 偏离 → 宁可无价，也不喂错价。"""
    fuyao["set"](_payload([_row("600519.SH", 100.0, 100.0)]))
    tencent["set"]('v_sh600519="1~贵州茅台~600519~95.0~100.0~"')

    assert Q.fallback_quote("600519.SH", ref=1500.0) == (None, "")
    assert len(discards) == 2                        # 两个源各留一次痕


def test_fallback_fuyao_bad_falls_through_to_tencent(fuyao, tencent, discards):
    """Fuyao 单源坏价不许拖垮整条链：腾讯有效价照用。"""
    fuyao["set"](_payload([_row("001312.SZ", 4.789, 17.5)]))
    tencent["set"]('v_sz001312="1~多浦乐~001312~17.52~17.50~"')

    assert Q.fallback_quote("001312.SZ") == (17.52, "tencent")


# ---------- 探活同源（rt_probe）----------

def test_probe_fuyao_requires_target_row_match(fuyao):
    import rt_probe as R

    fuyao["set"](_payload([_row("000001.SZ", 11.74, 11.85)]))   # 全市场返回但没目标
    v = R.probe_fuyao()
    assert v["ok"] is False
    assert "600519.SH" in v["error"]
    assert v["count_hint"] == 1        # 条数只作提示，不再当健康判据

    fuyao["set"](_payload([_row("000001.SZ", 11.74, 11.85),
                           _row("600519.SH", 1520.0, 1500.0)]))
    v = R.probe_fuyao()
    assert v["ok"] is True and v["now"] == 1520.0


def test_probe_fuyao_upstream_error(fuyao):
    import rt_probe as R

    fuyao["set"](_payload([], code=4001, message="上游限频"))
    v = R.probe_fuyao()
    assert v["ok"] is False and "限频" in v["error"]

    fuyao["set"](OSError("fuyao 超时"))
    assert R.probe_fuyao()["ok"] is False


# ---------- 旧调用点：live_hourly_analysis.quote_fallback 改为委托 ----------

def test_hourly_quote_fallback_delegates_and_keeps_float_contract(monkeypatch):
    import live_hourly_analysis as L

    seen = {}

    def _fb(code, ref=None):
        seen["args"] = (code, ref)
        return (12.3, "tencent")

    monkeypatch.setattr(Q, "fallback_quote", _fb)
    assert L.quote_fallback("600519.SH") == 12.3
    assert seen["args"][0] == "600519.SH"

    monkeypatch.setattr(Q, "fallback_quote", lambda code, ref=None: (None, ""))
    assert L.quote_fallback("600519.SH") == 0.0
