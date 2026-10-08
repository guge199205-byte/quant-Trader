"""账户协议（scripts/account_protocol.py）与三套市场台账的一致性。

两层保护：
  1. **golden 基线**：重构前（2026-09-13）捕获的三套台账实际输出。协议层必须
     逐字节复现——台账是实盘数据，重构不得改变任何既有数值。
  2. **一致性断言**：三套实现跑同一操作序列，除额度外结果必须相同。
     这条是防漂移复发的关键——本项目今天三次故障都是"同一规则复制多份→漂移"，
     此前没有任何测试会发现"两份不一样"。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_account_protocol.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import account_protocol as P  # noqa: E402

# 重构前捕获的基线（sha1 见各条注释）
# ⚠️ us 条 2026-09-18 重算：评审 L-5 修复「虚拟现金恰好归零被 `or quota` 读成
#    缺失→回退 10 万」后，本序列第一笔买入即把 US 虚拟现金打到 0，后续操作
#    结果随之改变（旧值是带着该 bug 记的账）。live/hk 两条未变——证明非退化
#    路径逐字节兼容未破坏；us 新值 = 修复后的正确算术（终值 500.0）。
GOLDEN = {
    "live": '{"agents": {"a1": {"positions": {"000001.SZ": {"buy_ts": "2026-09-13T10:00:00+08:00", "cost_price": 15.5, "last_ts": "2026-09-13T10:00:00+08:00", "volume": 800}}, "virtual_cash": 90500.0}}, "version": 1}',  # sha1 546514b3b838
    "hk": '{"agents": {"a1": {"positions": {"000001.SZ": {"buy_ts": "2026-09-13T10:00:00+08:00", "cost_price": 15.5, "last_ts": "2026-09-13T10:00:00+08:00", "volume": 800}}, "virtual_cash": 90500.0}}, "version": 1}',  # sha1 546514b3b838
    "us": '{"agents": {"a1": {"positions": {"000001.SZ": {"buy_ts": "2026-09-13T10:00:00+08:00", "cost_price": 15.5, "last_ts": "2026-09-13T10:00:00+08:00", "volume": 800}}, "virtual_cash": 500.0}}, "version": 1}',  # sha1 6ecbc9b51614（2026-09-18 L-5 修复后重算）
}
QUOTA = {"live": 100_000.0, "hk": 100_000.0, "us": 10_000.0}

OPS = [
    ("buy", "600000.SH", 1000, 10.0),
    ("buy", "600000.SH", 500, 12.0),      # 加仓 → 加权成本
    ("sell", "600000.SH", 300, 11.0),     # 部分平仓
    ("buy", "000001.SZ", 800, 15.5),
    ("sell", "600000.SH", 1200, 13.0),    # 清仓
    ("sell", "999999.SH", 100, 1.0),      # 不存在的票 → 原样返回
]


def run_ops(quota: float) -> dict:
    """同一组操作走协议层，返回账本主体（抛开 roundtrips——那是新能力）。"""
    led = {"version": 1, "agents": {}}
    for i, (side, code, vol, px) in enumerate(OPS):
        ts = f"2026-09-{10 + i:02d}T10:00:00+08:00"
        fn = P.record_buy if side == "buy" else P.record_sell
        led = fn(led, "a1", code, vol, px, ts, quota=quota)
    return {k: v for k, v in led.items() if k != "roundtrips"}


def _canon(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("name", ["live", "hk", "us"])
def test_protocol_reproduces_golden(name):
    """协议层逐字节复现重构前的实际输出。"""
    assert _canon(run_ops(QUOTA[name])) == GOLDEN[name]


@pytest.mark.parametrize("name", ["live", "hk", "us"])
def test_market_ledger_reproduces_golden(name):
    """市场实现层（薄封装）同样逐字节复现基线——保证迁移未改行为。"""
    mod = __import__({"live": "live_ledger", "hk": "hk_ledger", "us": "us_ledger"}[name])
    led = {"version": 1, "agents": {}}
    for i, (side, code, vol, px) in enumerate(OPS):
        ts = f"2026-09-{10 + i:02d}T10:00:00+08:00"
        fn = mod.record_buy if side == "buy" else mod.record_sell
        led = fn(led, "a1", code, vol, px, ts)
    assert _canon({k: v for k, v in led.items() if k != "roundtrips"}) == GOLDEN[name]


def test_protocol_is_market_agnostic():
    """协议层不绑市场：同一 quota 下反复运行结果逐字节相同（无隐藏状态）。"""
    assert _canon(run_ops(50_000.0)) == _canon(run_ops(50_000.0))


# ---------- position_cost（2026-09-18：单票集中度闸的取数口径） ----------

def test_position_cost_reads_volume_times_cost():
    led = {"version": 1, "agents": {"a1": {"virtual_cash": 100.0, "positions": {
        "600000.SH": {"volume": 300, "cost_price": 10.5, "buy_ts": "T", "last_ts": "T"}}}}}

    assert P.position_cost(led, "a1", "600000.SH") == 3150.0


def test_position_cost_zero_when_absent_or_garbage():
    led = {"version": 1, "agents": {"a1": {"positions": {
        "600000.SH": {"volume": "bad", "cost_price": 10.0}}}}}

    assert P.position_cost(led, "a1", "000001.SZ") == 0.0     # 无持仓
    assert P.position_cost(led, "ghost", "600000.SH") == 0.0  # 无该 agent
    assert P.position_cost(led, "a1", "600000.SH") == 0.0     # 脏值不抛、计 0


def test_virtual_cash_zero_is_not_mistaken_for_missing():
    """评审 L-5（2026-09-18）：虚拟现金恰好归零必须读 0——旧 `rec.get(...) or quota`
    把 0 当缺失读成 10 万（资金用完的账户反而多出一个 quota 的买入力）。"""
    led = {"version": 1, "agents": {"a1": {"virtual_cash": 0.0, "positions": {}}}}

    assert P.agent_virtual_cash(led, "a1") == 0.0
    led = P.record_buy(led, "a1", "600000.SH", 100, 10.0, "T")
    assert led["agents"]["a1"]["virtual_cash"] == -1000.0     # 从 0 起算透支，不再凭空回 10 万


def test_protocol_functions_survive_non_dict_ledger():
    """顶层非 dict（可解析但不是账本）→ 返回空/0，不抛 AttributeError。"""
    assert P.positions([1, 2], "a1") == {}
    assert P.position_cost([1, 2], "a1", "600000.SH") == 0.0
