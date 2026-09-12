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
import fcntl
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills as F  # noqa: E402
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
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(live_fills, "OUTCOMES_FILE", tmp_path / "outcomes.json")
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
        # "placed" = 已下单、规则保留打标（真实 _execute_sell 会写 pending_order_id，
        # 桩写不了也不必写：本组用例只测 run_watch 的分支接线）
        return "placed"

    monkeypatch.setattr(W, "_execute_sell", _fake_sell)
    return calls


def _total(counts: dict) -> int:
    return sum(counts.values())


def test_run_watch_holds_rule_in_close_auction(sentinel):
    """收盘集合竞价（14:57-15:00）触发条件位 → 只打印不卖，规则保留。"""
    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 7, 14, 58, 30, tzinfo=CN))

    assert _total(fired) == 0
    assert sentinel["sell"] == []
    assert sentinel["saved"][-1] == {"agentA": [dict(RULE)]}  # 条件位保留至盘后/次日


def test_run_watch_executes_outside_close_auction(sentinel):
    """14:50（非竞价时段）同一条件位 → 正常触发下单；规则**保留**等这笔单的归宿。

    2026-09-12 起「下单成功」不再等于「规则消费」：001312 实录那笔单被柜台判废
    （成交 0/300），当时规则已被消费 → 持仓当日再无保护。
    """
    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 7, 14, 50, 0, tzinfo=CN))

    assert fired["placed"] == 1
    assert sentinel["sell"] == [("agentA", "600362.SH", "stop_loss", 48.89)]
    assert sentinel["saved"][-1] == {"agentA": [dict(RULE)]}  # 已下单 → 规则保留


def test_run_watch_defaults_now_from_now_cn(sentinel, monkeypatch):
    """生产调用形状：调用方不传 now 时缺省取 now_cn()——事故当天崩的就是这条路径。"""
    monkeypatch.setattr(W, "now_cn", lambda: datetime(2026, 9, 7, 14, 50, 0, tzinfo=CN))

    fired = W.run_watch(_FakeBroker())  # 无 now 参数 → 不得 NameError

    assert fired["placed"] == 1
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

    assert _total(fired) == 0
    assert sentinel["sell"] == []
    assert sentinel["saved"] == []          # 不消费 = 不回写：文件原样，规则下一分钟还在
    events = json.loads(sentinel["events"].read_text(encoding="utf-8"))
    assert any(k.startswith("exec_disabled:600362.SH") for k in events)


def test_run_watch_dry_run_flag_keeps_rule(sentinel):
    """显式 --dry-run（手工试运行）同不消费条件位、**不回写文件**，也不告警。

    「保留」的机制就是「不写」（2026-09-12）：dry-run 下无人消费/丢弃规则，文件原样
    即规则原样；此前仍走 flush_watch，会把方向护栏/move_stop 的在内存改写落盘——
    试运行不该改状态。
    """
    fired = W.run_watch(_FakeBroker(), dry_run=True,
                        now=datetime(2026, 9, 11, 10, 0, 0, tzinfo=CN))

    assert _total(fired) == 0
    assert sentinel["sell"] == []
    assert sentinel["saved"] == []
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

    return _t.SimpleNamespace(write=write, read=read, path=path)


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

    assert _total(W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 14, 0, 0, tzinfo=CN))) == 0

    got = file_watch.read()
    assert [r["code"] for r in got["agentB"]] == ["000958.SZ"]   # 并发挂的必须活着
    assert [r["code"] for r in got["agentA"]] == ["600362.SH"]


