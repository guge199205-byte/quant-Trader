"""live_ledger 分账逻辑冒烟测试（纯函数，不碰文件）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_ledger import (  # noqa: E402
    AGENT_QUOTA,
    agent_remaining,
    agent_used,
    agent_virtual_cash,
    find_holder,
    record_buy,
    record_sell,
)

EMPTY = {"version": 1, "agents": {}}


def test_empty_ledger_used_remaining():
    # Arrange/Act
    used = agent_used(EMPTY, "deepseek-v4-flash")
    remain = agent_remaining(EMPTY, "deepseek-v4-flash")
    # Assert
    assert used == 0.0
    assert remain == AGENT_QUOTA


def test_buy_then_used_counts_cost():
    # Arrange
    ledger = EMPTY
    # Act
    ledger = record_buy(ledger, "flash", "600183.SH", 400, 150.0, "2026-08-31T11:13")
    ledger = record_buy(ledger, "flash", "300750.SZ", 100, 360.0, "2026-08-31T11:14")
    # Assert
    assert ledger is not EMPTY  # 不可变：返回新对象
    assert agent_used(ledger, "flash") == 400 * 150.0 + 100 * 360.0
    assert agent_used(ledger, "pro") == 0.0  # 互不影响


def test_buy_add_position_uses_weighted_cost():
    # Arrange
    ledger = record_buy(EMPTY, "flash", "600183.SH", 100, 100.0, "t1")
    # Act：加仓 100 股 @ 200 → 加权成本 (100*100 + 100*200)/200 = 150
    ledger = record_buy(ledger, "flash", "600183.SH", 100, 200.0, "t2")
    # Assert
    assert ledger["agents"]["flash"]["positions"]["600183.SH"]["volume"] == 200
    assert ledger["agents"]["flash"]["positions"]["600183.SH"]["cost_price"] == 150.0
    assert agent_used(ledger, "flash") == 30000.0


def test_remaining_never_negative_when_within_quota():
    # Arrange
    ledger = record_buy(EMPTY, "flash", "600183.SH", 500, 199.0, "t1")  # 99,500
    # Act/Assert
    assert agent_remaining(ledger, "flash") == AGENT_QUOTA - 99500.0


def test_sell_partial_releases_quota():
    # Arrange
    ledger = record_buy(EMPTY, "flash", "600183.SH", 200, 100.0, "t1")
    # Act：卖 50 股 @ 120
    ledger = record_sell(ledger, "flash", "600183.SH", 50, 120.0, "t2")
    # Assert
    assert ledger["agents"]["flash"]["positions"]["600183.SH"]["volume"] == 150
    assert agent_used(ledger, "flash") == 15000.0


def test_sell_all_removes_position():
    # Arrange
    ledger = record_buy(EMPTY, "flash", "600183.SH", 200, 100.0, "t1")
    # Act
    ledger = record_sell(ledger, "flash", "600183.SH", 200, 110.0, "t2")
    # Assert
    assert "600183.SH" not in (ledger["agents"]["flash"].get("positions") or {})
    assert agent_used(ledger, "flash") == 0.0


def test_sell_unowned_returns_same_ledger():
    # Arrange
    ledger = record_buy(EMPTY, "flash", "600183.SH", 100, 50.0, "t1")
    # Act：卖不存在的股票
    ledger2 = record_sell(ledger, "flash", "999999.SH", 100, 60.0, "t2")
    # Assert
    assert ledger2 is ledger  # 原样返回
    assert agent_used(ledger, "flash") == 5000.0


def test_virtual_cash_buy_deducts_sell_adds():
    # Arrange/Act：买 100 股 @ 100 → 虚拟现金 100000-10000=90000
    ledger = record_buy(EMPTY, "flash", "600183.SH", 100, 100.0, "t1")
    # Assert
    assert ledger["agents"]["flash"]["virtual_cash"] == 90000.0
    # Act：卖 40 股 @ 110 → +4400 → 94400
    ledger = record_sell(ledger, "flash", "600183.SH", 40, 110.0, "t2")
    # Assert
    assert ledger["agents"]["flash"]["virtual_cash"] == 94400.0
    assert agent_virtual_cash(ledger, "pro") == AGENT_QUOTA  # 其他 agent 不受影响


def test_find_holder():
    # Arrange
    ledger = record_buy(EMPTY, "pro", "600183.SH", 100, 50.0, "t1")
    # Act/Assert
    assert find_holder(ledger, "600183.SH") == "pro"
    assert find_holder(ledger, "300750.SZ") is None


def test_agents_isolated():
    # Arrange/Act
    ledger = record_buy(EMPTY, "flash", "600183.SH", 100, 100.0, "t1")
    ledger = record_buy(ledger, "pro", "300750.SZ", 100, 300.0, "t2")
    # Assert
    assert agent_used(ledger, "flash") == 10000.0
    assert agent_used(ledger, "pro") == 30000.0
    assert find_holder(ledger, "600183.SH") == "flash"
    assert find_holder(ledger, "300750.SZ") == "pro"
