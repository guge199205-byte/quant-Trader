"""下单路径的生产化护栏（2026-09-11 移植 quantmind QMT 真单链路压测结论）。

quantmind 2026-09-10~11 对真账户压测（40327478）得到的确定性结论与在本仓的落点：

  1. 卖出保护价（跌停价）报单 = 扫单成交：挂 ¥2.21（跌停）成交 ¥2.34——挂单价只是
     「愿卖的最低」，成交按盘口买一。故止损卖出报跌停价：只要盘口有买盘一定成交，
     且不会真按跌停价卖出。原来的「现价×0.99」在近跌停时会报出**低于跌停价**的
     不合法价（本仓桥容忍，真柜台可能废单）。
  2. 跌停封死不是「跳过」，是**挂跌停价排队** + 如实告警（无买盘本来也卖不掉）。
  3. 幂等委托号：崩溃重试复用同一 plan_id，桥内去重（否则崩溃一次就重复卖一笔）。
  4. 在途闸门：同标的同方向已有未确认委托 → 不再下第二笔。
  5. 提交前价格保护带：报价超出涨跌停带（含 2% 容差）→ 拒单并告警，防程序 bug
     把离谱价格当市价单打出去；**取不到行情/市价本身在带外（新股等）则放行**。
  6. 未成交/部分成交/收盘未了结 → 需要人工知晓的事件（此前只写 jsonl，零告警）。
  7. duplicate 的两处（2026-09-11 代码审查 HIGH）：桥的 plan 去重是 HTTP 409
     DUPLICATE_PLAN（不是内联 status），客户端必须认出来才不会每分钟空转；
     回捞时只接管**未记账**的委托——已记过账的再补挂 = 账本双记卖出。
  8. 下单成功 ≠ 规则消费（2026-09-12 P0-3，001312 实录）：规则保留并打标
     pending_order_id，由终态台账推进生命周期；回捞到「终态未成交」的废单
     不接管（挂了也会被 reconcile 立刻移除），换 plan 号重布防。
  9. 缺省委托号**每次调用唯一**（2026-09-12）：桥按 plan 号做入口去重，秒级时间戳
     让同秒的第二笔单被当成「已执行过」而**不进柜台**——与哨兵批 13d 同一类问题
     （那边是委托号只取到分钟）。显式传入的 plan_id 不受影响，那是规则级幂等键。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_order_guards.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills as F  # noqa: E402
import live_price_watch as W  # noqa: E402
from ashare_rules import protect_sell_price  # noqa: E402

CN = W.CN_TZ
AGENT = "deepseek-v4-pro"
PREV = 17.22          # 昨收（福恩 001312.SZ）
LIMIT_DOWN = 15.50    # 17.22 × 0.9 = 15.498 → 四舍五入 15.50


class FakeBroker:
    """只记录下单调用；get_orders 用于 duplicate 后的委托号回捞。"""

    def __init__(self, orders=None, result=None):
        self.sold = []
        self._orders = orders or []
        self._result = result or {"order_id": "T1001", "status": "submitted",
                                  "message": "已受理"}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price,
                          "plan_id": plan_id})
        return dict(self._result)

    def get_orders(self, stock_code="", cancelable_only=False):
        return [o for o in self._orders if not stock_code
                or o.get("stock_code") == stock_code]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 pending/事件/日志文件；返回 (broker, rule, events读取器)。"""
    import live_trade_picks

    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)
    # 成交流水（fill_recorded 的判据）落在 live_trade_picks.LOG_DIR
    monkeypatch.setattr(live_trade_picks, "LOG_DIR", tmp_path)

    def events():
        try:
            return json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
        except OSError:
            return {}
    broker = FakeBroker()
    rule = {"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None,
            "pct": 0.5, "reason": "测试", "created_ts": "2026-09-11T09:35:00+08:00"}
    return broker, rule, events


def _sell(broker, rule, price, prev=PREV, avail=1000, **kw):
    return W._execute_sell(broker, AGENT, rule, price, prev, "stop_loss", avail, **kw)


# ---------- 1. 止损卖出报价（现价-1%，且不低于跌停价） ----------

