"""杠杆强减的「已计划卖出」口径必须与执行段一致（2026-09-12 审查 HIGH-1）。

`compute_forced_trims` 会从超限市值里扣除模型**已主动计划卖出**的部分，避免
「模型自觉卖 + 系统强制减」双卖（14:00 教训：688183 被 800 股砍掉 57%）。
旧代码用 `min(max(d["pct"], 0), 1)` 算 planned：模型说「卖出」但漏给 pct 时记 0，
而执行段（`sell_fraction`）按**清仓**执行 → 系统再砍一刀就是同一持仓的双倍卖出。

`planned_sell_values` 就是把这个口径抽成单点：缺 pct → 整仓市值、脏 pct → 0
（执行段会跳过该决策，不能算已计划卖出）、明说比例 → 按比例市值。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_forced_trims.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_hourly_analysis as H  # noqa: E402

CODE = "001312.SZ"          # 深市主板（100 股整手）
PRICE = 10.0
VOLUME = 10000
CASH = -50000.0             # 负现金（融资形态）→ 持仓 100,000 > 权益 50,000 × 1.5


def _holdings():
    return [{"code": CODE, "name": "福恩股份", "volume": VOLUME, "cost": PRICE,
             "price": PRICE, "pnl_pct": 0.0, "day_chg": 0.0, "avail": VOLUME}]


def _sel(**kw):
    d = {"action": "sell", "code": CODE, "reason": "t"}
    d.update(kw)
    return d


# ---------- planned_sell_values 口径 ----------

def test_missing_pct_counts_as_full_market_value():
    """模型说卖出没给 pct → 执行段按清仓，planned 必须记整仓市值（不是 0）。"""
    p = H.planned_sell_values([_sel(pct=0.0, pct_given=False)], _holdings())
    assert p == {CODE: PRICE * VOLUME}


def test_dirty_pct_counts_zero_because_execution_skips_it():
    """脏 pct 的执行段会跳过并留痕 → 不计入已计划卖出（强减照常按需触发）。"""
    p = H.planned_sell_values(
        [_sel(pct=0.0, pct_given=False, pct_bad_raw="'0.3股'")], _holdings())
    assert p.get(CODE, 0) == 0


def test_explicit_ratio_counts_proportional_value():
    p = H.planned_sell_values([_sel(pct=0.3, pct_given=True)], _holdings())
    assert p == {CODE: PRICE * 3000}


def test_non_sell_and_unavailable_rows_are_ignored():
    hs = _holdings()
    hs[0]["avail"] = 0                      # T+1 不可卖 → 执行段不会卖
    assert H.planned_sell_values([_sel(pct=1.0)], hs) == {}
    assert H.planned_sell_values(
        [{"action": "buy", "code": CODE, "pct": 0.2}], _holdings()) == {}


# ---------- 与 compute_forced_trims 的联动（HIGH-1 回归钉） ----------

def test_scenario_leverage_breach_triggers_trim_without_planned_sells():
    """场景自检：无任何模型决策时强减确实触发（否则下面的用例是假绿）。"""
    trims = H.compute_forced_trims(_holdings(), CASH, {})
    assert trims and trims[0]["action"] == "sell" and trims[0]["code"] == CODE


def test_full_liquidation_decision_suppresses_forced_trim():
    """HIGH-1 回归：模型清仓（漏给 pct）+ 系统强减 = 双卖。

    planned 记整仓市值后，need 已为负 → 不再产生强减决策；
    旧口径记 0 时这里会多出一条 3000+ 股的强减。"""
    decisions = [_sel(pct=0.0, pct_given=False)]
    planned = H.planned_sell_values(decisions, _holdings())

    assert H.compute_forced_trims(_holdings(), CASH, planned) == []


def test_dirty_pct_decision_does_not_suppress_forced_trim():
    """脏 pct 决策会被执行段跳过 → 强减必须照常兜住杠杆（planned=0）。"""
    decisions = [_sel(pct=0.0, pct_given=False, pct_bad_raw="'0.3股'")]
    planned = H.planned_sell_values(decisions, _holdings())

    assert H.compute_forced_trims(_holdings(), CASH, planned)


def test_partial_planned_sell_still_trims_the_gap():
    """模型只卖 30% → planned 扣 30,000，剩余缺口由强减补上（不是全额双卖）。"""
    decisions = [_sel(pct=0.3, pct_given=True)]
    planned = H.planned_sell_values(decisions, _holdings())

    trims = H.compute_forced_trims(_holdings(), CASH, planned)
    assert trims
    assert trims[0]["pct"] < 1.0            # 只补缺口，不是再砍一整仓
    assert "已扣除模型主动卖出" in trims[0]["reason"]