def test_flush_applies_own_consumption_without_clobbering(file_watch, monkeypatch):
    """对照：合并 ≠ 不回写。本轮作废掉的规则仍要删；同一 agent 并发新增的保留。"""
    file_watch.write({"agentA": [_rule("600362.SH", 50.0)]})
    done = {"fired": False}

    def mid_tick_write(broker, code):
        if not done["fired"]:
            done["fired"] = True
            file_watch.write({"agentA": [_rule("600362.SH", 50.0),
                                         _rule("000958.SZ", 5.15)]})
        return 48.89, 49.00            # 跌破 50 → 触发（下方 _execute_sell 桩判作废）

    monkeypatch.setattr(W, "_last_price", mid_tick_write)

    def _discard(broker, agent, rule, price, prev, trig, avail, **kw):
        return "discard"

    monkeypatch.setattr(W, "_execute_sell", _discard)

    fired = W.run_watch(_FakeBroker(), now=datetime(2026, 9, 11, 14, 0, 0, tzinfo=CN))
    assert fired["discarded"] == 1

    codes = [r["code"] for r in file_watch.read()["agentA"]]
    assert codes == ["000958.SZ"]      # 已作废的 600362 删掉；并发挂的 000958 保留


# ---------- 条件位生命周期：下单 → 等归宿 → 消费/重布防/作废（2026-09-12 P0-3）----------
# 001312 实录：09:35 调仓触发止损卖出，桥受理并返回委托号，规则当场被「消费」——
# 随后该单被柜台判废（成交 0/300），条件位已经消失 → 该持仓当日再无保护。
# 新语义：下单成功 = 规则**保留并打标**（pending_order_id/pending_volume），由
# 「在途表 + 终态台账」推进生命周期：
#   仍在在途表 → 等；满额成交/持仓清零 → 消费；没卖完/废单 → 换号重布防；
#   终态台账查不到该委托号 → 不重布防，转人工事件（状态未知不许猜）。

LIFE_AGENT = "deepseek-v4-pro"
LIFE_CODE = "001312.SZ"
LIFE_PREV = 17.22
LIFE_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=CN)
ZERO = {"placed": 0, "consumed": 0, "discarded": 0}


class _LifeBroker:
    """能「真下单」的桩 broker：sell 返回委托号，get_orders 供 duplicate 回捞。"""

    def __init__(self):
        self.sold = []
        self.orders = []
        self.result = {"order_id": "T9001", "status": "submitted"}

    def _account_query(self):
        return {"asset": {"asset": 300000.0, "cash": 1000.0},
                "positions": [{"stock_code": LIFE_CODE, "available_volume": 1000}]}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price, "plan_id": plan_id})
        return dict(self.result)

    def get_orders(self, stock_code="", cancelable_only=False):
        return [o for o in self.orders if not stock_code or o.get("stock_code") == stock_code]


def _life_rule(**kw):
    """现价 17.00 / 昨收 17.22 下必然触发的止损规则（可覆写任意字段）。"""
    r = {"code": LIFE_CODE, "stop_loss": 17.10, "take_profit": None, "move_stop": None,
         "pct": 0.5, "reason": "测试", "created_ts": "2026-09-12T09:35:00+08:00"}
    r.update(kw)
    return r


@pytest.fixture
def life(monkeypatch, tmp_path):
    """条件位全生命周期测试台：watch 文件/在途表/终态台账/分账台账全用真实实现。

    只桩掉外部世界：桥（上面那个桩）、对账 reconcile（「对账 → 终态台账」的接线
    在 tests/test_reconcile_hardening.py 单测）、行情。
    """
    import types as _t

    import live_fills
    import live_hourly_analysis
    import live_ledger

    wf = tmp_path / "life_watch.json"
    ledger = tmp_path / "life_ledger.json"
    monkeypatch.setattr(W, "WATCH_FILE", wf)
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(live_fills, "OUTCOMES_FILE", tmp_path / "outcomes.json")
    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", ledger)
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)

    box = {"price": (17.00, LIFE_PREV)}
    monkeypatch.setattr(W, "_last_price", lambda broker, code: box["price"])

    def holds(yes=True):
        ledger.write_text(json.dumps({"version": 1, "agents": {LIFE_AGENT: {
            "virtual_cash": 100000.0,
            "positions": ({LIFE_CODE: {"volume": 1000, "cost_price": 16.0}} if yes else {})}}}),
            encoding="utf-8")

    def write_rules(rules):
        wf.write_text(json.dumps({LIFE_AGENT: rules}, ensure_ascii=False), encoding="utf-8")

    def rules():
        try:
            return json.loads(wf.read_text(encoding="utf-8")).get(LIFE_AGENT) or []
        except OSError:
            return []

    def outcomes(doc):
        (tmp_path / "outcomes.json").write_text(json.dumps(doc), encoding="utf-8")

    def events():
        try:
            return json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
        except OSError:
            return {}

    holds(True)
    return _t.SimpleNamespace(broker=_LifeBroker(), write=write_rules, rules=rules,
                              holds=holds, outcomes=outcomes, events=events,
                              price=lambda px, prev=LIFE_PREV: box.update(price=(px, prev)))