def test_stop_sell_quotes_legal_market_based_price(env):
    """常规盘中止损：报「现价−1%」——连续竞价有效竞价范围是 [基准价×98%, 涨停]，
    报跌停价（−10%）会废单（2026-09-21 002074 实录 42 笔全废、每 2 分钟重下循环）。"""
    broker, rule, _ = env
    assert _sell(broker, rule, 17.50) == "placed"
    px = broker.sold[0]["price"]
    assert px == 17.33                        # 17.50 × 0.99（HALF_UP 到分）
    assert px >= round(17.50 * 0.98, 2)       # 落在有效竞价范围下沿之内
    assert px != LIMIT_DOWN                   # 回归：不再是跌停价 15.50


def test_limit_down_day_still_places_queued_order(env):
    """跌停封死：不再「跳过保留」，而是挂跌停价排队 + 留一条告警事件。"""
    broker, rule, events = env
    px = 15.49   # 已跌破跌停价（-10.05%）
    assert _sell(broker, rule, px) == "placed"
    assert broker.sold[0]["price"] == LIMIT_DOWN
    assert "protect_queue" in json.dumps(events(), ensure_ascii=False)


# ---------- 1b. 连续废单熔断（2026-09-21，002074 42 笔实录） ----------

DAY1 = datetime(2026, 9, 21, 10, 0, tzinfo=CN)
DAY2 = datetime(2026, 9, 22, 9, 35, tzinfo=CN)   # 次日（熔断应自动失效）


def _drive_reject_rounds(env, monkeypatch, n):
    """驱动 n 轮「下单 → 柜台判废单（0 成交）」的生命周期推进。"""
    import live_account_cache as LAC
    import push_notify

    broker, rule, _ = env
    sent: list = []
    monkeypatch.setattr(push_notify, "notify", lambda t, c: sent.append(t))
    monkeypatch.setattr(LAC, "notify_throttled",
                        lambda k, t, c, gap_min=30: sent.append(t))
    monkeypatch.setattr(W, "_ledger_holds", lambda a, c: True)   # 持仓仍在（否则判已清仓消费）
    monkeypatch.setattr(F, "load_order_outcome",
                        lambda oid: {"status": "rejected", "filled": 0, "wanted": 100})
    for i in range(n):
        rule["pending_order_id"] = f"T9{i}"
        assert W._resolve_tagged(AGENT, rule, set(), now=DAY1) == "rearm"
    return broker, rule, sent


def test_consecutive_rejects_halt_auto_resubmit_for_the_day(env, monkeypatch):
    """连续 3 笔柜台废单 → 当日熔断：不再自动重下（旧行为 = 每 2 分钟一笔废单 + 刷屏）。"""
    key = f"{AGENT}:001312.SZ"
    broker, rule, sent = _drive_reject_rounds(env, monkeypatch, 2)
    assert W.load_halts()[key]["streak"] == 2          # 未到阈值：继续重布防
    assert not W._halted_now(AGENT, "001312.SZ", DAY1)
    broker, rule, sent = _drive_reject_rounds(env, monkeypatch, 1)
    assert W.load_halts()[key] == {"date": "2026-09-21", "streak": 3}
    assert sum(1 for t in sent if "熔断" in t) == 1
    # 熔断后本轮触发：条件位保留但**不下单**
    assert _sell(broker, rule, 17.50, now=DAY1) == "keep"
    assert broker.sold == []


def test_halt_resets_next_day(env, monkeypatch):
    """熔断只约束当日：次日自动恢复（不做永久状态，避免静默摘掉防守位）。"""
    broker, rule, _ = _drive_reject_rounds(env, monkeypatch, 3)
    key = f"{AGENT}:001312.SZ"
    assert W._halted_now(AGENT, "001312.SZ", DAY2) is False   # 日期不等 → 不熔断
    # 次日：计数从 0 起算（当天的第一笔废单只记 1）
    rule["pending_order_id"] = "T99"
    assert W._resolve_tagged(AGENT, rule, set(), now=DAY2) == "rearm"
    assert W.load_halts()[key] == {"date": "2026-09-22", "streak": 1}
    assert _sell(broker, rule, 17.50, now=DAY2) == "placed"


