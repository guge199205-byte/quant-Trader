"""老 schema 条件位（生产存量）在哨兵新链路下的行为——周一开盘前的只读演练。

背景（2026-09-12 批 10 附带复核）：`data/live_watch.json` 现存 7 条规则全部由
**批 3 之前的旧代码**写入，字段只有 code/stop_loss/take_profit/move_stop/pct/reason/
created_ts——没有新链路引入的 pending_order_id / pending_volume / fired_ts /
stop_from_move / plan_seq。2026-09-14（周一）09:25 哨兵启动后读到的就是这批老规则，
新代码必须：

  ① 不因缺键抛异常（run_watch 崩 = 当日全部条件位无人守，持仓裸奔）；
  ② 不把真实条件位误判成方向标错而丢弃/翻转错误的那一侧；
  ③ 001312 那条 `stop_loss=17.05` 高于周五收盘（理由自述『距现价+0.6%』→ ≈16.95）、
     理由无方向线索的规则，要被方向自洽护栏**翻成止盈**，而不是现价 17.0x 时按
     `price <= stop_loss` 假触发（旧代码里开盘就白卖 30%）；
  ④ 真有破位的（603213 收盘无止损的受压腿）照常触发下单并打标（不是"下单即消费"）；
  ⑤ 翻转/丢弃是**会落盘的语义改写** → 必须同时落事件面（watch_direction_fix /
     watch_discard，alert=False），且 dry-run 下与落盘同频地**不写**（批 13）。

规则原文是 2026-09-12 对 `data/live_watch.json` 的逐字生产快照；测试全程用 tmp_path
里的临时文件（watch 文件/台账/在途表/事件面/日志目录全部隔离），不碰任何生产状态。

变异验证（2026-09-12 实测，非推测）：把 run_watch 里的 `_fix_rule_direction(...)` 调用
改成 no-op（= 批 3 之前的行为）→ 本文件 3 failed（①③④），日志实录「001312.SZ 现价
¥17.00 跌破止损位 ¥17.05（减仓 30%）…已受理 300 股」+ 第二条 17.05 规则试图再卖 1000 股
（被在途闸门拦下）——这就是周一若没有方向护栏会真实发生的开盘假卖。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_price_watch as W  # noqa: E402

CN = W.CN_TZ
NOW = datetime(2026, 9, 14, 9, 25, 0, tzinfo=CN)          # 周一开盘前哨兵首轮
ZERO = {"placed": 0, "consumed": 0, "discarded": 0}

# 2026-09-12 生产快照（data/live_watch.json），逐字，勿改内容——本文件就是拿它当语料
LEGACY_RULES = {
    "deepseek-v4-flash": [
        {"code": "603213.SH", "stop_loss": 13.2, "take_profit": None, "move_stop": None,
         "pct": 1.0, "reason": "收盘无止损的受压腿，防守位必须由哨兵代执行",
         "created_ts": "2026-09-11T15:36:06.237852+08:00"},
        {"code": "600817.SH", "stop_loss": 9.3, "take_profit": None, "move_stop": None,
         "pct": 1.0, "reason": "最大市值腿且L2现派发迹象，锁住盈利腿下行风险",
         "created_ts": "2026-09-11T15:36:06.237881+08:00"},
    ],
    "deepseek-v4-pro": [
        {"code": "001312.SZ", "stop_loss": 17.05, "take_profit": None, "move_stop": None,
         "pct": 0.3,
         "reason": "17.05为09-10平台中轴、距现价+0.6%，弱市先降一档锁弹性，避免09-04式整数位硬等让利",
         "created_ts": "2026-09-11T15:37:21.166780+08:00"},
        {"code": "001312.SZ", "stop_loss": 16.6, "take_profit": None, "move_stop": None,
         "pct": 1.0,
         "reason": "09-04以来成本堆积下沿，折现口径下破位无支撑；本次不再打折为清仓以下的模糊处置，并在16.20补全清",
         "created_ts": "2026-09-11T15:37:21.166803+08:00"},
        {"code": "001312.SZ", "stop_loss": 17.05, "take_profit": None, "move_stop": None,
         "pct": 1.0,
         "reason": "距现价仅+0.6%且落在今日振幅16.84-17.17内，属高概率早盘噪音触发位，接近即减以防再次假触发污染归因",
         "created_ts": "2026-09-11T15:37:21.166810+08:00"},
    ],
    "glm-5.3-flash": [
        {"code": "600817.SH", "stop_loss": 9.3, "take_profit": None, "move_stop": None,
         "pct": 0.5, "reason": "跌破9.30减50%：恢复丢失的防守位，防L2价量背离(-0.249)派发兑现",
         "created_ts": "2026-09-11T15:40:53.986717+08:00"},
        {"code": "000028.SZ", "stop_loss": 19.3, "take_profit": None, "move_stop": None,
         "pct": 0.5, "reason": "跌破19.30减50%：补挂计划防守位，破位即降敞口不等更深",
         "created_ts": "2026-09-11T15:40:53.986739+08:00"},
    ],
}

# 「安静开盘」行情：各票现价都在自己条件位上方（001312 的 17.05 翻转后是止盈位）
QUIET = {"001312.SZ": (17.00, 16.95),   # 昨收 16.95 = 理由「距现价+0.6%」的现价
         "603213.SH": (13.30, 13.25),
         "600817.SH": (9.55, 9.60),
         "000028.SZ": (19.40, 19.35)}

HOLDINGS = {"deepseek-v4-flash": {"603213.SH": 500, "600817.SH": 1000},
            "deepseek-v4-pro": {"001312.SZ": 1000},
            "glm-5.3-flash": {"600817.SH": 1000, "000028.SZ": 1000}}


class _LegacyBroker:
    """桩桥：假活硬闸要的 asset+positions 都给全，sell 返回委托号。"""

    def __init__(self):
        self.sold = []

    def _account_query(self):
        pos = [{"stock_code": c, "available_volume": v}
               for holds in HOLDINGS.values() for c, v in holds.items()]
        pos = list({p["stock_code"]: p for p in pos}.values())
        return {"asset": {"asset": 300000.0, "cash": 1000.0}, "positions": pos}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.sold.append({"code": code, "volume": vol, "price": price, "plan_id": plan_id})
        return {"order_id": f"L{len(self.sold)}", "status": "submitted"}


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    """老规则演练台：文件全在 tmp_path，桥与行情是桩，其余走真实实现。"""
    import types as _t

    import live_fills
    import live_hourly_analysis
    import live_ledger

    wf = tmp_path / "live_watch.json"
    monkeypatch.setattr(W, "WATCH_FILE", wf)
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)
    monkeypatch.setattr(W, "POLL_SLEEP_SEC", 0)
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(live_fills, "OUTCOMES_FILE", tmp_path / "outcomes.json")
    monkeypatch.setattr(live_fills, "reconcile", lambda broker: None)
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)

    box = {"px": dict(QUIET)}
    monkeypatch.setattr(W, "_last_price", lambda broker, code: box["px"][code])

    wf.write_text(json.dumps(LEGACY_RULES, ensure_ascii=False), encoding="utf-8")
    ledger = {"version": 1, "agents": {
        a: {"virtual_cash": 100000.0,
            "positions": {c: {"volume": v, "cost_price": 10.0} for c, v in hs.items()}}
        for a, hs in HOLDINGS.items()}}
    (tmp_path / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    def rules():
        return json.loads(wf.read_text(encoding="utf-8"))

    def events():
        try:
            return json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
        except OSError:
            return {}

    def price(code, px, prev):
        box["px"][code] = (px, prev)

    return _t.SimpleNamespace(broker=_LegacyBroker(), rules=rules, events=events,
                              price=price, path=wf)


def _by_code(doc, agent, code):
    return [r for r in doc[agent] if r["code"] == code]


def test_quiet_open_keeps_all_seven_rules_and_places_nothing(legacy):
    """①/② 安静开盘：不崩、不下单、不丢弃——7 条规则一条不少地留着继续守。"""
    counts = W.run_watch(legacy.broker, now=NOW)

    assert counts == ZERO
    assert legacy.broker.sold == []
    doc = legacy.rules()
    assert sum(len(v) for v in doc.values()) == 7
    assert not [e for e, evs in legacy.events().items() if e == "watch_discard"]


def test_stop_above_prev_close_flips_to_take_profit_not_fake_sell(legacy):
    """③ 001312 的 17.05：护栏翻成止盈（不在现价 17.00 假触发）；下方 16.60 不动。"""
    counts = W.run_watch(legacy.broker, now=NOW)

    assert counts == ZERO and legacy.broker.sold == []
    pro = _by_code(legacy.rules(), "deepseek-v4-pro", "001312.SZ")
    flipped = [r for r in pro if r["take_profit"] == 17.05]
    assert len(flipped) == 2                     # pct 0.3 与 1.0 两条同款都翻
    assert all(r["stop_loss"] is None for r in flipped)
    deep = [r for r in pro if r["stop_loss"] == 16.6]
    assert len(deep) == 1 and deep[0]["take_profit"] is None   # 昨收下方的真实止损不翻


def test_hint_matched_stops_keep_direction(legacy):
    """② 有方向线索的止损（『跌破』『防守』『止损』）不翻、不丢。"""
    W.run_watch(legacy.broker, now=NOW)

    doc = legacy.rules()
    assert _by_code(doc, "deepseek-v4-flash", "603213.SH")[0]["stop_loss"] == 13.2
    assert _by_code(doc, "deepseek-v4-flash", "600817.SH")[0]["stop_loss"] == 9.3
    assert _by_code(doc, "glm-5.3-flash", "600817.SH")[0]["stop_loss"] == 9.3
    assert _by_code(doc, "glm-5.3-flash", "000028.SZ")[0]["stop_loss"] == 19.3


def test_real_breakout_fires_and_tags_rule(legacy):
    """④ 真破位照常下单：603213 现价 13.10 跌破 13.20 → 下全仓单并打标（不消费）。"""
    legacy.price("603213.SH", 13.10, 13.25)

    counts = W.run_watch(legacy.broker, now=NOW)

    assert counts["placed"] == 1 and counts["discarded"] == 0
    assert [(s["code"], s["volume"]) for s in legacy.broker.sold] == [("603213.SH", 500)]
    r = _by_code(legacy.rules(), "deepseek-v4-flash", "603213.SH")[0]
    assert r["pending_order_id"] == "L1" and r["pending_volume"] == 500


def test_production_watch_file_is_still_parseable():
    """上游契约：生产 live_watch.json 的每条规则都有 code（哨兵按 r["code"] 取行情）。

    只做「可被哨兵消费」的最低形状检查——不绑定具体条目（规则随行情每天变）。
    """
    p = ROOT / "data" / "live_watch.json"
    if not p.is_file():
        pytest.skip("生产 live_watch.json 不存在")
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    for agent, rules in doc.items():
        assert agent and isinstance(rules, list)
        for r in rules:
            assert isinstance(r, dict) and str(r.get("code") or "")


def test_direction_flip_records_audit_event(legacy):
    """③ 翻转是**语义改写且会落盘** → 必须留事件（此前只有 print，事后无从回溯：
    条件位为什么从止损变成了止盈，只有当时的终端输出记得）。alert=False：不是资金
    风险，但审计面必须查得到。"""
    counts = W.run_watch(legacy.broker, now=NOW)

    assert counts == ZERO
    fix = {k: v for k, v in legacy.events().items() if v.get("kind") == "watch_direction_fix"}
    assert fix, "方向翻转没有落事件"
    assert any("001312.SZ" in v["msg"] and "17.05" in v["msg"] for v in fix.values())
    assert all(v["alert"] is False for v in fix.values())
    # 两条同款（pct 0.3 / 1.0）翻的是同一个价位 → 合并成一条事件，不重复打扰
    assert len([k for k in fix if "001312.SZ" in k]) == 1


def _with_mislabeled_side(legacy) -> None:
    """给 glm 补一条 sl 侧标错（理由『涨到』+ 止损 19.5 > 昨收 19.35）且 tp 侧已在的
    规则 → 护栏会丢弃 sl 侧（走 `_discard_event` 分支，不是翻转分支）。"""
    doc = {a: [dict(r) for r in rs] for a, rs in LEGACY_RULES.items()}
    doc["glm-5.3-flash"].append(
        {"code": "000028.SZ", "stop_loss": 19.5, "take_profit": 21.0, "move_stop": None,
         "pct": 1.0, "reason": "涨到21.0锁利", "created_ts": "2026-09-11T15:41:00+08:00"})
    legacy.path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("dry", [True, False], ids=["--dry-run", "真跑"])
def test_discard_event_follows_persist_rule(legacy, dry):
    """丢弃侧的事件必须与**落盘**同频：真跑时留 watch_discard（可回溯），dry-run 时
    规则根本没被改（见批 12），事件面也不许写——否则「试运行」在生产事件文件里留下
    一条从未发生的处置记录。"""
    _with_mislabeled_side(legacy)

    counts = W.run_watch(legacy.broker, dry_run=dry, now=NOW)

    assert counts == ZERO and legacy.broker.sold == []
    evs = legacy.events()
    if dry:
        assert evs == {}
        assert len(_by_code(legacy.rules(), "glm-5.3-flash", "000028.SZ")) == 2
    else:
        assert any(v.get("kind") == "watch_discard" for v in evs.values())
        kept = _by_code(legacy.rules(), "glm-5.3-flash", "000028.SZ")
        assert [r["stop_loss"] for r in kept] == [19.3, None]   # sl 侧已丢，tp 侧留着


@pytest.mark.parametrize("switch_on", [True, False], ids=["--dry-run", "执行开关关闭"])
def test_dry_run_does_not_rewrite_watch_file(legacy, monkeypatch, switch_on):
    """dry-run / 执行开关关闭（自动降级 dry-run）**不得回写条件位文件**。

    计划书原则「dry-run 路径不得改动任何持久状态」；本轮演练里 001312 的两条 17.05
    会被方向护栏在内存里翻成止盈（`_fix_rule_direction` 就地改写）、move_stop 也会
    在内存推进——若照常 flush，这些改写会落盘，与「试运行」承诺不符（且护栏是基于
    当时那条行情判断的，快照/桥抖动时不该被试运行固化）。
    规则保留 ≠ 必须回写：dry-run 下无人消费/丢弃规则，文件原样即保留。
    """
    import live_hourly_analysis

    if not switch_on:
        monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: False)

    before = legacy.path.read_bytes()
    counts = W.run_watch(legacy.broker, dry_run=switch_on, now=NOW)

    assert counts == ZERO and legacy.broker.sold == []
    assert legacy.path.read_bytes() == before          # 逐字节不变（含结尾换行）
    assert legacy.events() == {}                       # 事件面同样零落盘（方向翻转不写）
    assert sum(len(v) for v in legacy.rules().values()) == 7
