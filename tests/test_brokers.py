"""Broker 注册表与券商适配器测试（未配置时安全拒绝）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_tools.brokers import available_brokers, get_broker  # noqa: E402
from agent_tools.brokers.base import BrokerError  # noqa: E402


class TestBrokerRegistry:
    def test_all_brokers_registered(self):
        brokers = available_brokers()
        for expected in ["sandbox", "tdx", "futu", "tiger", "ibkr"]:
            assert expected in brokers

    def test_unknown_broker(self):
        with pytest.raises(BrokerError, match="未知 broker"):
            get_broker("not-a-broker")


class TestRealBrokerGates:
    """实盘券商：未配置/未连接时必须安全失败（不挂起、不误下单）。"""

    def test_tdx_unconfigured(self):
        import os

        old_url = os.environ.pop("TDX_BRIDGE_URL", None)
        try:
            with pytest.raises(BrokerError, match="TDX 桥未配置"):
                get_broker("tdx")
        finally:
            if old_url:
                os.environ["TDX_BRIDGE_URL"] = old_url

    def test_futu_no_password_rejects_order(self):
        f = get_broker("futu")
        with pytest.raises(BrokerError, match="交易密码未配置"):
            f.buy("sig", "d", "00700.HK", 100)

    def test_tiger_unconfigured_rejects(self):
        t = get_broker("tiger")
        with pytest.raises(BrokerError, match="未配置"):
            t.get_quote("00700", "2025-10-30", market="hk")

    def test_ibkr_unreachable_reports(self):
        ib = get_broker("ibkr")
        with pytest.raises(BrokerError):
            ib.get_cash("sig", "d")