def test_halt_survives_hourly_rule_rebuild(env, monkeypatch):
    """HIGH-1 回归（2026-09-21 审查）：整点轮整组重建 watch 文件后熔断仍生效。

    熔断计数放规则字典里时，`save_watch_rules → _rules_from_decisions` 的整组重建
    （只留 code/价位/pct/reason/created_ts）会把它抹掉 → 熔断退化成「每重建周期 3 笔
    废单」。计数落 sidecar 后，重建不影响停手。"""
    broker, rule, _ = _drive_reject_rounds(env, monkeypatch, 3)
    rebuilt = {"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None,
               "pct": 0.5, "reason": "整点轮重建", "created_ts": "2026-09-21T13:35:00+08:00"}
    W.save_watch({AGENT: [rebuilt]})                       # 生产写路径（原子写）
    rule2 = W.load_watch()[AGENT][0]
    assert "halt_date" not in rule2 and "reject_streak" not in rule2   # 重建确实抹掉了字段
    assert _sell(broker, rule2, 17.50, now=DAY1) == "keep"             # 但熔断仍生效
    assert broker.sold == []


def test_filled_consume_resets_reject_streak(env, monkeypatch):
    """MEDIUM-1 回归（2026-09-21 审查）：成交/清仓消费要清零连续废单计数。

    旧实现唯一清零点在「未卖完」分支：2 废 + 1 成交 + 1 废 = 熔断（文案还宣称
    「连续 3 笔废单」）。2026-09-21 002074 真成交后 sidecar 残留 streak=1 就是活体样本。
    """
    key = f"{AGENT}:001312.SZ"
    broker, rule, _ = _drive_reject_rounds(env, monkeypatch, 2)
    assert W.load_halts()[key]["streak"] == 2
    monkeypatch.setattr(F, "load_order_outcome",
                        lambda oid: {"status": "filled", "filled": 500, "wanted": 500})
    rule["pending_order_id"] = "T95"
    assert W._resolve_tagged(AGENT, rule, set(), now=DAY1) == "consume"
    assert key not in W.load_halts()
    assert not W._halted_now(AGENT, "001312.SZ", DAY1)
    # 消费后重新起算：下一笔废单记 1，而不是接着旧的 2
    monkeypatch.setattr(F, "load_order_outcome",
                        lambda oid: {"status": "rejected", "filled": 0, "wanted": 500})
    rule["pending_order_id"] = "T96"
    assert W._resolve_tagged(AGENT, rule, set(), now=DAY1) == "rearm"
    assert W.load_halts()[key] == {"date": "2026-09-21", "streak": 1}


def test_deterministic_sell_exception_halts_after_three(env, monkeypatch):
    """LOW-6 回归（2026-09-21 审查）：下单异常（桥本地预检/风控门/远端立即 rejected
    都以异常抛出）也计入连续废单 → 3 笔当日熔断、次日自动恢复。

    旧实现只看终态台账，异常路径永不计入 → 同一循环无限重试。"""
    import push_notify
    from agent_tools.brokers.base import BrokerError

    broker, rule, _ = env
    sent: list = []
    monkeypatch.setattr(push_notify, "notify", lambda t, c: sent.append(t))

    def boom(*a, **k):
        raise BrokerError("柜台拒绝：报价越界")

    monkeypatch.setattr(broker, "sell", boom)
    assert _sell(broker, rule, 17.50, now=DAY1) == "keep"
    assert _sell(broker, rule, 17.50, now=DAY1) == "keep"
    assert W.load_halts()[f"{AGENT}:001312.SZ"]["streak"] == 2
    assert _sell(broker, rule, 17.50, now=DAY1) == "keep"      # 第 3 笔 → 熔断
    key = f"{AGENT}:001312.SZ"
    assert W.load_halts()[key] == {"date": "2026-09-21", "streak": 3}
    assert W._halted_now(AGENT, "001312.SZ", DAY1)
    assert sum(1 for t in sent if "熔断" in t) == 1
    # 熔断后触发：连 broker.sell 都不再被调用（旧行为 = 每 2 分钟一笔废单）
    calls: list = []

    def ok(*a, **k):
        calls.append(a)
        return {"order_id": "T2001", "status": "submitted", "message": "已受理"}

    monkeypatch.setattr(broker, "sell", ok)
    assert _sell(broker, rule, 17.50, now=DAY1) == "keep"
    assert calls == []
    # 次日自动恢复（熔断只约束当日）
    assert not W._halted_now(AGENT, "001312.SZ", DAY2)
    assert _sell(broker, rule, 17.50, now=DAY2) == "placed"
    assert len(calls) == 1


