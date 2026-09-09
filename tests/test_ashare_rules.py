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