def test_placed_order_tags_rule_instead_of_consuming(life):
    """下单成功 → 规则保留并打标（pending_order_id/pending_volume/fired_ts）。"""
    life.write([_life_rule()])

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == {"placed": 1, "consumed": 0, "discarded": 0}
    assert life.broker.sold[0]["volume"] == 500          # 1000 股 × 50%
    assert [p["order_id"] for p in F.load_pending()] == ["T9001"]
    r = life.rules()[0]
    assert r["pending_order_id"] == "T9001" and r["pending_volume"] == 500
    assert r["fired_ts"]


def test_tagged_rule_waits_while_order_in_flight(life):
    """委托仍在在途表 → 只等，不重复下单（同一条件位不许一分钟下一笔）。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500, fired_ts="x")])
    F.add_pending("T9001", LIFE_AGENT, LIFE_CODE, "sell", 500, 15.50, LIFE_NOW.isoformat())

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    assert life.broker.sold == []
    assert life.rules()[0]["pending_order_id"] == "T9001"


def test_rejected_order_rearms_and_refires_with_new_plan_id(life):
    """001312 场景：委托终态废单（成交 0/300）→ 重新布防；下一轮换号真下单。

    同号重下会被桥按 plan_id 永久判重复（去重记在桥内存里），所以重新布防必须
    同时换 plan 号——这是本用例钉住的关键不变量。
    """
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500, fired_ts="x")])
    life.outcomes({"T9001": {"status": "rejected", "filled": 0, "wanted": 500,
                             "ts": LIFE_NOW.isoformat()}})

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO                      # 本轮只重布防，不立刻下单
    r = life.rules()[0]
    assert "pending_order_id" not in r and "pending_volume" not in r
    assert r["plan_seq"] == 1
    assert "refire" in json.dumps(life.events(), ensure_ascii=False)

    counts = W.run_watch(life.broker, now=LIFE_NOW)      # 下一轮：仍跌破 → 真下单
    assert counts["placed"] == 1
    assert life.broker.sold[0]["plan_id"].endswith("-r1")
    assert life.rules()[0]["pending_order_id"] == "T9001"


def test_filled_order_consumes_rule_when_position_gone(life):
    """满额成交 + 台账已无此持仓（卖光了）→ 消费规则（正常收尾）。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500, fired_ts="x")])
    life.outcomes({"T9001": {"status": "filled", "filled": 500, "wanted": 500,
                             "ts": LIFE_NOW.isoformat()}})
    life.holds(False)

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts["consumed"] == 1
    assert life.rules() == []