def test_unknown_outcome_exception_not_counted_toward_halt(env, monkeypatch):
    """MEDIUM-2 回归（2026-09-21 审查）：连接/超时类异常（结果未知）不计入熔断。

    单可能已在柜台，计入会让桥抖动一次就停掉自动防守，告警也指错方向。"""
    import requests
    import push_notify
    from agent_tools.brokers.base import BrokerError

    broker, rule, _ = env
    monkeypatch.setattr(push_notify, "notify", lambda t, c: None)
    exc = BrokerError("桥请求失败")
    exc.__cause__ = requests.Timeout("read timeout")   # 模拟 raise ... from exc 的异常链

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(broker, "sell", boom)
    for _ in range(4):
        assert _sell(broker, rule, 17.50, now=DAY1) == "keep"
    assert W.load_halts() == {}
    assert not W._halted_now(AGENT, "001312.SZ", DAY1)


def test_unfilled_push_is_throttled_per_rule(env, monkeypatch):
    """「条件位卖出未成交」同规则 30 分钟去重——旧实现每笔废单推一条（一天 41 条）。"""
    import live_account_cache as LAC
    import push_notify

    _, rule, _ = env
    calls: list = []
    monkeypatch.setattr(push_notify, "notify", lambda t, c: calls.append(("direct", t)))
    monkeypatch.setattr(LAC, "notify_throttled",
                        lambda k, t, c, gap_min=30: calls.append((k, gap_min)))
    monkeypatch.setattr(W, "_ledger_holds", lambda a, c: True)
    monkeypatch.setattr(F, "load_order_outcome",
                        lambda oid: {"status": "rejected", "filled": 0, "wanted": 100})
    rule["pending_order_id"] = "T91"
    W._resolve_tagged(AGENT, rule, set(), now=DAY1)
    assert calls == [(f"watch_unfilled:{AGENT}:001312.SZ", 30)]   # 走限频器，不直推


def test_prev_missing_falls_back_to_minus_one_pct(env):
    """昨收缺失（K 线不足/脏数据）：无跌停价可夹取 → 纯现价-1%，不臆造价格。"""
    broker, rule, _ = env
    assert _sell(broker, rule, 17.50, prev=0) == "placed"
    assert broker.sold[0]["price"] == 17.33   # 现价×0.99（HALF_UP，与全仓一种口径）


def test_placed_order_keeps_rule_tagged_with_pending_order(env):
    """下单成功 ≠ 卖出完成：规则**保留**并打标 pending_order_id/pending_volume。

    2026-09-12 P0-3（001312 实录）：旧实现下单即消费条件位，那笔单随后被柜台判废
    （成交 0/300），持仓当日再无保护。打标是「等这笔委托归宿」的锚点。
    """
    broker, rule, _ = env
    assert _sell(broker, rule, 17.50) == "placed"
    assert rule["pending_order_id"] == "T1001"
    assert rule["pending_volume"] == 500 and rule["fired_ts"]


# ---------- 2. 在途闸门（不重复下单） ----------

def test_inflight_sell_blocks_second_order(env):
    """同标的有在途卖单 → 本轮不下单、条件位保留（防崩溃重试/多轮重复卖）。"""
    broker, rule, _ = env
    F.add_pending("T1", AGENT, "001312.SZ", "sell", 100, 15.50,
                  "2026-09-11T10:00:00+08:00")
    assert _sell(broker, rule, 17.50) == "keep"
    assert broker.sold == []
    assert F.inflight("001312.SZ", "sell")


