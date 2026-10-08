"""买入闸门纯函数（buy_gate）：两个实盘入口共用的放行判定。

背景：整点轮与 09:35 开盘轮曾各写一份判定并已漂移（开盘轮缺新开仓上限、
没接风险预算档位）。收敛成一个纯函数后，判定逻辑可被测试直接覆盖。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_buy_gate.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from buy_gate import BuyGate, check_buy  # noqa: E402

POOL = frozenset({"600362.SH", "002144.SZ"})


def _gate(**kw) -> BuyGate:
    base = {"pool_codes": POOL, "per_stock_pct": 0.2, "max_new_buys": 3}
    base.update(kw)
    return BuyGate(**base)


# ---------------------------------------------------------------- 熔断优先

def test_halted_blocks_every_buy():
    d = check_buy("600362.SH", 0.2, False, 0, set(), _gate(halted=True, halt_reason="回撤 7%"))
    assert not d.ok and "熔断" in d.reason and "回撤 7%" in d.reason


def test_halted_blocks_add_on_existing_position_too():
    """熔断是"今天别再买了"，不是"别开新仓"——加仓同样停。"""
    assert not check_buy("600362.SH", 0.2, True, 0, set(), _gate(halted=True)).ok


# ---------------------------------------------------------------- pct 归一化

def test_pct_clamped_to_budget():
    d = check_buy("600362.SH", 0.9, True, 0, set(), _gate(per_stock_pct=0.15))
    assert d.ok and d.pct == 0.15


def test_pct_zero_or_negative_rejected():
    for pct in (0, -0.1, None, "bad"):
        d = check_buy("600362.SH", pct, True, 0, set(), _gate())
        assert not d.ok and "pct" in d.reason


# ---------------------------------------------------------------- 加仓 vs 新开仓

def test_add_on_held_position_ignores_pool_and_caps():
    """持仓内加仓不受候选池/新开仓上限约束（换仓由卖出侧释放额度）。"""
    d = check_buy("000001.SZ", 0.2, True, 99, {"x", "y", "z"},
                  _gate(pool_codes=frozenset(), max_new_buys=0))
    assert d.ok and d.new_buys == 99


def test_new_position_outside_pool_rejected():
    d = check_buy("000001.SZ", 0.2, False, 0, set(), _gate())
    assert not d.ok and "候选池" in d.reason


def test_new_position_with_empty_pool_rejected():
    assert not check_buy("600362.SH", 0.2, False, 0, set(),
                         _gate(pool_codes=frozenset())).ok


# ---------------------------------------------------------------- 新开仓上限

def test_round_cap_blocks_extra_new_positions():
    d = check_buy("600362.SH", 0.2, False, 3, set(), _gate(max_new_buys=3))
    assert not d.ok and "本轮新开仓" in d.reason


def test_daily_cap_blocks_extra_new_positions():
    d = check_buy("600362.SH", 0.2, False, 0, {"600000.SH", "600001.SH"},
                  _gate(max_new_buys=2))
    assert not d.ok and "当日新开仓" in d.reason and "600000.SH" in d.reason


def test_daily_cap_allows_same_code_already_opened_today():
    """当日上限按"新开的标的只数"计：同一代码已开过，不再重复占名额。"""
    d = check_buy("600362.SH", 0.2, False, 0, {"600362.SH", "600001.SH"},
                  _gate(max_new_buys=2))
    assert d.ok


def test_new_buys_increments_only_on_allowed_open():
    d = check_buy("600362.SH", 0.2, False, 1, set(), _gate())
    assert d.ok and d.new_buys == 2
    assert check_buy("002144.SZ", 0.2, True, 1, set(), _gate()).new_buys == 1


def test_defaults_are_conservative():
    """默认构造：无池 → 只能加仓不能开仓（防调用方漏传 pool_codes 时放开买入）。"""
    d = check_buy("600362.SH", 0.2, False, 0, set(),
                  BuyGate(pool_codes=frozenset(), per_stock_pct=0.2, max_new_buys=3))
    assert not d.ok


# ---------------------------------------------------------------- 接线（整点轮）

def _buy(code, pct=0.2, reason="r"):
    return {"action": "buy", "code": code, "pct": pct, "reason": reason}


def test_hourly_executor_wires_the_gate(monkeypatch):
    """真实入口 execute_intraday_decision 必须走到闸门（池外/超额被拦，池内放行）。"""
    import live_hourly_analysis as H

    monkeypatch.setattr(H, "MAX_NEW_BUYS", 1)
    out = H.execute_intraday_decision(
        None, "test-agent-gate",
        [_buy("600362.SH"), _buy("000001.SZ"), _buy("002144.SZ")],
        [], 1e6, dry_run=True, pool_codes={"600362.SH", "002144.SZ"})
    assert [e["code"] for e in out] == ["600362.SH"]


def test_hourly_executor_blocks_buys_when_tripped(monkeypatch):
    """熔断时买入全停、卖出照常。"""
    import live_breaker
    import live_hourly_analysis as H

    monkeypatch.setattr(live_breaker, "check_and_trip", lambda *a, **k: (True, "日亏 6%"))
    out = H.execute_intraday_decision(
        None, "test-agent-gate",
        [{"action": "sell", "code": "600309.SH", "pct": 0.5, "reason": "减仓"},
         _buy("600362.SH")],
        [{"code": "600309.SH", "avail": 1000, "day_chg": 1.0, "name": "万华化学"}],
        1e6, dry_run=True, pool_codes={"600362.SH"})
    assert [e["action"] for e in out] == ["sell"]


# ------------------------------------------------- 单票集中度闸（2026-09-18 机构级）
# per_stock_pct 是"单笔占剩余额度"的比例，同一标的跨轮加仓此前不封顶（只有杠杆闸
# 兜底）。position_cap_reason：同票已有敞口 + 本单成本 ≤ cap_pct×权益。

def test_position_cap_within_limit_passes():
    from buy_gate import position_cap_reason

    assert position_cap_reason(10_000, 12_000, 100_000, 0.25) == ""   # 22% < 25%


def test_position_cap_blocks_when_projected_exceeds():
    from buy_gate import position_cap_reason

    reason = position_cap_reason(24_000, 20_000, 100_000, 0.25)

    assert reason and "¥44,000" in reason and "¥24,000" in reason
    assert "25%×权益" in reason and "¥25,000" in reason


def test_position_cap_exactly_at_limit_passes():
    from buy_gate import position_cap_reason

    assert position_cap_reason(15_000, 10_000, 100_000, 0.25) == ""   # == 上限 → 放行


def test_position_cap_fails_open_on_unusable_inputs():
    """equity/cap 不可用（≤0）或脏输入 → 放行——调用方另有杠杆/现金闸兜底，
    与杠杆闸 `if equity > 0` 同口径。"""
    from buy_gate import position_cap_reason

    assert position_cap_reason(50_000, 50_000, 0, 0.25) == ""
    assert position_cap_reason(50_000, 50_000, -1, 0.25) == ""
    assert position_cap_reason(50_000, 50_000, 100_000, 0) == ""
    assert position_cap_reason("bad", 50_000, 100_000, 0.25) == ""
    assert position_cap_reason(None, None, None, None) == ""


def test_position_cap_fails_open_on_nan_inf_and_overflow():
    """评审 LOW（2026-09-18）：NaN 穿过所有比较会产出假判定；超大整数强转溢出。
    两者都必须按"判不了 → 放行"处理，绝不误拦。"""
    from buy_gate import position_cap_reason

    nan, inf = float("nan"), float("inf")
    assert position_cap_reason(nan, 1_000, 100_000, 0.25) == ""
    assert position_cap_reason(1_000, 1_000, nan, 0.25) == ""
    assert position_cap_reason(1_000, 1_000, 100_000, nan) == ""
    assert position_cap_reason(inf, 1_000, 100_000, 0.25) == ""
    assert position_cap_reason(10 ** 400, 0, 100, 0.25) == ""   # float() 溢出


# ------------------------------------------------- 规则标识（影子代价账，2026-09-19）
# 中文 reason 是给人看的；影子账要按规则分组算成本，需要一个不受文案改动影响的
# 机器可读 id（改一个字就换一条规则 = 历史样本全部断档）。

def test_every_rejection_branch_carries_a_stable_rule_id():
    import gate_rules as GR

    cases = [
        ("熔断", check_buy("600362.SH", 0.2, False, 0, set(), _gate(halted=True)), GR.HALT_DAILY),
        ("标的边界", check_buy("600362.SH", 0.2, False, 0, set(), _gate(),
                            name="*ST海航"), GR.SYMBOL_BOUNDARY),
        ("pct 非法", check_buy("600362.SH", "bad", True, 0, set(), _gate()), GR.PCT_INVALID),
        ("pct=0", check_buy("600362.SH", 0, True, 0, set(), _gate()), GR.PCT_ZERO),
        ("池外", check_buy("000001.SZ", 0.2, False, 0, set(), _gate()), GR.POOL_NOT_MEMBER),
        ("本轮上限", check_buy("600362.SH", 0.2, False, 3, set(), _gate(max_new_buys=3)),
         GR.CAP_ROUND_NEW_BUYS),
        ("当日上限", check_buy("600362.SH", 0.2, False, 0, {"002144.SZ"}, _gate(max_new_buys=1)),
         GR.CAP_DAILY_NEW_BUYS),
    ]
    for label, d, want in cases:
        assert not d.ok, label
        assert d.rule == want, f"{label}：rule={d.rule!r}，期望 {want!r}"


def test_rejection_rules_are_registered_in_vocabulary():
    """闸门发出的每个 id 都必须在词表里（写死字符串 = 登记表查不到、报告分不了组）。"""
    import gate_rules as GR

    emitted = {check_buy("600362.SH", 0.2, False, 0, set(), _gate(halted=True)).rule,
               check_buy("600362.SH", "bad", True, 0, set(), _gate()).rule,
               check_buy("600362.SH", 0, True, 0, set(), _gate()).rule,
               check_buy("000001.SZ", 0.2, False, 0, set(), _gate()).rule,
               check_buy("600362.SH", 0.2, False, 3, set(), _gate(max_new_buys=3)).rule,
               check_buy("600362.SH", 0.2, False, 0, {"002144.SZ"}, _gate(max_new_buys=1)).rule}
    assert emitted <= set(GR.ALL)


def test_approved_buy_has_no_rule_id():
    """放行不是"某条规则放行"，rule 必须为空——否则影子账会把成功单也记一笔。"""
    d = check_buy("600362.SH", 0.2, True, 0, set(), _gate())

    assert d.ok and d.rule == ""