def test_pct_reduction_rule_is_consumed_after_full_fill(life):
    """pct=0.3 的减仓单满额成交 → 消费。不消费会一轮 30% 地卖到清仓。"""
    life.write([_life_rule(pct=0.3, pending_order_id="T9001", pending_volume=300,
                           fired_ts="x")])
    life.outcomes({"T9001": {"status": "filled", "filled": 300, "wanted": 300,
                             "ts": LIFE_NOW.isoformat()}})

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts["consumed"] == 1
    assert life.rules() == []
    assert life.broker.sold == []              # 台账仍持有 1000 股，也不许再卖一笔
    # 条件位消失要留机器可读的「为什么」（旧实现只有一行 print：001312 事后在事件面
    # 里查不到任何记录）；满额成交是正常收尾，不推人。
    ev = [v for v in life.events().values() if v["kind"] == "watch_consumed"]
    assert ev and ev[0]["alert"] is False and "300/300" in ev[0]["msg"]


def test_partial_fill_rearms_for_the_remainder(life):
    """部分成交后失效（partial_cancelled 100/300）→ 重布防守余量。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=300, fired_ts="x")])
    life.outcomes({"T9001": {"status": "partial_cancelled", "filled": 100, "wanted": 300,
                             "ts": LIFE_NOW.isoformat()}})

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    r = life.rules()[0]
    assert r["plan_seq"] == 1 and "pending_order_id" not in r
    assert "refire" in json.dumps(life.events(), ensure_ascii=False)


def test_missing_outcome_keeps_rule_and_alerts(life):
    """终态台账没有该委托号（去向未知）→ 不重布防（不猜），转人工事件。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500, fired_ts="x")])

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    assert life.broker.sold == []
    assert life.rules()[0]["pending_order_id"] == "T9001"      # 原样留着等人工
    events = life.events()
    assert any(k.startswith("outcome_unknown") for k in events)
    assert [e for e in events.values() if e["kind"] == "outcome_unknown"][0]["alert"] is True


def test_duplicate_of_rejected_order_rearms_without_spinning(life):
    """桥判 duplicate 且当日唯一在案委托是废单（0 成交）→ 不接管、换号重布防。

    接管那笔废单只会被下一轮 reconcile 立刻移除，哨兵随即再次触发 → 每分钟空转，
    而且永远拿不到真单（同 plan 号被桥永久判重复）。"""
    life.write([_life_rule()])
    life.broker.result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    life.broker.orders = [{"order_id": "W1", "stock_code": LIFE_CODE, "side": "sell",
                           "order_price": 15.50, "total_volume": 500, "filled_volume": 0,
                           "status": "rejected"}]

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    assert F.load_pending() == []                       # 废单不进在途表
    r = life.rules()[0]
    assert r["plan_seq"] == 1 and "pending_order_id" not in r
    oc = F.load_order_outcome("W1")                     # 归宿已落机器可读台账
    assert oc["status"] == "rejected" and oc["filled"] == 0 and oc["wanted"] == 500
    # 这笔委托从没进过在途表 → reconcile 不会为它发任何告警；柜台判废的第一现场
    # 只能靠这里打扰人（2026-09-12 审查 MEDIUM）
    ev = [v for v in life.events().values() if v["kind"] == "refire"]
    assert ev and ev[0]["alert"] is True