def test_inflight_other_side_does_not_block(env):
    """买方向的在途单不该挡住卖出。"""
    broker, rule, _ = env
    F.add_pending("T1", AGENT, "001312.SZ", "buy", 100, 15.50,
                  "2026-09-11T10:00:00+08:00")
    assert _sell(broker, rule, 17.50) == "placed"
    assert len(broker.sold) == 1


# ---------- 3. 幂等委托号（崩溃重试不重复下单） ----------

def test_plan_id_stable_for_same_rule_and_differs_per_rule(env):
    """同一条件位（重启重试）plan_id 不变；规则刷新（新 created_ts）换号。"""
    _, rule, _ = env
    a = W._watch_plan_id(AGENT, rule)
    assert a == W._watch_plan_id(AGENT, rule)
    rule2 = dict(rule, created_ts="2026-09-11T10:35:00+08:00")
    assert W._watch_plan_id(AGENT, rule2) != a
    rule3 = dict(rule, code="600309.SH")
    assert W._watch_plan_id(AGENT, rule3) != a


def test_plan_id_passed_to_broker(env):
    broker, rule, _ = env
    _sell(broker, rule, 17.50)
    assert broker.sold[0]["plan_id"] == W._watch_plan_id(AGENT, rule)


def test_duplicate_plan_recovers_order_id_from_today_orders(env):
    """桥回 duplicate（上次已受理、本地没存下）→ 回捞当日委托号补挂 pending，
    否则这笔成交永远进不了分账账本。"""
    broker, rule, _ = env
    broker._result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    broker._orders = [{"order_id": "W777", "stock_code": "001312.SZ", "side": "sell",
                       "order_price": LIMIT_DOWN, "total_volume": 500,
                       "filled_volume": 500, "status": "filled"}]
    assert _sell(broker, rule, 17.50) == "keep"  # 条件位保留（见下节：卖出完成前继续守）
    pend = F.load_pending()
    assert [p["order_id"] for p in pend] == ["W777"]
    assert rule["pending_order_id"] == "W777"    # 打标：等这笔委托的归宿


def test_duplicate_without_recovery_records_event(env):
    """duplicate 但回捞不到委托号：单可能在场内也可能压根没进，状态未知 →
    条件位**保留**（消费掉等于把没卖出去的持仓从保护里摘出去），但必须留告警。"""
    broker, rule, events = env
    broker._result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    assert _sell(broker, rule, 17.50) == "keep"
    assert "dup_unresolved" in json.dumps(events(), ensure_ascii=False)


# ---------- 4. 事件留痕（reconcile 的终态/收盘提醒） ----------

def test_reconcile_records_unfilled_terminal_event(env):
    """委托终态未成交（废单/撤单/过期）→ 事件告警：止损没卖出去必须让人知道。"""
    broker, _, events = env
    F.add_pending("T9", AGENT, "001312.SZ", "sell", 500, LIMIT_DOWN,
                  "2026-09-11T10:00:00+08:00")
    broker._orders = [{"order_id": "T9", "stock_code": "001312.SZ", "side": "sell",
                       "status": "rejected", "filled_volume": 0, "filled_price": 0}]
    F.reconcile(broker)
    assert "unfilled" in json.dumps(events(), ensure_ascii=False)
    assert F.load_pending() == []


def test_close_reminder_recorded_once_for_resting_order(env):
    """14:50 后仍未了结的在途单：提醒「将随日终失效」——一天一次不刷屏。"""
    broker, _, events = env
    F.add_pending("T8", AGENT, "001312.SZ", "sell", 500, LIMIT_DOWN,
                  "2026-09-11T13:00:00+08:00")
    broker._orders = [{"order_id": "T8", "stock_code": "001312.SZ", "side": "sell",
                       "status": "submitted", "filled_volume": 0, "filled_price": 0}]
    now = datetime(2026, 9, 11, 14, 52, tzinfo=CN)
    F.reconcile(broker, now=now)
    F.reconcile(broker, now=now)
    ev = events()
    assert sum(1 for k in ev if k.startswith("close_pending")) == 1


# ---------- 5. 提交前价格保护带（tdx_bridge） ----------

