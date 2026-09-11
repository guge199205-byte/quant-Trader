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
def sentinel(monkeypatch, tmp_path):
    """把 run_watch 的外部依赖全换成桩，记录卖出调用与落盘结果。"""
    calls = {"sell": [], "saved": [], "events": tmp_path / "events.json"}

    import live_fills
    import live_hourly_analysis

    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    # 事件文件必须隔离：跑测试不得往真实 data/ 里写委托事件（alert.sh 会读它）
    monkeypatch.setattr(live_fills, "EVENTS_FILE", calls["events"])
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)
    monkeypatch.setattr(W, "load_watch", lambda: {"agentA": [dict(RULE)]})
    monkeypatch.setattr(W, "save_watch", lambda rules: calls["saved"].append(rules))
    monkeypatch.setattr(W, "_last_price", lambda broker, code: (48.89, 49.00))
    # 动作日志必须隔离：走真实 _execute_sell 的用例一旦失败/触发，_log_line 会写
    # 真实 logs/live_watch_*.jsonl（2026-09-11 实录：合成 agentA 记录混进生产日志）
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)

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
    该场景同时必须落一条委托事件（alert.sh 上报）：开关关着的止损=持仓无保护。
    """
    import json

    import live_hourly_analysis

    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)

    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 9, 44, 0, tzinfo=CN))

    assert fired == 0
    assert sentinel["sell"] == []
    kept = sentinel["saved"][-1]["agentA"]
    assert [r["code"] for r in kept] == ["600362.SH"]  # 保留 → 下一分钟继续守
    events = json.loads(sentinel["events"].read_text(encoding="utf-8"))
    assert any(k.startswith("exec_disabled:600362.SH") for k in events)


def test_run_watch_dry_run_flag_keeps_rule(sentinel):
    """显式 --dry-run（手工试运行）同样不消费条件位——试运行不该改状态，也不告警。"""
    fired = W.run_watch(_FakeBroker(), dry_run=True,
                        now=datetime(2026, 9, 11, 10, 0, 0, tzinfo=CN))

    assert fired == 0
    assert sentinel["sell"] == []
    assert [r["code"] for r in sentinel["saved"][-1]["agentA"]] == ["600362.SH"]
    assert not sentinel["events"].exists()   # 手工试运行不写委托事件（避免误告警）


# ---------- 回写粒度：只施加本轮差量，不冲掉并发写入 ----------
# 2026-09-11 移植 quantmind 止损执行器代码审查 LOW 12（状态回写按 dirty 合并）：
# 哨兵一轮跑十几秒（每条规则 1s 桥限流 + 逐只行情），期间整点分析 / 收盘复盘
# 可能刚给某个 agent 换上新条件位（live_hourly_analysis / post_review 都调
# save_watch_rules）。原来 run_watch 结尾整表回写，用的是**轮询开始时的快照**——
# 并发挂上的新止损被静默冲掉，持仓裸奔到下一次分析（最长一小时，收盘复盘挂的
# 直接等次日）。回写必须只施加本轮自己的差量。

def _rule(code, stop, created="2026-09-11T13:30:00+08:00"):
    return {"code": code, "stop_loss": stop, "pct": 1.0, "reason": "测试",
            "created_ts": created}


@pytest.fixture
def file_watch(monkeypatch, tmp_path):
    """真实 load_watch/save_watch（落 tmp 文件）；测试用 _last_price 在轮询中途
    插入一次「另一个进程」的写入，复现哨兵跑动期间的并发窗口。"""
    import json
    import types as _t

    import live_fills
    import live_hourly_analysis

    path = tmp_path / "live_watch.json"
    monkeypatch.setattr(W, "WATCH_FILE", path)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)      # 同 sentinel：动作日志隔离
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)

    def write(rules):
        path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    def read():
        return json.loads(path.read_text(encoding="utf-8"))

    return _t.SimpleNamespace(write=write, read=read)


def test_flush_keeps_rules_armed_by_another_process_mid_tick(file_watch, monkeypatch):
    """轮询途中整点分析给 agentB 挂上新条件位 → 本轮回写不得把它冲掉。"""
    file_watch.write({"agentA": [_rule("600362.SH", 50.0)]})
    done = {"fired": False}

    def mid_tick_write(broker, code):
        if not done["fired"]:
            done["fired"] = True
            file_watch.write({"agentA": [_rule("600362.SH", 50.0)],
                              "agentB": [_rule("000958.SZ", 5.15)]})
        # 现价 51.00 在止损 50.0 上方 = 真不触发（此前用 49.00 配 50.0 止损，
        # 实际每轮都打到触发分支，靠 _execute_sell 在缺方法的假 broker 上抛异常
        # 才凑出 fired==0 ——假绿，且会往真实 logs/live_watch_*.jsonl 写脏记录）
        return 51.00, 50.00

    monkeypatch.setattr(W, "_last_price", mid_tick_write)

    assert W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 14, 0, 0, tzinfo=CN)) == 0

    got = file_watch.read()
    assert [r["code"] for r in got["agentB"]] == ["000958.SZ"]   # 并发挂的必须活着
    assert [r["code"] for r in got["agentA"]] == ["600362.SH"]


def test_flush_applies_own_consumption_without_clobbering(file_watch, monkeypatch):
    """对照：合并 ≠ 不回写。本轮消费掉的规则仍要删；同一 agent 并发新增的保留。"""
    file_watch.write({"agentA": [_rule("600362.SH", 50.0)]})
    done = {"fired": False}

    def mid_tick_write(broker, code):
        if not done["fired"]:
            done["fired"] = True
            file_watch.write({"agentA": [_rule("600362.SH", 50.0),
                                         _rule("000958.SZ", 5.15)]})
        return 48.89, 49.00            # 跌破 50 → 触发（下方 _execute_sell 桩返回已消费）

    monkeypatch.setattr(W, "_last_price", mid_tick_write)

    def _consume(broker, agent, rule, price, prev, trig, avail, **kw):
        return True

    monkeypatch.setattr(W, "_execute_sell", _consume)

    assert W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 14, 0, 0, tzinfo=CN)) == 1

    codes = [r["code"] for r in file_watch.read()["agentA"]]
    assert codes == ["000958.SZ"]      # 已消费的 600362 删掉；并发挂的 000958 保留