def test_stale_outcome_from_previous_day_is_not_mistaken_for_this_order(life):
    """台账 24h 窗口跨日 + 委托号每日重排：昨天同号的终态不许当今天的归宿。

    误当「废单」→ 清标记换号重布防 → 今天可能再卖一笔；真状态是「今天的单尚无
    归宿」，正确动作是保持原样 + 转人工。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500,
                           fired_ts="2026-09-12T09:35:00+08:00")])
    life.outcomes({"T9001": {"status": "rejected", "filled": 0, "wanted": 500,
                             "ts": "2026-09-11T09:35:00+08:00"}})   # 昨天的同号单

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    r = life.rules()[0]
    assert r["pending_order_id"] == "T9001" and "plan_seq" not in r
    assert life.broker.sold == []
    assert any(k.startswith("outcome_unknown") for k in life.events())


def test_rearm_after_expired_outcome_alerts_human(life):
    """隔夜过期单的成交数是「已记账量」而非柜台实况（可能昨天真成交了）→
    重布防保住当日保护的同时，必须告警让人核对账本，别按未证实的 0 成交静默重下。"""
    life.write([_life_rule(pending_order_id="T9001", pending_volume=500,
                           fired_ts="2026-09-11T14:30:00+08:00")])
    life.outcomes({"T9001": {"status": "expired", "filled": 0, "wanted": 500,
                             "ts": LIFE_NOW.isoformat()}})

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    assert life.rules()[0]["plan_seq"] == 1          # 仍重布防：持仓当天不能裸奔
    ev = [v for v in life.events().values() if v["kind"] == "refire"]
    assert ev and ev[0]["alert"] is True and "核对" in ev[0]["msg"]


def test_refire_events_of_two_orders_do_not_clobber_each_other(life):
    """同一天同代码的两笔委托各自重布防 → 两条事件都要在。

    record_event 的去重键默认是 kind:code:日期，两条会互相覆盖：后写的那条能把
    alert=True 的告警吞掉（001312 就曾同时挂两条止损）。按键改到委托号后互不影响。
    """
    life.write([_life_rule(pending_order_id="T1", pending_volume=500, fired_ts="x",
                           created_ts="2026-09-12T09:35:00+08:00"),
                _life_rule(pending_order_id="T2", pending_volume=500, fired_ts="x",
                           created_ts="2026-09-12T09:36:00+08:00")])
    life.outcomes({"T1": {"status": "rejected", "filled": 0, "wanted": 500,
                          "ts": LIFE_NOW.isoformat()},
                   "T2": {"status": "partial_cancelled", "filled": 100, "wanted": 500,
                          "ts": LIFE_NOW.isoformat()}})

    W.run_watch(life.broker, now=LIFE_NOW)

    keys = [k for k, v in life.events().items() if v["kind"] == "refire"]
    assert len(keys) == 2 and any("T1" in k for k in keys) and any("T2" in k for k in keys)


class _DedupBroker(_LifeBroker):
    """模拟桥的**入口去重**（Windows 侧 plan_executor._executed_plans，内存、重启即丢）：
    同一个 plan 号第二次执行 → 409 DUPLICATE_PLAN，桥回 status=duplicate。"""

    def __init__(self):
        super().__init__()
        self.executed, self.duplicates = set(), []

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        if plan_id in self.executed:
            self.duplicates.append(plan_id)
            return {"order_id": "", "status": "duplicate",
                    "message": "plan 已执行过（桥去重）"}
        self.executed.add(plan_id)
        return super().sell(sig, date, code, vol, price, plan_id)


def test_sibling_rules_in_same_minute_get_distinct_plan_ids():
    """同批写下（created_ts 只差微秒）的同代码多条规则 → 各有各的 plan 号。

    001312 实录：17.05(0.3) / 16.60(1.0) / 17.05(1.0) 三条同一分钟由一次分析写下。
    号只取到分钟 → 三条共用一个号 → 桥对第二条起永久判 duplicate。
    """
    a = _life_rule(stop_loss=17.05, pct=0.3, created_ts="2026-09-12T09:35:00.100000+08:00")
    b = _life_rule(stop_loss=16.60, pct=1.0, created_ts="2026-09-12T09:35:00.100003+08:00")
    c = _life_rule(stop_loss=17.05, pct=1.0, created_ts="2026-09-12T09:35:00.100010+08:00")

    ids = {W._watch_plan_id(LIFE_AGENT, r) for r in (a, b, c)}

    assert len(ids) == 3


def test_deeper_stop_can_fire_after_shallow_order_filled(life, monkeypatch):
    """浅止损成交消费后，同批写下的**更深止损**必须还能真下单（001312 的真实形态）。

    旧号只取到分钟 → 两条共号 → 第二条触发时被桥判 duplicate，回捞到的唯一候选又是
    已记账的第一笔（不接管）→ dup_unresolved 空转：价格继续下探也没有第二笔保护性
    卖出——**越深的保护越先失效**。正确语义：第二条带自己的号真下单。
    """
    monkeypatch.setattr(life, "broker", _DedupBroker())
    life.write([_life_rule(stop_loss=17.10, pct=0.5,
                           created_ts="2026-09-12T09:35:00.100000+08:00"),
                _life_rule(stop_loss=16.60, pct=1.0,
                           created_ts="2026-09-12T09:35:00.100003+08:00")])

    # 第 1 轮：现价 17.00 跌破 17.10 → 浅止损下单 500 股
    assert W.run_watch(life.broker, now=LIFE_NOW)["placed"] == 1
    oid = life.rules()[0]["pending_order_id"]
    assert oid and [s["volume"] for s in life.broker.sold] == [500]

    # 满额成交（对账已把它移出在途表）→ 第 2 轮消费掉浅止损那条
    life.outcomes({oid: {"status": "filled", "filled": 500, "wanted": 500,
                         "ts": LIFE_NOW.isoformat()}})
    F.save_pending([])
    assert W.run_watch(life.broker, now=LIFE_NOW)["consumed"] == 1

    # 价格继续下探到更深止损下方 → 第 3 轮：这条必须真下单（不许被判 duplicate）
    life.price(16.50)
    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts["placed"] == 1
    assert life.broker.duplicates == []
    assert [s["volume"] for s in life.broker.sold] == [500, 1000]   # 桩账户可卖量恒 1000


class _SameDayGuardBroker(_LifeBroker):
    """模拟桥的**第二层**去重（plan_executor._execute_one：当日已有同代码同方向
    「成交」（Status=3）→ 这笔不进柜台，回 200 + status=duplicate + 中文原因）。

    与 plan 级去重（上面 _DedupBroker）是两回事：那个是「号已用过」，这个是
    「今天这个方向已经卖过一笔了」——号是新的、单却依然进不了柜台。"""

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price, "plan_id": plan_id})
        return {"order_id": "", "status": "duplicate",
                "message": "当日已有同方向成交, 跳过"}


def test_same_day_filled_skip_keeps_rule_and_says_why(life, monkeypatch):
    """桥的「当日已有同方向成交，跳过」落到哨兵侧：条件位保留 + 事件带桥的原话。

    001312 的第二段退出（17.05/pct 1.0）与更深的 16.60 在周一都不会兑现：第一笔
    300 股成交后，桥的这一层会把当天该代码所有同向卖单都跳过（无论哪个 agent、
    号是不是新的）——第二段要等到下一个交易日才有真单。桥在 Windows 侧、本侧
    修不了；能做的是**别让日志说错话**：与 409 plan 去重的「单可能已在柜台、
    成交未记账」不同，这笔根本没进柜台，读错方向会去查一笔不存在的委托。
    """
    monkeypatch.setattr(life, "broker", _SameDayGuardBroker())
    life.write([_life_rule()])

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts["placed"] == 0 and counts["consumed"] == 0 and counts["discarded"] == 0
    assert [s["volume"] for s in life.broker.sold] == [500]      # 试过，被桥跳过
    r = life.rules()[0]
    assert r["stop_loss"] == 17.10 and not r.get("pending_order_id")
    ev = json.dumps(life.events(), ensure_ascii=False)
    assert "dup_unresolved" in ev and "当日已有同方向成交" in ev   # 桥的原话必须带出来


def test_illegal_volume_discard_records_event(life, monkeypatch):
    """可卖量按板块手数合规后为 0 → 规则作废，且留 watch_discard 事件（此前只有 print）。"""
    monkeypatch.setattr(W, "round_sell_qty", lambda code, raw, avail: 0)
    life.write([_life_rule()])

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == {"placed": 0, "consumed": 0, "discarded": 1}
    assert life.rules() == []
    assert "watch_discard" in json.dumps(life.events(), ensure_ascii=False)


def test_wrong_side_discard_records_event(life):
    """方向标错的一侧被丢时留事件（条件位为什么消失必须可回溯）。"""
    life.price(17.50)                                   # 现价仍在昨收上方
    life.write([_life_rule(stop_loss=18.0, take_profit=19.0)])

    counts = W.run_watch(life.broker, now=LIFE_NOW)

    assert counts == ZERO
    r = life.rules()[0]
    assert r["stop_loss"] is None and r["take_profit"] == 19.0   # 丢标错的一侧
    assert any(k.startswith("watch_discard") for k in life.events())


# ---------- 同 agent 规则落盘：去重 + 锁 ----------

def test_save_watch_rules_dedupes_identical_duplicates(file_watch):
    """同 code 同价位同比例重复项只留一条；不同价位的多条保留（001312 实录 17.05×2）。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "001312.SZ", "stop_loss": 17.05, "pct": 0.5},
        {"action": "watch", "code": "001312.SZ", "stop_loss": 17.05, "pct": 0.5},
        {"action": "watch", "code": "001312.SZ", "stop_loss": 16.60, "pct": 0.5},
    ])

    assert n == 2
    assert [r["stop_loss"] for r in file_watch.read()["agentA"]] == [17.05, 16.6]