def _bridge(monkeypatch, bars):
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    b = TdxBridgeBroker({"bridge_url": "http://127.0.0.1:1", "token": "x"})
    monkeypatch.setattr(b, "get_klines", lambda *a, **kw: bars)
    sent = {}

    def _post(path, payload, timeout, idempotent=True):
        sent["path"], sent["payload"] = path, payload
        return {"orders": [{"order_id": "X1", "status": "submitted"}]}

    monkeypatch.setattr(b, "_post", _post)
    return b, sent


_BARS = [{"date": "2026-09-10", "close": 10.00},
         {"date": "2026-09-11", "close": 10.05}]


def test_price_band_rejects_absurd_price(monkeypatch):
    """报价远超涨停价（程序/模型 bug 的离谱价）→ 本地拒单，不送到桥。"""
    from agent_tools.brokers.base import BrokerError

    b, sent = _bridge(monkeypatch, _BARS)
    with pytest.raises(BrokerError):
        b.sell(None, None, "600000.SH", 100, price=100.0)
    assert not sent


def test_price_band_allows_protection_price(monkeypatch):
    """跌停保护价（= 当日跌停价）合法放行。"""
    b, sent = _bridge(monkeypatch, _BARS)
    b.sell(None, None, "600000.SH", 100, price=9.00)
    assert sent["payload"]["orders"][0]["price"] == 9.00


def test_price_band_fails_open_when_market_outside_band(monkeypatch):
    """市价本身在带外（新股首日无涨跌幅等，本模块不追踪上市日）→ 规则模型不成立，
    放行不拦（不能因为自家涨跌停口径过窄把正常单拒了）。"""
    b, sent = _bridge(monkeypatch, [{"date": "2026-09-10", "close": 10.00},
                                    {"date": "2026-09-11", "close": 15.00}])
    b.sell(None, None, "600000.SH", 100, price=14.60)
    assert sent["payload"]["orders"][0]["price"] == 14.60


def test_price_band_fails_open_without_klines(monkeypatch):
    """取不到昨收（桥抖动）→ 放行（交易可用性优先，柜台仍兜底）。"""
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    b = TdxBridgeBroker({"bridge_url": "http://127.0.0.1:1", "token": "x"})

    def _boom(*a, **kw):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(b, "get_klines", _boom)
    sent = {}
    monkeypatch.setattr(b, "_post", lambda p, pl, t, idempotent=True: sent.update(
        {"payload": pl}) or {"orders": [{"order_id": "X1", "status": "submitted"}]})
    b.sell(None, None, "600000.SH", 100, price=100.0)
    assert sent["payload"]["orders"][0]["price"] == 100.0


def test_custom_plan_id_used_in_payload(monkeypatch):
    b, sent = _bridge(monkeypatch, _BARS)
    b.sell(None, None, "600000.SH", 100, price=10.00, plan_id="watch-x-1")
    assert sent["payload"]["plan_id"] == "watch-x-1"


def test_auto_plan_id_is_unique_within_same_second(monkeypatch):
    """缺省委托号必须**每次调用唯一**（2026-09-12，与批 13d 同一类问题）。

    旧实现 `baymax_{int(time.time())}_{os.getpid()}` 只到秒：同一进程同一秒内下的
    第二笔单拿到**同一个 plan 号** → 桥的入口去重（plan_executor.execute_plan:49-52）
    判「这个 plan 已执行过」→ 第二笔**根本不会送到柜台**，调用方只看到
    status=duplicate、order_id 空（09:35 调仓/整点轮一轮连下多笔，前一笔成交回报快
    时两笔就落在同一秒内；`_execute_one` 的「当日已有同方向成交」只拦同代码，
    拦不住这种**不同代码**的撞号）。与之相对：显式传入的 plan_id 仍须原样透传
    （下面是既有用例），那是规则级幂等键、同号重试必须被桥去重。

    冻结 wall clock 是稳定复现的唯一方式——不冻结时两次调用天然不在同一秒，
    旧实现也能过，那这条断言就钉不住任何东西。`time_ns()` 不受此冻结影响。
    """
    monkeypatch.setattr("time.time", lambda: 1_700_000_000)      # 固定在「同一秒」
    b, sent = _bridge(monkeypatch, _BARS)

    b.sell(None, None, "600000.SH", 100, price=10.00)
    first = sent["payload"]["plan_id"]
    b.sell(None, None, "600001.SH", 100, price=10.00)
    second = sent["payload"]["plan_id"]

    assert first != second
    # 仍是可辨识的 baymax 号（进桥日志/去重集，形状不该变）
    assert first.startswith("baymax_") and second.startswith("baymax_")


