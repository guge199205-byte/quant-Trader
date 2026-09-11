"""live_l2_capture 因子计算回归：量化细节的「无数据」路径不得炸整只/整轮。

2026-09-11 移植 quantmind 同日修复（其线上实录：停牌/无行情时 now 记 0，
rets 全空 → day_rv = sum/len 抛 ZeroDivisionError）。本仓同款除法在
micro_zone_rv_ratio_close：本仓调用点有单只级 try/except（不会拖垮整轮），
但该只票整分钟被跳过、告警每轮刷——按「样本不足」返回 None 才是正确语义。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_l2_factors.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_l2_capture as L  # noqa: E402


def _state(price: float, n: int = 8) -> dict:
    """n 帧采样（[ts, v_buy, v_sell, a_buy, a_sell, price]），价格全为 price。"""
    return {
        "samples": [[i, 100, 100, 1000, 1000, price] for i in range(n)],
        "zone_baselines": {},
        "prev_price": price,
    }


def test_zone_rv_ratio_all_zero_prices_returns_none():
    """全程无有效价（停牌/行情缺失）→ 波动率比返回 None，不抛 ZeroDivisionError。"""
    data = {"vol_4x4": [[100, 100, 100, 100]], "amo_4x4": [[1000, 1000, 1000, 1000]]}
    snap = {"now": 0, "volume": 0, "inside": 0, "outside": 0}
    fac = L.compute_l2_factors(data, snap, _state(0.0))
    assert fac["micro_zone_rv_ratio_close"] is None


def test_zone_rv_ratio_normal_prices_still_computed():
    """有有效价的常规路径不受影响（防修成恒 None）。"""
    data = {"vol_4x4": [[100, 100, 100, 100]], "amo_4x4": [[1000, 1000, 1000, 1000]]}
    snap = {"now": 10.0, "volume": 1000, "inside": 500, "outside": 500}
    prices = [10.0, 10.1, 10.05, 10.2, 10.15, 10.3, 10.25, 10.4]
    state = {
        "samples": [[i, 100, 100, 1000, 1000, p] for i, p in enumerate(prices)],
        "zone_baselines": {},
        "prev_price": prices[-1],
    }
    fac = L.compute_l2_factors(data, snap, state)
    assert fac["micro_zone_rv_ratio_close"] is not None
    assert fac["micro_zone_rv_ratio_close"] >= 0