def test_save_watch_rules_waits_for_watch_lock(file_watch):
    """别的写者持锁时 save_watch_rules 必须排队（读-改-写交叠会互相覆盖）。"""
    import threading

    holder = open(W._watch_lock_file(), "a+")
    fcntl.flock(holder, fcntl.LOCK_EX)
    t = threading.Thread(target=W.save_watch_rules, args=(
        "agentA", [{"action": "watch", "code": "600362.SH", "stop_loss": 50.0, "pct": 1.0}]))
    try:
        t.start()
        t.join(0.5)
        assert t.is_alive()                 # 在等锁：没有抢先落盘
        assert not file_watch.path.exists()
    finally:
        holder.close()                      # 释放 → 落盘
    t.join(5)
    assert not t.is_alive()
    assert [r["code"] for r in file_watch.read()["agentA"]] == ["600362.SH"]


# ---------- 决策比例缺失/为 0：不许静默丢掉条件位（2026-09-12 审查 HIGH-1）----------
# parse_intraday_decision 把「模型没给 pct」压成 0.0（`float(x.get("pct") or 0)`），
# 与本脚本「0 = 不表达卖出量」撞车：模型漏个字段，该挂的止损被整条丢掉，且整条
# 链路无声无息（无 print、无事件、无计数）。靠 pct_given 区分后：**没给按全仓挂**
# （漏挂 = 这个防守位当日无人执行），**明说 0 不挂但落事件**。