# ---------- 6. 桥 409 DUPLICATE_PLAN：是「已执行过」，不是失败（审查 HIGH A）----------
# 2026-09-11 代码审查：桥对 plan_id 的去重在 execute_plan **入口**读
# _executed_plans（plan_executor.py:49-52），HTTP 层返回 **409 DUPLICATE_PLAN**
# （routes.py:219-220）——不是 200 内联 status=duplicate。客户端原先只认内联形状，
# 409 被 raise_for_status 抛成 HTTPError → BrokerError → 哨兵走 sell_failed 异常
# 分支：同一 plan_id（条件位重试恒同号）每分钟重试一次、每分钟报一次失败、
# 条件位永远不消费、那笔已受理的委托永远补不进账本。

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _http_error(status, body):
    import requests

    err = requests.exceptions.HTTPError(f"{status} Client Error")
    err.response = _Resp(status, body)
    return err


def _bridge_raising(monkeypatch, exc):
    b, _ = _bridge(monkeypatch, _BARS)

    def _post(path, payload, timeout, idempotent=True):
        raise exc

    monkeypatch.setattr(b, "_post", _post)
    return b


def test_plan_duplicate_409_surfaces_as_duplicate_not_failure(monkeypatch):
    """409 DUPLICATE_PLAN → 返回 status=duplicate（走回捞/保留条件位），不抛错。"""
    b = _bridge_raising(monkeypatch, _http_error(409, {
        "success": False,
        "error": {"code": "DUPLICATE_PLAN", "message": "plan 已执行过", "details": {}}}))

    out = b.sell(None, None, "600000.SH", 100, price=9.00)

    assert out["status"] == "duplicate" and out["order_id"] == ""
    assert "已执行过" in out["message"]


@pytest.mark.parametrize("status,body", [
    (409, {"error": {"code": "OTHER_ERROR", "message": "别的冲突"}}),
    (502, {"error": {"code": "TDX_UNAVAILABLE", "message": "柜台不可用"}}),
    (409, ValueError("响应不是 JSON")),
])
def test_other_http_errors_still_raise(monkeypatch, status, body):
    """对照组：只有 DUPLICATE_PLAN 是「非失败」；别的 409/5xx/脏响应照旧抛错。"""
    from agent_tools.brokers.base import BrokerError

    b = _bridge_raising(monkeypatch, _http_error(status, body))

    with pytest.raises(BrokerError, match="TDX 桥下单失败"):
        b.sell(None, None, "600000.SH", 100, price=9.00)


# ---------- 7. duplicate 回捞：接管未记账的，绝不重记已记账的（审查 HIGH A）----------
# 回捞到「当日已成交且已记过账」的老单再补挂 pending，reconcile 会从
# volume_recorded=0 重新补记 → 同一笔卖出扣两次持仓、加两次现金（账本双记）。
# 触发形状：半仓止损先成交并记账；剩余仓位的另一条规则再触发时，桥按
# 「当日已有同向成交」判 duplicate，回捞拿到的正是那笔已记账的老单。

def _log_fill(tmp_path, order_id, mode="fill_confirm"):
    """在当日成交流水写一条记账线（fill_recorded 的判据）。"""
    stamp = W.now_cn().strftime("%Y%m%d")
    with open(tmp_path / f"live_trade_{stamp}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": W.now_cn().isoformat(), "mode": mode,
                             "order_id": order_id}) + "\n")


def _dup_with_order(env, order):
    broker, rule, events = env
    broker._result = {"order_id": "", "status": "duplicate", "message": "plan 已执行过"}
    broker._orders = [order]
    return broker, rule, events


