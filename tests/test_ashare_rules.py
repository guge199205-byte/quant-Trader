"""ashare_rules 单测：2026 新规口径（ST 主板带宽沿革/盘后定价扩展/申报量/提示词）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_ashare_rules.py -q
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ashare_rules as R  # noqa: E402


def test_board_of():
    assert R.board_of("600309.SH") == "main"
    assert R.board_of("688183.SH") == "star"
    assert R.board_of("300750.SZ") == "chinext"
    assert R.board_of("832000.BJ") == "bse"


def test_price_limit_st_main_widened_2026():
    # 沪深《交易规则》2026-04-24 修订、2026-07-06 实施：主板 ST ±5% → ±10%
    assert R.price_limit_pct("600309.SH", "万华化学", date(2026, 6, 30)) == 10.0
    assert R.price_limit_pct("000001.SZ", "ST某某", date(2026, 6, 30)) == 5.0
    assert R.price_limit_pct("000001.SZ", "*ST某某", date(2026, 7, 6)) == 10.0
    assert R.price_limit_pct("600000.SH", "ST某某", date(2026, 9, 7)) == 10.0
    # 创业板/科创板/北交所风险警示带宽不变
    assert R.price_limit_pct("300001.SZ", "ST某某", date(2026, 6, 1)) == 20.0
    assert R.price_limit_pct("688001.SH", "ST某某", date(2026, 6, 1)) == 20.0
    assert R.price_limit_pct("832000.BJ", "ST某某", date(2026, 6, 1)) == 30.0


def test_price_limit_st_lookup_fallback():
    # name 缺省 → 静态名称表兜底（表里没有的按非 ST）
    pct = R.price_limit_pct("600309.SH", None, date(2026, 9, 7))
    assert pct == 10.0


def test_limit_price_rounding():
    assert R.limit_price(100.0, 10.0, "up") == 110.0
    assert R.limit_price(10.03, 10.0, "down") == 9.03  # HALF_UP 到分
    assert R.limit_price(0, 10.0, "up") is None


def test_at_limit_tolerances():
    assert R.at_limit_down("600309.SH", -9.95, "万华化学")
    assert not R.at_limit_down("600309.SH", -9.0, "万华化学")
    assert R.at_limit_up("300750.SZ", 19.95, "宁德时代")


def test_after_hours_eligible_all_a_2026():
    # 2026-07-06 起盘后固定价格交易扩展至全部 A股（北交所除外）
    assert R.after_hours_eligible("600309.SH")
    assert R.after_hours_eligible("000001.SZ")
    assert R.after_hours_eligible("688183.SH")
    assert R.after_hours_eligible("300750.SZ")
    assert not R.after_hours_eligible("832000.BJ")
    assert R.after_hours_window(datetime(2026, 9, 7, 15, 20))
    assert not R.after_hours_window(datetime(2026, 9, 7, 15, 31))


def test_round_qty():
    assert R.round_sell_qty("688183.SH", 150, 600) == 200  # 不足起报量抬到 200（剩余非碎股不全清）
    assert R.round_sell_qty("688183.SH", 150, 220) == 220  # 卖后剩 20 股碎股 → 一次性全清
    assert R.round_sell_qty("600309.SH", 250, 600) == 200  # 主板 100 股整数倍
    assert R.round_buy_qty("688183.SH", 150) == 200        # 科创板起报 200
    assert R.round_buy_qty("600309.SH", 150) == 100


def test_round_sell_qty_nearest_lot_ties_down():
    """2026-09-08 回归：600×33% 意图 199 股被地板取整成 100（一半），模型对不上账。
    主板/创业板改最近整手（恰半手向下）；科创板/北交所 1 股递增，意图本身合法。"""
    assert R.round_sell_qty("600309.SH", 199, 600) == 200  # 实录事故量：最近整手向上
    assert R.round_sell_qty("600309.SH", 150, 600) == 100  # 恰半手向下，不放大意图
    assert R.round_sell_qty("600309.SH", 199, 500) == 200
    assert R.round_sell_qty("600309.SH", 30, 600) == 100   # 不足起报量抬升（原语义不变）
    assert R.round_sell_qty("688183.SH", 350, 600) == 350  # 科创板 1 股递增：不再 floor 到 200
    assert R.round_sell_qty("430047.BJ", 550, 800) == 550  # 北交所 1 股递增
    assert R.round_sell_qty("688183.SH", 550, 600) == 600  # 卖后剩 50 <200 碎股 → 全清
    assert R.round_sell_qty("600309.SH", 100, 130) == 130  # 送股后零碎持仓（130 非整手）→ 全清


def test_min_buy_qty_by_board():
    """最小买入申报量：科创板 200 股，其余（主板/创业板/北交所/未知）100 股。"""
    assert R.min_buy_qty("688183.SH") == 200
    assert R.min_buy_qty("689009.SH") == 200
    assert R.min_buy_qty("600309.SH") == 100
    assert R.min_buy_qty("300750.SZ") == 100
    assert R.min_buy_qty("832000.BJ") == 100
    assert R.min_buy_qty("") == 100  # 未知按主板保守处理


def test_min_buy_cost_uses_board_min_qty():
    # 按资金量筛候选的统一口径（filter_affordable / compute_order 共用）
    assert R.min_buy_cost("688183.SH", 50) == 10000.0
    assert R.min_buy_cost("600309.SH", 50) == 5000.0
    assert R.min_buy_cost("600309.SH", "12.34") == 1234.0  # 桥返回字符串价
    assert R.MIN_LOT_SLACK > 1.0  # 限价按现价+1% 报，容差必须 >1


def test_min_buy_cost_bad_price_returns_zero():
    """价格非法/缺失返回 0：调用方按"无法判定→放行"处理，绝不臆造买不起。"""
    for bad in (None, "", 0, -1, "bad", float("nan")):
        assert R.min_buy_cost("600309.SH", bad) == 0.0


def test_rules_brief_mentions_2026_rules():
    brief = R.rules_brief()
    assert "ST/*ST 2026-07-06 起同步放宽至±10%" in brief
    assert "扩展至全部A股" in brief
    assert "≤20万股" in brief  # 北交所风警股限额（待实施，提示词先知）


def test_protect_sell_price_is_limit_down():
    """protect_sell_price = 跌停价（价格**硬下限**，用于夹取/跌停排队）。

    注意它不是「可报出的卖价」——连续竞价有效竞价范围是 [基准价×98%, 涨停价]，
    报跌停价会废单（2026-09-21 002074 实录 42 笔）。可卖报价用 aggressive_sell_price。"""
    assert R.protect_sell_price("600309.SH", 75.00) == 67.50   # 主板 ±10%
    assert R.protect_sell_price("688183.SH", 20.00) == 16.00   # 科创板 ±20%
    assert R.protect_sell_price("300750.SZ", 100.00) == 80.00  # 创业板 ±20%
    assert R.protect_sell_price("832000.BJ", 10.00) == 7.00    # 北交所 ±30%
    assert R.protect_sell_price("600309.SH", 10.005) == 9.00   # 四舍五入到分
    assert R.protect_sell_price("600309.SH", 0) is None        # 无昨收 → None（调用方降级）
    assert R.protect_sell_price("600309.SH", None) is None


def test_aggressive_sell_price_is_market_minus_one_percent():
    """卖出可报单价 = 现价×0.99（HALF_UP 到分），落在 98% 有效竞价范围之内。"""
    # 主板：跌停 67.50 远低于现价 → 取现价-1%
    assert R.aggressive_sell_price("600309.SH", 75.00, 76.00) == 75.24
    # 创业板 ±20%：跌停 80.00 同样不约束
    assert R.aggressive_sell_price("300750.SZ", 100.00, 99.00) == 98.01


def test_aggressive_sell_price_floor_binds_near_limit_down():
    """近跌停：现价-1% 会低于跌停价 → 取跌停价（此时基准价贴近跌停，仍在带内）。"""
    # 001312.SZ：昨收 17.22（跌停 15.50），现价 15.60 → 15.60×0.99=15.44 < 15.50
    assert R.aggressive_sell_price("001312.SZ", 17.22, 15.60) == 15.50
    # 跌停价同时是合法下沿：不低于现价的 98%
    assert R.aggressive_sell_price("001312.SZ", 17.22, 15.60) >= round(15.60 * 0.98, 2)


def test_aggressive_sell_price_002074_regression():
    """2026-09-21 实盘回归：002074 止损触发（市价 26.26）旧口径报跌停价 23.53 全废。

    新口径 = 26.26×0.99 = 26.00：既 ≠ 跌停价，又在 [0.98×现价, 涨停] 之内。"""
    px = R.aggressive_sell_price("002074.SZ", 26.14, 26.26)
    assert px == 26.00
    assert R.protect_sell_price("002074.SZ", 26.14) == 23.53   # 旧口径（会废单）
    assert px != 23.53
    assert px >= round(26.26 * 0.98, 2)   # 25.73 — 落在有效竞价范围（下沿）内


def test_aggressive_sell_price_missing_inputs():
    """昨收缺失 → 纯现价口径（无限价夹取）；现价缺失/非法 → None（调用方降级）。

    2026-09-21 审查 MEDIUM-3：旧契约在现价缺失时退回跌停价——那正是当天 42 笔
    废单的报价，等于把「最激进可成交价」退化成已知废单价。现在一律 None，
    调用方（哨兵/整点轮/清仓）各自走同口径兜底，不得凭空造价。"""
    assert R.aggressive_sell_price("600309.SH", None, 10.00) == 9.90
    assert R.aggressive_sell_price("600309.SH", 75.00, None) is None
    assert R.aggressive_sell_price("600309.SH", 75.00, 0) is None
    assert R.aggressive_sell_price("600309.SH", 75.00, float("nan")) is None
    assert R.aggressive_sell_price("600309.SH", None, None) is None