def _recorded_events(file_watch) -> list:
    try:
        doc = json.loads((file_watch.path.parent / "events.json").read_text(encoding="utf-8"))
    except OSError:
        return []
    return list(doc.values())


def test_watch_rule_without_pct_is_armed_at_full_position(file_watch):
    """模型漏给 pct → 按全仓挂上，不丢防守位（真实链路：解析器标注 pct_given=False）。"""
    from live_prompt_context import parse_intraday_decision

    decisions = parse_intraday_decision(
        '{"decisions": [{"action": "watch", "code": "600309.SH", "stop_loss": 75.5}]}')

    assert decisions[0]["pct_given"] is False
    assert W.save_watch_rules("agentA", decisions) == 1

    r = file_watch.read()["agentA"][0]
    assert r["stop_loss"] == 75.5 and r["pct"] == 1.0
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_missing"]
    assert ev and ev[0]["alert"] is False        # 留痕但不打扰人（旧口径的既有行为）


def test_watch_rule_with_explicit_zero_pct_is_not_armed_but_traced(file_watch):
    """明说 pct=0（不表达卖出量）→ 不挂，但必须留事件；旧实现把它当 100% 全减。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "600309.SH", "stop_loss": 75.5, "pct": 0,
         "pct_given": True}])

    assert n == 0
    assert "agentA" not in file_watch.read()
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_zero"]
    assert ev and ev[0]["alert"] is False


def test_watch_rule_without_pct_key_from_raw_caller_falls_back_to_full(file_watch):
    """不经解析器的调用方（post_review 自己造决策）没有 pct_given 键 → 按「已给」
    语义走 `_pct` 缺省，仍是 100%（旧口径），不会被新分支改成丢弃。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "600362.SH", "stop_loss": 50.0}])

    assert n == 1
    assert file_watch.read()["agentA"][0]["pct"] == 1.0


