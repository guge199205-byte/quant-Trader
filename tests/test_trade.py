"""交易执行与手续费测试（独立 runtime_env，隔离真实数据）。"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试用独立 runtime_env，避免污染真实状态
TEST_RUNTIME = Path(__file__).resolve().parents[1] / "runtime_env_test.json"
os.environ["RUNTIME_ENV_PATH"] = str(TEST_RUNTIME)

from tools.general_tools import write_config_value  # noqa: E402
from agent_tools.brokers.sandbox import SandboxBroker  # noqa: E402
from agent_tools.brokers.base import BrokerError  # noqa: E402

SIG = "pytest-agent"


@pytest.fixture(autouse=True)
def clean_state():
    write_config_value("SIGNATURE", SIG)
    write_config_value("TODAY_DATE", "2025-10-30 10:00:00")
    write_config_value("MARKET", "us")
    write_config_value("LOG_PATH", "./data/agent_data")
    data_dir = Path(__file__).resolve().parents[1] / "data" / "agent_data" / SIG
    import shutil

    if data_dir.exists():
        shutil.rmtree(data_dir)
    yield
    if data_dir.exists():
        shutil.rmtree(data_dir)
    if TEST_RUNTIME.exists():
        TEST_RUNTIME.unlink()


class TestSandboxBroker:
    def test_buy_updates_position(self):
        b = SandboxBroker({"initial_cash": 10000.0})
        result = b.buy(SIG, "2025-10-30 10:00:00", "AAPL", 5)
        assert result["AAPL"] == 5
        assert result["CASH"] < 10000.0  # 现金减少（含滑点+手续费）

    def test_buy_incurs_fees(self):
        b = SandboxBroker({"initial_cash": 10000.0})
        b.buy(SIG, "2025-10-30 10:00:00", "AAPL", 5)
        cash = b.get_cash(SIG, "2025-10-30 10:00:00")
        # 无费用时 5 股 ≈ 1357，扣费用后应更少
        assert cash < 10000 - 1350

    def test_sell_reduces_position(self):
        b = SandboxBroker({"initial_cash": 10000.0})
        b.buy(SIG, "2025-10-30 10:00:00", "AAPL", 5)
        result = b.sell(SIG, "2025-10-30 10:00:00", "AAPL", 2)
        assert result["AAPL"] == 3
        assert result["CASH"] > b.get_cash(SIG, "2025-10-30 10:00:00") - 100  # 增加

    def test_cn_lot_rule(self):
        b = SandboxBroker({"initial_cash": 100000.0})
        write_config_value("MARKET", "cn")
        with pytest.raises(BrokerError, match="multiples of 100"):
            b.buy(SIG, "2025-10-30 10:00:00", "600519.SH", 50)

    def test_unknown_symbol(self):
        b = SandboxBroker({"initial_cash": 10000.0})
        with pytest.raises(BrokerError, match="not found"):
            b.buy(SIG, "2025-10-30 10:00:00", "NOTEXIST", 1)

    def test_position_and_cash(self):
        b = SandboxBroker({"initial_cash": 10000.0})
        b.buy(SIG, "2025-10-30 10:00:00", "AAPL", 5)
        positions = b.get_positions(SIG, "2025-10-30 10:00:00")
        assert positions.get("AAPL") == 5
        assert b.get_cash(SIG, "2025-10-30 10:00:00") > 0
