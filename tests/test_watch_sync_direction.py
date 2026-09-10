"""复盘预案 → 哨兵条件位同步的方向/比例/持仓校验（2026-09-10 实录）。

当日三处失真（都写进了 data/live_watch.json，次日开盘即反向假触发）：

  1) 方向：『涨到17.90减50%』被同步成 `stop_loss: 17.90`——现价 17.50 在阈值下方，
     `price <= stop_loss` 成立 → 开盘就是一笔反向卖出。09-09 的 18.10/80.00 同款。
  2) 比例：『距该位仅0.8%缓冲』的 pct 正则取到 "8" → 8%（应为默认全减）；
     同一 agent 还挂了两条重复的 3896。
  3) 标的：上证指数 000001.SH、以及 flash 的低吸闸门 3975，被当持仓挂成卖出条件位——
     账户空仓照样触发（11:13 实录），条件位白烧、日志白刷。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_watch_sync_direction.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import post_review as P  # noqa: E402

AGENT = "deepseek-v4-pro"
PREV = {"001312.SZ": 17.22, "600309.SH": 78.00, "000001.SH": 3940.0}


@pytest.fixture
def sync(monkeypatch):
    """桩掉落盘 + 台账，返回捕获到的 watch 决策列表。"""
    import live_price_watch

    calls = {"decisions": []}
    monkeypatch.setattr(live_price_watch, "save_watch_rules",
                        lambda agent, decisions: calls["decisions"].extend(decisions) or len(decisions))
    monkeypatch.setattr(P, "_held_codes", lambda agent: {"001312.SZ", "600309.SH"})
    return calls


def _sync(sync, payload, held=None):
    n = P.sync_plan_to_watch(AGENT, payload, prev_close=lambda code: PREV.get(code))
    return n, sync["decisions"]


# ---------- pct 解析 ----------

@pytest.mark.parametrize("txt,expect", [
    ("今日区间3927.35-3949.26距该位仅0.8%缓冲", 1.0),   # 缓冲比例不是减仓比例 → 默认全减
    ("跌破先减50%、确认破位清仓", 0.5),
    ("触及兑现线减半锁利", 0.5),
    ("减 30 % 落袋", 0.3),
    ("减50％（全角）", 0.5),
    ("涨到80.0止盈减50%，不加仓", 0.5),
    ("没有任何比例", 1.0),
])
def test_pct_from_text(txt, expect):
    assert P._pct_from_text(txt) == expect


# ---------- 方向判定 ----------

def test_watch_up_level_armed_as_take_profit(sync):
    """『涨到17.90减50%』→ take_profit（修复前挂成 stop_loss，开盘反向假触发）。"""
    _, dec = _sync(sync, {"watch": [{"code": "001312.SZ", "price": 17.9, "action": "sell",
                                     "reason": "反弹兑现位减50%落袋，须写成『涨到17.90』"}]})
    assert len(dec) == 1
    assert dec[0]["take_profit"] == 17.9 and dec[0]["stop_loss"] is None
    assert dec[0]["pct"] == 0.5


def test_watch_down_level_armed_as_stop_loss(sync):
    _, dec = _sync(sync, {"watch": [{"code": "001312.SZ", "price": 16.6, "action": "sell",
                                     "reason": "清仓硬线：跌破先减50%、确认破位清仓"}]})
    assert dec[0]["stop_loss"] == 16.6 and dec[0]["take_profit"] is None


def test_direction_text_conflicting_with_prev_close_follows_prev(sync):
    """文字说『涨到』但价位在昨收下方（一挂就反向触发）→ 以昨收为准改按跌破。"""
    _, dec = _sync(sync, {"watch": [{"code": "600309.SH", "price": 77.0, "action": "sell",
                                     "reason": "涨到77.0减半"}]})
    assert dec[0]["stop_loss"] == 77.0 and dec[0]["take_profit"] is None


def test_explicit_stop_loss_key_wins(sync):
    """plan 里显式给了 stop_loss 键 → 尊重原键，不做文字推断。"""
    _, dec = _sync(sync, {"plan": [{"code": "600309.SH", "action": "sell", "stop_loss": 75.5,
                                    "reason": "跌破75.50减50%"}]})
    assert dec[0]["stop_loss"] == 75.5 and dec[0]["take_profit"] is None


# ---------- 持仓校验 ----------

def test_index_and_buy_gate_not_armed(sync):
    """非持仓标的（上证指数/买点闸门）不挂卖出哨兵——账户空仓无从卖出。"""
    payload = {
        "plan": [{"code": "000001.SH", "action": "watch", "stop_loss": 3896.0,
                  "reason": "防守位失守意味弱市下沿打开"}],
        "watch": [{"code": "000001.SH", "price": 3896.0, "action": "sell", "reason": "跌破3896"},
                  {"code": "000001.SH", "price": 3975.0, "action": "sell", "reason": "站上3975才低吸"}],
    }
    n, dec = _sync(sync, payload)
    assert n == 0 and dec == []


def test_held_code_still_armed(sync):
    n, dec = _sync(sync, {"watch": [{"code": "001312.SZ", "price": 16.6, "action": "sell",
                                     "reason": "跌破16.60减半"}]})
    assert n == 1 and dec[0]["code"] == "001312.SZ"


def test_buy_plan_not_armed_as_sell(sync):
    """买入预案不是卖出条件位——挂成 take_profit 会在目标买价上把持仓卖掉。"""
    n, dec = _sync(sync, {"plan": [{"code": "001312.SZ", "action": "buy", "take_profit": 18.5,
                                    "reason": "放量站上18.5确认后加仓"}]})
    assert n == 0 and dec == []


def test_ledger_unreadable_keeps_rules(sync, monkeypatch):
    """台账读不到（None）时不裁剪——不能因台账故障把真实止损位清空。"""
    monkeypatch.setattr(P, "_held_codes", lambda agent: None)
    n, dec = _sync(sync, {"watch": [{"code": "000001.SH", "price": 3896.0, "action": "sell",
                                     "reason": "跌破3896"}]})
    assert n == 1 and dec[0]["code"] == "000001.SH"


def test_held_codes_reads_ledger(tmp_path, monkeypatch):
    import live_ledger

    p = tmp_path / "live_ledger.json"
    p.write_text('{"version":1,"agents":{"deepseek-v4-pro":{"positions":'
                 '{"001312.SZ":{"volume":1100}}},"deepseek-v4-flash":{"positions":{}}}}',
                 encoding="utf-8")
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", p)

    assert P._held_codes("deepseek-v4-pro") == {"001312.SZ"}
    assert P._held_codes("deepseek-v4-flash") == set()
    assert P._held_codes("未知agent") is None          # 账本里没有 → 不裁剪
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", tmp_path / "缺失.json")
    assert P._held_codes("deepseek-v4-pro") is None
