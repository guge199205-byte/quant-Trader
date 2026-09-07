#!/usr/bin/env python3
"""A股板块交易规则速查（全管线统一的单一口径）。

覆盖：板块判定、涨跌停幅度、买卖最低申报量/手数、盘后固定价格交易窗口。
所有执行路径（哨兵/整点轮/自主调仓/强平守护/延期重放）与提示词注入统一引用，
禁止在各脚本里散落硬编码（2026-09-04 科创板碎股拒单、±9.9% 统一涨跌停误判复盘）。

口径（沪深交易所交易规则）：
- 主板（沪 600/601/603/605，深 000/001/002/003）：±10%（ST ±5%）；
  买入 100 股整数倍；卖出 100 股整数倍，碎股一次性卖出
- 创业板（300/301/302）：±20%；100 股整数倍，碎股一次性卖出；盘后固定价格交易 15:05-15:30
- 科创板（688/689）：±20%；买入/卖出申报 ≥200 股、可 1 股递增，余额不足 200 股一次性卖出；
  盘后固定价格交易 15:05-15:30
- 北交所（43/83/87/92 等）：±30%；申报 ≥100 股、可 1 股递增，不足 100 股一次性卖出
- 新股上市初期涨跌幅特殊（科创板/创业板前 5 日无涨跌幅、主板首日另计）——本模块不追踪
  上市日，全新股的闸门可能不准，由人工/复盘注意。
"""
from datetime import date, datetime, time as _time

AH_START, AH_END = _time(15, 5), _time(15, 30)   # 盘后固定价格交易窗口


def board_of(code: str) -> str:
    """'star'科创板 | 'chinext'创业板 | 'bse'北交所 | 'main'主板 | 'unknown'。"""
    c = str(code).split(".")[0]
    if c.startswith(("688", "689", "689")):
        return "star"
    if c.startswith(("300", "301", "302")):
        return "chinext"
    if c.startswith(("43", "83", "87", "92", "88")) and len(c) == 6:
        return "bse"
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "unknown"


def is_star_market(code: str) -> bool:
    return board_of(code) == "star"


# 主板风险警示股带宽沿革（沪深交易所《交易规则》2026-04-24 修订，2026-07-06 生效）：
# < 2026-07-06：ST/*ST ±5%；≥ 2026-07-06：与普通股一致 ±10%。创业板/科创板/北交所
# 的风险警示股从来就是原带宽。审计历史样本时按 bar 日期解析，勿全样本一刀切。
_ST_MAIN_BOARD_WIDENED = date(2026, 7, 6)


def price_limit_pct(code: str, name: str | None = None, d: date | None = None) -> float:
    """涨跌停幅度（%），按板块+ST+日期解析：
    主板 ±10（ST：2026-07-06 前 ±5，之后 ±10）；创业板/科创板 ±20；北交所 ±30。"""
    b = board_of(code)
    if b == "star" or b == "chinext":
        return 20.0
    if b == "bse":
        return 30.0
    if name and "ST" in str(name).upper():
        eff = d or date.today()
        return 5.0 if eff < _ST_MAIN_BOARD_WIDENED else 10.0
    return 10.0


def limit_price(pre_close: float, pct: float, direction: str) -> float | None:
    """涨跌停价 = 前收盘 × (1±pct)，四舍五入到 0.01 元（ROUND_HALF_UP，非银行家舍入）。"""
    from decimal import ROUND_HALF_UP, Decimal

    if not pre_close or pre_close <= 0:
        return None
    q = Decimal(str(pre_close)) * (Decimal("1") + Decimal(str(pct)) / 100
                                   * (1 if direction == "up" else -1))
    return float(q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def at_limit_down(code: str, day_chg: float | None, name: str | None = None) -> bool:
    """当日涨跌是否已到/接近跌停（0.1pp 容差，兼容旧 -9.9 口径）。"""
    if day_chg is None:
        return False
    return day_chg <= -price_limit_pct(code, name) + 0.1


def at_limit_up(code: str, day_chg: float | None, name: str | None = None) -> bool:
    if day_chg is None:
        return False
    return day_chg >= price_limit_pct(code, name) - 0.1


def round_sell_qty(code: str, raw: int, avail: int) -> int:
    """把目标卖出股数调成交易所可受理的量。返回 0 = 无合法可卖量。

    - 科创板/北交所：≥200/≥100 股起、1 股递增 → 目标不足起报量抬到起报量；
      卖出后会留下"一次性才能卖出"的碎股尾部 → 直接一次性全清（防之后卖不掉）
    - 主板/创业板：100 股整数倍；同理碎股尾部全清
    """
    raw = min(max(int(raw or 0), 0), max(int(avail or 0), 0))
    if raw <= 0 or avail <= 0:
        return 0
    b = board_of(code)
    if b == "star":
        lot, min_qty = 200, 200
    elif b == "bse":
        lot, min_qty = 100, 100
    else:
        lot, min_qty = 100, 100  # 主板/创业板（unknown 按主板保守处理）
    if avail < min_qty:
        return avail  # 不足起报量只能一次性全卖
    qty = (raw // lot) * lot
    if qty < min_qty:
        qty = min_qty  # 目标不足起报量 → 抬到最小可申报量
    if avail - qty < lot:
        qty = avail  # 剩余会是碎股 → 一次性全清
    return min(qty, avail)


def round_buy_qty(code: str, qty: int) -> int:
    """把目标买入股数调成可申报量（资金充足性由调用方另行校验）。返回 0 = 非法。"""
    qty = max(int(qty or 0), 0)
    if qty <= 0:
        return 0
    b = board_of(code)
    if b == "star":
        return max(qty, 200)  # ≥200 股、1 股递增
    if b == "bse":
        return max(qty, 100)
    return (qty // 100) * 100  # 主板/创业板 100 股整数倍


def after_hours_window(now: datetime) -> bool:
    """盘后固定价格交易窗口：交易日 15:05-15:30（科创板/创业板标的，收盘价撮合）。"""
    if now.weekday() >= 5:
        return False
    return AH_START <= now.time() <= AH_END


def after_hours_eligible(code: str) -> bool:
    """盘后固定价格交易：2026-07-06 起由科创板扩展至全部 A 股（沪深主板/创业板/科创板）。
    北交所独立交易所不适用。"""
    return board_of(code) in ("main", "star", "chinext")


def rules_brief() -> str:
    """注入提示词的一行速览（给 agent 的规则常识，勿再逐条向工具求证）。"""
    return ("板块交易规则：主板涨跌停±10%（ST±5%）、创业板/科创板±20%、北交所±30%；"
            "买入100股整数倍（科创板最低200股、可1股递增）；卖出同口径且碎股一次性卖出；"
            "2026-07-06 起全部 A 股 15:05-15:30 盘后固定价格交易（收盘价撮合）。")


if __name__ == "__main__":
    from datetime import datetime as _dt
    print(rules_brief())
    for c in ("600309.SH", "688183.SH", "300750.SZ", "301170.SZ", "832000.BJ"):
        print(c, board_of(c), f"±{price_limit_pct(c)}%",
              "sell30%of600:", round_sell_qty(c, 180, 600),
              "buy100:", round_buy_qty(c, 100))
    print("盘后窗口 15:20:", after_hours_window(_dt(2026, 9, 4, 15, 20)),
          " 15:45:", after_hours_window(_dt(2026, 9, 4, 15, 45)),
          " 11:30:", after_hours_window(_dt(2026, 9, 4, 11, 30)))
