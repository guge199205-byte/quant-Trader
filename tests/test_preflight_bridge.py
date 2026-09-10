"""盘前健检的四项判定（scripts/preflight_bridge.py）。

2026-09-10 实录：开盘前没验过执行链路，账户通道整日 asset=0，
7 轮盘中分析 + 3 次 09:35 调仓 + 全部哨兵条件位静默哑火 → 全天零成交。
本文件测「验什么、怎么判」的部分（纯函数），网络/文件 IO 留在 main()。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_preflight_bridge.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_bridge as P  # noqa: E402

LEDGER = {"agents": {
    "deepseek-v4-pro": {"positions": {"001312.SZ": {"volume": 1100}}},
    "glm-5.3-flash": {"positions": {"600309.SH": {"volume": 500}}},
    "deepseek-v4-flash": {"positions": {}},
}}
ACCT = {"asset": {"asset": 296732.4, "cash": 1200.0},
        "positions": [{"stock_code": "001312.SZ", "total_volume": 1100},
                      {"stock_code": "600309.SH", "total_volume": 500},
                      {"stock_code": "600519.SH", "total_volume": 100}]}
WATCH = {"deepseek-v4-pro": [{"code": "001312.SZ"}], "glm-5.3-flash": []}


def test_reconcile_lists_holdings_and_unassigned():
    """券商有、台账无 = 2026-08-31 那批不入分账的总账户持仓（预期，不算漂移）。"""
    rec = P.reconcile(LEDGER, ACCT["positions"])

    assert rec["by_agent"] == {"deepseek-v4-pro": ["001312.SZ"],
                               "glm-5.3-flash": ["600309.SH"]}
    assert rec["drift"] == []
    assert rec["unassigned"] == ["600519.SH"]


def test_reconcile_flags_drift_when_broker_missing_position():
    """台账说持有、券商快照没有 → 最危险的方向（条件位挂空、止损保护不了）。"""
    rec = P.reconcile(LEDGER, [{"stock_code": "001312.SZ", "total_volume": 1100}])

    assert rec["drift"] == [{"agent": "glm-5.3-flash", "code": "600309.SH"}]


def test_evaluate_passes_when_everything_green():
    res = P.evaluate(ACCT, "", LEDGER, True, WATCH)

    assert res["ok"] is True and res["problems"] == []
    assert res["account"]["positions"] == 3
    assert res["watch_rules"] == {"deepseek-v4-pro": 1}


def test_evaluate_flags_fake_alive_account():
    """行情通、账户返 0（当日实况）→ 必须列成问题，且不许写"通过"。

    同时不许拿 asset=0 的空持仓去对账：那必然报出一堆假"漂移"，把真问题埋进噪音。
    """
    res = P.evaluate({"asset": {"asset": 0.0}, "positions": []}, "", LEDGER, True, WATCH)

    assert res["ok"] is False
    assert any("资产" in p and "桥假活" in p for p in res["problems"])
    assert not any("漂移" in p for p in res["problems"])
    assert "ledger" not in res["account"]


def test_evaluate_flags_exec_switch_off_and_query_failure():
    off = P.evaluate(ACCT, "", LEDGER, False, WATCH)
    assert any("执行开关" in p for p in off["problems"])

    dead = P.evaluate(None, "Connection refused", LEDGER, True, WATCH)
    assert dead["ok"] is False
    assert any("账户通道不可用" in p for p in dead["problems"])
