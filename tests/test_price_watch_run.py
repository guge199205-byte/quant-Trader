"""分钟级价格哨兵 run_watch 的接线回归测试。

2026-09-09 实盘实录：`run_watch` 里引用 `now`（收盘集合竞价护栏）却没这个变量——
`now` 只是 `main()` 的局部量，从未传进来。只要任一条件位触发就 `NameError`：

    File "scripts/live_price_watch.py", line 283, in run_watch
        if in_close_auction(now):
    NameError: name 'now' is not defined

后果不止是刷屏：触发分支在**执行卖出之前**就崩，止损单根本发不出去
（自动执行开关打开时 = 止损形同虚设），且崩了就不落盘 → 下一分钟重复触发。
护栏函数本身有单测（tests/test_news_brief.py::test_price_watch_auction_guard），
但**调用点**没有——所以这里测的是接线：14:58 不卖、14:50 卖。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_price_watch_run.py -q
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_price_watch as W  # noqa: E402

CN = W.CN_TZ
RULE = {"code": "600362.SH", "stop_loss": 50.0, "pct": 1.0, "reason": "测试"}


class _FakeBroker:
    def _account_query(self):
        return {"positions": [{"stock_code": "600362.SH", "available_volume": 100}]}


@pytest.fixture
def sentinel(monkeypatch):
    """把 run_watch 的外部依赖全换成桩，记录卖出调用与落盘结果。"""
    calls = {"sell": [], "saved": []}

    import live_fills
    import live_hourly_analysis

    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)
    monkeypatch.setattr(W, "load_watch", lambda: {"agentA": [dict(RULE)]})
    monkeypatch.setattr(W, "save_watch", lambda rules: calls["saved"].append(rules))
    monkeypatch.setattr(W, "_last_price", lambda broker, code: (48.89, 49.00))

    def _fake_sell(broker, agent, rule, price, prev, trig, avail, **kw):
        calls["sell"].append((agent, rule["code"], trig, price))
        return True

    monkeypatch.setattr(W, "_execute_sell", _fake_sell)
    return calls


def test_run_watch_holds_rule_in_close_auction(sentinel):
    """收盘集合竞价（14:57-15:00）触发条件位 → 只打印不卖，规则保留。"""
    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 7, 14, 58, 30, tzinfo=CN))

    assert fired == 0
    assert sentinel["sell"] == []
    assert sentinel["saved"][-1] == {"agentA": [dict(RULE)]}  # 条件位保留至盘后/次日


def test_run_watch_executes_outside_close_auction(sentinel):
    """14:50（非竞价时段）同一条件位 → 正常触发卖出，规则被消费。"""
    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 7, 14, 50, 0, tzinfo=CN))

    assert fired == 1
    assert sentinel["sell"] == [("agentA", "600362.SH", "stop_loss", 48.89)]
    assert sentinel["saved"][-1] == {}  # 已消费


def test_run_watch_defaults_now_from_now_cn(sentinel, monkeypatch):
    """生产调用形状：调用方不传 now 时缺省取 now_cn()——事故当天崩的就是这条路径。"""
    monkeypatch.setattr(W, "now_cn", lambda: datetime(2026, 9, 7, 14, 50, 0, tzinfo=CN))

    fired = W.run_watch(_FakeBroker())  # 无 now 参数 → 不得 NameError

    assert fired == 1
    assert sentinel["sell"] == [("agentA", "600362.SH", "stop_loss", 48.89)]


def test_run_watch_keeps_rule_when_exec_switch_off(sentinel, monkeypatch):
    """执行开关关闭（自动降级 dry-run）时触发条件位：只报不卖，且**条件位必须保留**。

    2026-09-11 实录：600309 的 75.50 止损在开关关闭时于 09:44 触发，dry-run 分支
    照样返回「已消费」→ 规则被吃掉、当天再无人守，持仓裸奔（价格 74.41 → 73.85）。
    """
    import live_hourly_analysis

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)

    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 9, 44, 0, tzinfo=CN))

    assert fired == 0
    assert sentinel["sell"] == []
    kept = sentinel["saved"][-1]["agentA"]
    assert [r["code"] for r in kept] == ["600362.SH"]  # 保留 → 下一分钟继续守


def test_run_watch_dry_run_flag_keeps_rule(sentinel):
    """显式 --dry-run（手工试运行）同样不消费条件位——试运行不该改状态。"""
    fired = W.run_watch(_FakeBroker(), dry_run=True,
                        now=datetime(2026, 9, 11, 10, 0, 0, tzinfo=CN))

    assert fired == 0
    assert sentinel["sell"] == []
    assert [r["code"] for r in sentinel["saved"][-1]["agentA"]] == ["600362.SH"]
