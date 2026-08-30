"""风控模块测试。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_tools.risk import RiskPolicy, get_initial_cash, get_current_equity, pre_trade_check


def make_policy(**kwargs) -> RiskPolicy:
    return RiskPolicy(enabled=True, **kwargs)


class TestRiskPolicy:
    def test_buy_ok(self):
        p = make_policy()
        assert p.check_buy("AAPL", 5, 100.0, 10000, 10000, 0, 0) is None

    def test_order_ratio_exceeded(self):
        p = make_policy(max_order_ratio=0.2)
        err = p.check_buy("AAPL", 50, 100.0, 10000, 10000, 0, 0)
        assert err and "单笔" in err

    def test_position_ratio_exceeded(self):
        p = make_policy(max_position_ratio=0.2)
        # 已持 15 股(1500)，再买 10 股(1000) = 2500 > 2000
        err = p.check_buy("AAPL", 10, 100.0, 10000, 10000, 1500, 0.15)
        assert err and "持仓" in err

    def test_cash_reserve(self):
        p = make_policy(min_cash_reserve=500.0, max_order_ratio=0.8, max_position_ratio=0.8)
        err = p.check_buy("AAPL", 6, 100.0, 1000, 1000, 0, 0)
        assert err and "保留" in err

    def test_blacklist(self):
        p = make_policy(blacklist=["PLTR"])
        err = p.check_buy("PLTR", 1, 100.0, 10000, 10000, 0, 0)
        assert err and "黑名单" in err

    def test_sell_over_held(self):
        p = make_policy()
        assert p.check_sell("AAPL", 10, 5) is not None
        assert p.check_sell("AAPL", 3, 5) is None

    def test_daily_loss_circuit(self):
        p = make_policy(max_daily_loss_pct=0.05)
        # 亏 10% 熔断买入；卖出放行
        assert p.check_daily_loss(10000, 9000, "buy") is not None
        assert p.check_daily_loss(10000, 9000, "sell") is None

    def test_disabled_policy(self):
        p = RiskPolicy(enabled=False)
        assert p.check_buy("AAPL", 999, 1e9, 1, 1, 0, 0) is None


class TestPositionHelpers:
    def test_get_initial_cash(self, tmp_path):
        pf = tmp_path / "position.jsonl"
        pf.write_text(
            '{"date":"2025-10-30","id":0,"positions":{"AAPL":0,"CASH":10000.0}}\n'
            '{"date":"2025-10-30","id":1,"this_action":{"action":"buy","symbol":"AAPL","amount":1},"positions":{"AAPL":1,"CASH":9000.0}}\n',
            encoding="utf-8",
        )
        assert get_initial_cash(pf) == 10000.0

    def test_get_initial_cash_missing(self, tmp_path):
        assert get_initial_cash(tmp_path / "nope.jsonl") == 0.0
