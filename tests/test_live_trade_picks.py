"""live_trade_picks.compute_order 单测：下单量合规化 + 资金量硬闸（2026-09-09）。

背景：每 agent ¥10 万额度、单票 ≤20% 剩余额度 → 单票预算 ~¥2 万。池子里的高价票
（宁德时代 ¥363、中际旭创 ¥845）与科创板票（最小 200 股）常常一手就超预算，
提示词侧 filter_affordable 已整行剔除，这里兜底：模型从对话历史报出的代码也拦得住。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_trade_picks.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "agent_tools"))

import live_trade_picks as T  # noqa: E402


def _bars(price: float, prev: float | None = None) -> list:
    prev = price if prev is None else prev
    return [{"close": prev, "open": prev, "volume": 1000},
            {"close": price, "open": price, "volume": 1000}]


def test_main_board_pricey_rejected_with_min_lot_message():
    # 单票预算 = 100000×0.2 = ¥20,000；¥300 的票一手 ¥30,000 → 买不起
    o = T.compute_order(_bars(300.0), 100000.0, 0.2, "600309.SH")
    assert o["ok"] is False
    assert "资金不足" in o["reason"]
    assert "最小可买 100 股需 ¥30,000" in o["reason"]
    assert "单票预算 ¥20,000" in o["reason"]


def test_star_market_min_200_shares_used():
    # 科创板最小 200 股：¥120×200 = ¥24,000 > ¥20,000 → 拒（¥100 时可买）
    o = T.compute_order(_bars(120.0), 100000.0, 0.2, "688183.SH")
    assert o["ok"] is False and "最小可买 200 股需 ¥24,000" in o["reason"]


def test_affordable_main_board_still_passes():
    o = T.compute_order(_bars(10.0), 100000.0, 0.2, "600309.SH")
    assert o["ok"] is True and o["volume"] == 2000  # 100 股整数倍
    assert o["limit_price"] == 10.1                 # 限价 = 现价 +1%


def test_affordable_star_market_passes_at_min_lot():
    o = T.compute_order(_bars(90.0), 100000.0, 0.2, "688183.SH")
    assert o["ok"] is True and o["volume"] == 222   # ≥200 股、1 股递增


def test_small_pct_still_blocked_by_budget():
    """模型自降 pct（5% → 预算 ¥5,000）时同样拦——口径跟 pct 走，不写死 20%。"""
    o = T.compute_order(_bars(60.0), 100000.0, 0.05, "600309.SH")
    assert o["ok"] is False and "单票预算 ¥5,000" in o["reason"]


def test_limit_up_down_and_suspension_still_take_priority():
    # 涨停/跌停/停牌 先于资金判据（老口径不变）
    assert "涨停" in T.compute_order(_bars(11.0, 10.0), 100000.0, 0.2, "600309.SH")["reason"]
    assert "跌停" in T.compute_order(_bars(9.0, 10.0), 100000.0, 0.2, "600309.SH")["reason"]
    halted = [{"close": 10.0, "open": 10.0, "volume": 1000},
              {"close": 10.0, "open": 10.0, "volume": 0}]
    assert "停牌" in T.compute_order(halted, 100000.0, 0.2, "600309.SH")["reason"]


# ------------------------------------ 规则标识（影子代价账，2026-09-19）
# 拒单原因（中文句子）给人看；影子账按稳定 id 分组算"这条闸花了多少钱"。
# 同 buy_gate.BuyDecision.rule：改文案不能让历史样本断档。

def test_rejection_carries_stable_rule_id():
    import gate_rules as GR

    cases = [
        (_bars(300.0), "600309.SH", GR.BUDGET_BELOW_MIN_LOT),      # 买不起一手
        (_bars(11.0, 10.0), "600309.SH", GR.LIMIT_UP),
        (_bars(9.0, 10.0), "600309.SH", GR.LIMIT_DOWN),
        ([{"close": 10.0, "open": 10.0, "volume": 1000},
          {"close": 10.0, "open": 10.0, "volume": 0}], "600309.SH", GR.SYMBOL_HALTED),
        ([], "600309.SH", GR.DATA_NO_QUOTE),                       # K线不足
    ]
    for bars, code, want in cases:
        o = T.compute_order(bars, 100000.0, 0.2, code)
        assert o["ok"] is False and o["rule"] == want, (bars, o)


def test_dirty_price_is_data_no_quote():
    """价格非有限数（桥返回 NaN/字符串）→ data.no_quote，不是"资金不足"。"""
    import gate_rules as GR

    nan_bars = [{"close": 10.0, "open": 10.0, "volume": 1000},
                {"close": float("nan"), "open": 10.0, "volume": 1000}]
    o = T.compute_order(nan_bars, 100000.0, 0.2, "600309.SH")

    assert o["ok"] is False and o["rule"] == GR.DATA_NO_QUOTE


def test_approved_order_has_no_rule_id():
    o = T.compute_order(_bars(10.0), 100000.0, 0.2, "600309.SH")

    assert o["ok"] is True and "rule" not in o