def test_duplicate_recovers_live_order_and_keeps_rule_armed(env):
    """在途（未终态）那笔：补挂 pending 跟踪成交；条件位保留——卖出真完成或持仓
    清零之前继续守，期间由在途闸门保证不会下第二笔。"""
    broker, rule, _ = _dup_with_order(env, {
        "order_id": "W778", "stock_code": "001312.SZ", "side": "sell",
        "order_price": LIMIT_DOWN, "total_volume": 500, "filled_volume": 0,
        "status": "submitted"})

    assert _sell(broker, rule, 17.50) == "keep"
    assert [p["order_id"] for p in F.load_pending()] == ["W778"]


def test_duplicate_of_terminal_unfilled_rearms_with_new_plan_id(env):
    """终态未成交的废单（rejected 0/500）**不接管**：挂它进在途表只会被 reconcile
    立刻移除、下一轮再触发 → 每分钟空转，且同 plan 号被桥永久判 duplicate。

    正确动作 = 落终态台账 + 换 plan 号重新布防（调用方下一轮用新号真下单）。
    """
    broker, rule, _ = _dup_with_order(env, {
        "order_id": "W781", "stock_code": "001312.SZ", "side": "sell",
        "order_price": LIMIT_DOWN, "total_volume": 500, "filled_volume": 0,
        "status": "rejected"})

    assert _sell(broker, rule, 17.50) == "keep"
    assert F.load_pending() == []                       # 废单不进在途表
    assert rule["plan_seq"] == 1                        # 换号 → 重布防
    assert "pending_order_id" not in rule
    oc = F.load_order_outcome("W781")                   # 归宿落机器可读台账
    assert oc["status"] == "rejected" and oc["filled"] == 0 and oc["wanted"] == 500


def test_duplicate_does_not_rebook_an_already_recorded_fill(env, tmp_path):
    """已记账的成交：不许补挂（补挂 = 双记卖出）；条件位保留 + 留告警。"""
    broker, rule, events = _dup_with_order(env, {
        "order_id": "W777", "stock_code": "001312.SZ", "side": "sell",
        "order_price": LIMIT_DOWN, "total_volume": 500, "filled_volume": 500,
        "status": "filled"})
    _log_fill(tmp_path, "W777")

    assert _sell(broker, rule, 17.50) == "keep"
    assert F.load_pending() == []                       # 一笔都没补挂
    assert "dup_unresolved" in json.dumps(events(), ensure_ascii=False)


def test_duplicate_does_not_rebook_order_already_in_pending(env):
    """防御：回捞到的那笔已在在途表里（同号）→ 不重复补挂。"""
    broker, rule, _ = _dup_with_order(env, {
        "order_id": "W779", "stock_code": "001312.SZ", "side": "sell",
        "order_price": LIMIT_DOWN, "total_volume": 500, "filled_volume": 0,
        "status": "submitted"})
    F.add_pending("W779", AGENT, "001312.SZ", "sell", 500, LIMIT_DOWN,
                  "2026-09-11T10:00:00+08:00")

    _sell(broker, rule, 17.50)

    assert [p["order_id"] for p in F.load_pending()] == ["W779"]   # 还是一条


def test_duplicate_recovers_untracked_fill_once(env, tmp_path):
    """成交流水里没有该委托号 → 是「已受理但本地没存下」那笔（成交成了账外单），
    补挂一次让 reconcile 补记（fill_recorded 认内联成交线 fill.order_id 形状）。"""
    broker, rule, _ = _dup_with_order(env, {
        "order_id": "W780", "stock_code": "001312.SZ", "side": "sell",
        "order_price": LIMIT_DOWN, "total_volume": 500, "filled_volume": 500,
        "status": "filled"})
    stamp = W.now_cn().strftime("%Y%m%d")
    with open(tmp_path / f"live_trade_{stamp}.jsonl", "a", encoding="utf-8") as fh:
        # 别的单的成交线：不能因为「文件里出现过别的号」就误判已记账
        fh.write(json.dumps({"ts": "x", "mode": "execute",
                             "fill": {"order_id": "OTHER9"}}) + "\n")

    assert _sell(broker, rule, 17.50) == "keep"
    assert [p["order_id"] for p in F.load_pending()] == ["W780"]