def test_watch_rule_with_zero_pct_and_no_pct_given_key_is_treated_as_explicit(file_watch):
    """真缺省分支：调用方**写了** `pct: 0`、但没带 pct_given 键（手写 live_watch.json /
    未来新增调用点）→ 按「明说 0」处理（不挂 + 留痕），不会被静默改成全仓全减。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "600362.SH", "stop_loss": 50.0, "pct": 0}])

    assert n == 0
    assert "agentA" not in file_watch.read()
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_zero"]
    assert ev and ev[0]["alert"] is False


def test_watch_rule_with_nan_pct_falls_back_to_full_not_dropped(file_watch, capsys):
    """json.loads 接受字面量 NaN；_pct 回退全仓（NaN 挂上去会让触发时刻整轮哨兵
    抛 ValueError 且每分钟复现），脏值仍要留痕。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "600362.SH", "stop_loss": 50.0, "pct": float("nan")}])

    assert n == 1
    assert file_watch.read()["agentA"][0]["pct"] == 1.0
    assert "非有限数" in capsys.readouterr().out


def test_dirty_pct_watch_decision_arms_full_and_traces_dirty(file_watch, capsys):
    """解析器判脏的 watch 决策（NaN/Inf/"0.3股" → pct_bad_raw）：

    - 保护层按全仓挂（漏挂 = 这个防守位当日无人执行）；
    - 留痕说真话：事件是 watch_pct_dirty（解析不出），不是 watch_pct_missing（没给）——
      两种原因排查方向不同，混成一条会掩盖「模型输出了坏值」这类系统性问题。"""
    from live_prompt_context import parse_intraday_decision

    decisions = parse_intraday_decision(
        '{"decisions": [{"action": "watch", "code": "600362.SH", "stop_loss": 50.0,'
        ' "pct": "0.3股"}]}')

    assert W.save_watch_rules("agentA", decisions) == 1
    assert file_watch.read()["agentA"][0]["pct"] == 1.0
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_dirty"]
    assert ev and ev[0]["alert"] is False and "0.3股" in ev[0]["msg"]
    assert not [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_missing"]


def test_nan_pct_watch_decision_arms_full_and_traces_dirty(file_watch):
    """NaN（JSON 字面量，模型可产出）走 parse_pct 的 dirty 分支，不是 missing。"""
    from live_prompt_context import parse_intraday_decision

    decisions = parse_intraday_decision(
        '{"decisions": [{"action": "watch", "code": "600362.SH", "stop_loss": 50.0,'
        ' "pct": NaN}]}')

    assert W.save_watch_rules("agentA", decisions) == 1
    assert file_watch.read()["agentA"][0]["pct"] == 1.0
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_dirty"]
    assert ev and "nan" in ev[0]["msg"].lower()


def test_watch_rule_with_negative_pct_message_shows_actual_value(file_watch, capsys):
    """pct=-1 落在 pct<=0 分支：事件/print 要显示实际值，不能谎称「pct=0」。"""
    n = W.save_watch_rules("agentA", [
        {"action": "watch", "code": "600362.SH", "stop_loss": 50.0, "pct": -1}])

    assert n == 0
    assert "-1" in capsys.readouterr().out
    ev = [e for e in _recorded_events(file_watch) if e["kind"] == "watch_pct_zero"]
    assert ev and "-1" in ev[0]["msg"]
