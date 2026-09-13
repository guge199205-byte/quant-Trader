"""限价买单口径（live_fills.buy_limit_and_cost）——三市场唯一出处。

为什么单独测：这条规则此前在三个文件里各写一遍并已实测漂移——
A 股版对（按限价算成本），**美/港股版错（按现价算）**，导致单票预算与
账户现金闸可被超 1%（美）/ 0.5%（港）。收敛到一处后，用测试钉死它不再各漂各的。

口径依据（对照 HKUDS/Vibe-Trading order_guard 的 H3）：
**买单在限价内的任意价位都能成交，所以额度/现金闸必须按「限价」卡，不能用现价。**

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_buy_limit_cost.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from live_fills import BUY_LIMIT_BUFFER, buy_limit_and_cost  # noqa: E402


def test_cost_is_at_the_limit_price_not_the_market_price():
    """★ 核心：成本按限价算，绝不能按现价算（那是已修过的 bug）。"""
    limit, cost = buy_limit_and_cost(10.0, 1000, "cn")

    assert limit == 10.1                    # 10.0 × 1.01
    assert cost == pytest.approx(10100.0)   # 1000 × 10.1，不是 1000 × 10.0
    assert cost > 1000 * 10.0               # 必须严大于按现价算的值


def test_buffer_differs_by_market():
    """港股价差更窄 → +0.5%；A股/美股 +1%。"""
    assert BUY_LIMIT_BUFFER["cn"] == 1.01
    assert BUY_LIMIT_BUFFER["us"] == 1.01
    assert BUY_LIMIT_BUFFER["hk"] == 1.005

    assert buy_limit_and_cost(100.0, 100, "hk")[0] == 100.5
    assert buy_limit_and_cost(100.0, 100, "us")[0] == 101.0


def test_unknown_market_falls_back_to_cn_not_silently_zero():
    """市场拼错时退回 A 股口径（保守），不得静默变成"不加缓冲"。"""
    assert buy_limit_and_cost(10.0, 100, "jp") == buy_limit_and_cost(10.0, 100, "cn")


def test_limit_is_rounded_to_cent_before_multiplying():
    """限价先取整到分（真实报出去的就是这个价），成本再按它算。

    旧实现是 vol×price×1.01 未取整，与真实出金差最多几元——
    闸门要卡的是**真实 worst-case 出金**，所以先取整更正确。
    """
    limit, cost = buy_limit_and_cost(13.6, 1100, "cn")

    assert limit == 13.74                   # round(13.736, 2)
    assert cost == pytest.approx(round(1100 * 13.74, 2))
    assert cost != round(1100 * 13.6 * 1.01, 2)   # 与未取整口径确实不同


def test_zero_volume_gives_zero_cost():
    assert buy_limit_and_cost(10.0, 0, "cn")[1] == 0
