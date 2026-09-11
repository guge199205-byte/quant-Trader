"""标的边界（symbol_policy）：实盘买入的**标的级**硬约束。

背景：候选池只约束"买什么"，不构成安全边界——池里出现 ST/*ST/退市整理股时
买入路径此前照单全收（`ashare_rules` 只用 ST 算涨跌停幅度，没有禁买），
`agent_tools/risk.py` 的 blacklist 又只作用于 MCP 路径。本测试锁定三条底线：
风险股禁买、操作员黑名单禁买（不依赖名称）、名称缺失时放行（fail-open 取舍）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_symbol_policy.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from buy_gate import BuyGate, check_buy  # noqa: E402
from symbol_policy import (SymbolPolicy, check_symbol, filter_pool,  # noqa: E402
                           is_risky_name, load_policy, load_policy_with_risk)

DEFAULT = SymbolPolicy()


# ---------------------------------------------------------------- 风险股识别

def test_st_variants_detected():
    for n in ("ST中珠", "*ST海航", "SST前锋", "S*ST前锋", "st中珠", " *ST 海航 "):
        assert is_risky_name(n), n


def test_delisting_names_detected():
    for n in ("退市海润", "海润退"):
        assert is_risky_name(n), n


def test_normal_names_not_flagged():
    for n in ("贵州茅台", "万华化学", "宁德时代", "中国平安", "隆基绿能", ""):
        assert not is_risky_name(n), n


def test_missing_name_is_not_risky():
    """名称取不到 → 不算风险股（放行；见模块 fail-open 说明）。"""
    for n in (None, "", "   "):
        assert not is_risky_name(n)


# ---------------------------------------------------------------- 判定

def test_st_buy_rejected_with_reason():
    r = check_symbol("600666.SH", "*ST瑞德", DEFAULT)
    assert "ST" in r and "600666.SH" in r


def test_blacklist_rejected_without_name():
    """黑名单按代码匹配：名称表拉不到也照样拦得住。"""
    p = SymbolPolicy(block_buy=frozenset({"600666.SH"}))
    assert "黑名单" in check_symbol("600666.SH", None, p)


def test_blacklist_code_matching_ignores_case_and_space():
    p = SymbolPolicy(block_buy=frozenset({"600666.SH"}))
    assert "黑名单" in check_symbol(" 600666.sh ", "正常名称", p)


def test_normal_symbol_passes():
    assert check_symbol("600309.SH", "万华化学", DEFAULT) == ""


def test_allow_st_operator_override():
    p = SymbolPolicy(allow_st=True)
    assert check_symbol("600666.SH", "*ST瑞德", p) == ""


# ---------------------------------------------------------------- 配置加载

def test_load_policy_missing_file_is_default(tmp_path):
    p = load_policy(tmp_path / "nope.json")
    assert p.block_buy == frozenset() and p.allow_st is False


def test_load_policy_corrupt_file_is_default(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    assert load_policy(f) == SymbolPolicy()


def test_load_policy_reads_and_normalizes(tmp_path):
    f = tmp_path / "ok.json"
    f.write_text(json.dumps({"block_buy": [" 600666.sh ", "", None],
                             "allow_st": True, "_note": "x"}), encoding="utf-8")
    p = load_policy(f)
    assert p.block_buy == frozenset({"600666.SH"}) and p.allow_st is True


def test_load_policy_bad_block_type_is_empty(tmp_path):
    f = tmp_path / "ok.json"
    f.write_text(json.dumps({"block_buy": "600666.SH"}), encoding="utf-8")
    assert load_policy(f).block_buy == frozenset()


# ---------------------------------------------------------------- 闸门接线

def _gate(**kw) -> BuyGate:
    base = {"pool_codes": frozenset({"600666.SH", "600309.SH"}),
            "per_stock_pct": 0.2, "max_new_buys": 3}
    base.update(kw)
    return BuyGate(**base)


def test_gate_blocks_st_new_position():
    d = check_buy("600666.SH", 0.2, False, 0, set(), _gate(), name="*ST瑞德")
    assert not d.ok and "标的边界" in d.reason


def test_gate_blocks_st_add_on_existing_position():
    """禁买是"这只不碰"，不是"别开新仓"——已持有的 ST 也不许加仓（卖出照常）。"""
    d = check_buy("600666.SH", 0.2, True, 0, set(), _gate(), name="ST瑞德")
    assert not d.ok and "标的边界" in d.reason


def test_gate_blocks_st_even_when_caller_forgot_policy():
    """默认 policy 即禁 ST：调用方忘读配置也守得住底线。"""
    g = BuyGate(pool_codes=frozenset({"600666.SH"}), per_stock_pct=0.2, max_new_buys=3)
    assert not check_buy("600666.SH", 0.2, False, 0, set(), g, name="*ST瑞德").ok


def test_gate_allows_normal_pool_member():
    d = check_buy("600309.SH", 0.2, False, 0, set(), _gate(), name="万华化学")
    assert d.ok and d.pct == 0.2


def test_gate_unknown_name_passes():
    """名称缺失 → 放行（fail-open）：不能因名称表拉不到就停掉全天买入。"""
    assert check_buy("600666.SH", 0.2, False, 0, set(), _gate()).ok


def test_gate_operator_blacklist_blocks_pool_member():
    p = SymbolPolicy(block_buy=frozenset({"600309.SH"}))
    d = check_buy("600309.SH", 0.2, False, 0, set(), _gate(policy=p), name="万华化学")
    assert not d.ok and "黑名单" in d.reason


def test_gate_sell_side_unaffected():
    """标的边界只拦买入：闸门根本不参与卖出判定（卖出由 execute_intraday_decision 直通）。"""
    import live_hourly_analysis as H

    out = H.execute_intraday_decision(
        None, "test-agent-policy",
        [{"action": "sell", "code": "600666.SH", "pct": 1.0, "reason": "清仓"}],
        [{"code": "600666.SH", "avail": 1000, "day_chg": 1.0, "name": "*ST瑞德"}],
        1e6, dry_run=True, pool_codes={"600666.SH"})
    assert [e["action"] for e in out] == ["sell"]


def test_hourly_executor_blocks_st_buy_via_names():
    """真实入口：池内 ST 标的按 names 表识别后拦下（名称来自持仓行则直接用持仓名）。"""
    import live_hourly_analysis as H

    out = H.execute_intraday_decision(
        None, "test-agent-policy",
        [{"action": "buy", "code": "600666.SH", "pct": 0.2, "reason": "追涨"},
         {"action": "buy", "code": "600309.SH", "pct": 0.2, "reason": "正常"}],
        [], 1e6, dry_run=True, pool_codes={"600666.SH", "600309.SH"},
        names={"600666.SH": "*ST瑞德", "600309.SH": "万华化学"})
    assert [e["code"] for e in out] == ["600309.SH"]


# ---------------------------------------------------------------- 事件风险清单

def _risk_policy(*pairs) -> SymbolPolicy:
    return SymbolPolicy(risk=tuple(pairs))


def test_risk_code_blocked_with_reason():
    p = _risk_policy(("688795", "09/10解禁3.4%流通盘（首发原股东限售股份）"))
    r = check_symbol("688795.SH", "摩尔线程", p)
    assert "事件风险" in r and "解禁" in r and "688795.SH" in r


def test_risk_matching_ignores_exchange_suffix_and_case():
    """风险表按 6 位代码匹配：数据源后缀口径不统一（.SH/.SZ/无后缀）也要拦得住。"""
    p = _risk_policy(("688795", "解禁"))
    assert check_symbol("688795", "摩尔线程", p)
    assert check_symbol(" 688795.sh ", "摩尔线程", p)


def test_risk_unrelated_code_passes():
    p = _risk_policy(("688795", "解禁"))
    assert check_symbol("600309.SH", "万华化学", p) == ""


def test_gate_blocks_risk_listed_buy():
    p = _risk_policy(("600309", "负面新闻：高管处罚"))
    d = check_buy("600309.SH", 0.2, False, 0, set(), _gate(policy=p), name="万华化学")
    assert not d.ok and "事件风险" in d.reason


def test_load_policy_with_risk_merges_risk_file(tmp_path):
    conf = tmp_path / "live_symbols.json"
    conf.write_text(json.dumps({"block_buy": ["600666.SH"]}), encoding="utf-8")
    risk = tmp_path / "risk_block.json"
    risk.write_text(json.dumps({"items": {
        "688795": {"reason": "解禁跌停", "kind": "news", "expire": "2099-01-01"},
    }}), encoding="utf-8")
    p = load_policy_with_risk(conf, risk)
    assert p.block_buy == frozenset({"600666.SH"})
    assert "解禁跌停" in check_symbol("688795.SH", None, p)


def test_load_policy_with_risk_missing_risk_file_is_operator_policy(tmp_path):
    conf = tmp_path / "live_symbols.json"
    conf.write_text(json.dumps({"block_buy": ["600666.SH"]}), encoding="utf-8")
    p = load_policy_with_risk(conf, tmp_path / "nope.json")
    assert p.block_buy == frozenset({"600666.SH"}) and p.risk == ()


# ---------------------------------------------------------------- 候选池入口过滤
# 2026-09-11 用户口径：「agent 选出来的股票、跟踪的股票，不需要 ST 的……都黑名单」。
# 此前边界只在**下单时**拦（buy_gate）：池子仍把 ST/风险股原样推给模型和 L2 采集，
# 模型看得见就会考虑、白挨一轮"提了买、被毙掉"；采集侧还给这些票浪费时间窗。

def _pool():
    return [{"code": "600309.SH", "name": "万华化学"},
            {"code": "600666.SH", "name": "*ST瑞德"},
            {"code": "001312.SZ", "name": "福恩股份"}]


def test_filter_pool_drops_st_row_with_reason():
    kept, dropped = filter_pool(_pool(), DEFAULT)
    assert [r["code"] for r in kept] == ["600309.SH", "001312.SZ"]
    row, reason = dropped[0]
    assert row["code"] == "600666.SH" and "ST" in reason


def test_filter_pool_drops_blacklist_and_risk_rows():
    p = SymbolPolicy(block_buy=frozenset({"600309.SH"}),
                     risk=(("001312", "09/12解禁5.0%流通盘"),))
    kept, dropped = filter_pool(_pool(), p)
    assert kept == [] and len(dropped) == 3          # 黑名单 + ST + 事件风险各一
    assert any("黑名单" in r for _, r in dropped)
    assert any("事件风险" in r for _, r in dropped)


def test_filter_pool_missing_name_fails_open():
    """名称缺失 → 保留（与 check_symbol 同口径 fail-open：不能因名称表拉不到清空池子）。"""
    kept, dropped = filter_pool([{"code": "600666.SH", "name": ""}], DEFAULT)
    assert len(kept) == 1 and dropped == []


def test_filter_pool_skips_malformed_rows_and_empty_input():
    kept, dropped = filter_pool([None, "600309.SH", {"name": "无代码"}], DEFAULT)
    assert len(kept) == 1 and dropped == []          # 非 dict 丢弃；无代码行 fail-open 保留
    assert filter_pool([], DEFAULT) == ([], [])
    assert filter_pool(None, DEFAULT) == ([], [])


def test_filter_pool_does_not_mutate_input():
    pool = _pool()
    filter_pool(pool, DEFAULT)
    assert len(pool) == 3
