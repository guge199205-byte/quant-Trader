"""P2 观测层：决策记录带「候选池位置戳」（2026-09-18）。

P2（模型分数门限）的度量前提是「决策时刻该票在候选池中的排名/分数」。
decision_pool 此前**不记这个字段**，于是「低分买入的远期超额」这个问题
不是"答案为空"而是"数据不存在"——补上它才能在未来证伪或证实。

三态语义（本文件钉住的核心不变量）：
  * shown=True           该票在模型看到的候选池表里（filter_affordable 之后）
  * shown=False + rank=N 池里有、被资金闸剔除（模型看不到，只能凭持仓/新闻等选中）
  * rank=None            池外标的（既不在展示集也不在剔除名单）
池整份为空（09-11/09-15 实录）时 n_shown=0，仍是有效观测——不是"没插桩"。
只有调用方整轮不传 pool_view 才不写 pool_ctx（旧记录向后兼容）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_pool_ctx_ingest.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_fills as F  # noqa: E402
import live_ledger as L  # noqa: E402

CN = F.CN_TZ
AGENT = "deepseek-v4-pro"
BUY_CODE = "600362.SH"
NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=CN)
TS = NOW.isoformat()


def _dt():
    sys.path.insert(0, str(ROOT / "scripts"))
    import decision_track as DT

    return DT


# ---------- pool_stamp：三态派生 ----------

def test_pool_stamp_marks_shown_dropped_and_absent():
    """展示集 → state=shown；剔除名单 → state=dropped 且 rank 从整池找回。"""
    shown = [{"code": "600362.SH", "name": "江西铜业", "rank": 5,
              "score": 27.3, "fusion": None}]
    full = shown + [{"code": "300502.SZ", "name": "新易盛", "rank": 1,
                     "score": 1050.7, "fusion": None}]
    dropped = [("300502.SZ", "新易盛", 42494.0, 100)]

    st = _dt().pool_stamp(shown, dropped, full=full, pool_file="20260917_agent_picks.json")

    assert st["file"] == "20260917_agent_picks.json"
    assert st["n_shown"] == 1 and st["n_dropped"] == 1
    assert st["codes"]["600362.SH"] == {"state": "shown", "shown": True, "rank": 5,
                                        "score": 27.3, "fusion": None}
    # 被剔除的票在池里有 rank（1）——模型看不到它，但它确实是池内高分票
    assert st["codes"]["300502.SZ"] == {"state": "dropped", "shown": False, "rank": 1,
                                        "score": 1050.7, "fusion": None}


def test_pool_stamp_state_survives_rows_without_rank():
    """池行缺 rank 时，"被剔除"与"池外"必须在戳里仍可区分（09-18 审查 MEDIUM）。

    select_from_reports 的 md 兜底分支给所有行 rank=None：若分组靠 rank 反推，
    被资金闸剔除的高分票会整体并入"池外"——正是 P2 要防的反向误读。
    """
    st = _dt().pool_stamp([], dropped=[("300502.SZ", "新易盛", 42494.0, 100)],
                          full=[{"code": "300502.SZ", "name": "新易盛"}],
                          pool_file="p.json")

    assert st["codes"]["300502.SZ"]["state"] == "dropped"
    assert st["codes"]["300502.SZ"]["rank"] is None       # 名次不可得，归属仍明确


def test_pool_stamp_tolerates_rows_without_rank_or_score():
    """quantmind 池行有 rank/score，桥兜底行两样都可能缺 → None，不抛。"""
    st = _dt().pool_stamp([{"code": "600362.SH", "name": "江西铜业"}], pool_file="p.json")

    assert st["codes"]["600362.SH"] == {"state": "shown", "shown": True, "rank": None,
                                        "score": None, "fusion": None}


def test_pool_stamp_accepts_tuple_and_set_inputs():
    """非 list 序列（tuple/set）不得被读成"空池"——空池是合法观测，错读不留痕。

    函数自己的签名默认值 dropped=() 就是 tuple；只认 list 的 _rows 会把它们
    静默降级成"整份空池/无剔除"（09-18 审查 HIGH，实测过）。
    """
    dt = _dt()
    st = dt.pool_stamp(({"code": "600362.SH", "rank": 5},),
                       dropped=(("300502.SZ", "新易盛", 42494.0, 100),),
                       full=[{"code": "300502.SZ", "rank": 1}], pool_file="p.json")

    assert st["n_shown"] == 1 and st["n_dropped"] == 1
    assert st["codes"]["600362.SH"]["state"] == "shown"
    assert st["codes"]["300502.SZ"]["state"] == "dropped"


def test_pool_stamp_handles_empty_pool_as_valid_observation():
    """整份空池（09-11 实录）是有效观测：n_shown=0 且 codes 为空，不是异常。"""
    st = _dt().pool_stamp([], [], pool_file="20260911_picks.json")

    assert st == {"file": "20260911_picks.json", "n_shown": 0, "n_dropped": 0, "codes": {}}


def test_pool_stamp_ignores_rows_without_code():
    """脏行（无 code）不得污染戳表——记分卡按 code 查表，空 key 会串台。"""
    st = _dt().pool_stamp([{"name": "无代码"}, {"code": "", "rank": 9}], pool_file="p.json")

    assert st["codes"] == {}


def test_pool_stamp_normalizes_code_keys():
    """戳表键与决策 code 同口径（都 strip）：否则带空白的池行永远查不中 → 池外。"""
    st = _dt().pool_stamp([{"code": " 600362.SH ", "rank": 5}], pool_file="p.json")

    assert set(st["codes"]) == {"600362.SH"}


# ---------- ingest_decisions：落池 ----------

@pytest.fixture
def pool_file(tmp_path):
    return tmp_path / "decision_pool.jsonl"


def _ingest(pool_file, decisions, pool_view=None):
    return _dt().ingest_decisions(AGENT, decisions, TS, held_codes=set(),
                                  source="live", path=pool_file,
                                  pool_view=pool_view)


def test_ingest_writes_pool_ctx_per_decision(pool_file):
    view = {"shown": [{"code": BUY_CODE, "rank": 3, "score": 0.75, "fusion": 0.011}],
            "dropped": [], "full": [], "file": "20260915_picks.json"}

    assert _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}], view) == 1

    rec = json.loads(pool_file.read_text(encoding="utf-8").strip())
    assert rec["pool_ctx"] == {"file": "20260915_picks.json", "n_shown": 1, "n_dropped": 0,
                               "state": "shown", "shown": True, "rank": 3,
                               "score": 0.75, "fusion": 0.011}


def test_ingest_marks_off_pool_code_as_absent(pool_file):
    """池外标的（模型凭持仓/新闻自选）→ state=off、rank=None，与"池内被剔除"可区分。"""
    view = {"shown": [{"code": "000001.SZ", "rank": 1, "score": 30.0}],
            "dropped": [], "full": [], "file": "p.json"}

    _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}], view)

    ctx = json.loads(pool_file.read_text(encoding="utf-8").strip())["pool_ctx"]
    assert ctx["state"] == "off" and ctx["shown"] is False
    assert ctx["rank"] is None and ctx["n_shown"] == 1


def test_ingest_without_pool_view_writes_no_pool_ctx(pool_file):
    """整轮不传 pool_view（旧调用方/回填路径）→ 记录里没有该键，不写 null 占位。"""
    _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}])

    rec = json.loads(pool_file.read_text(encoding="utf-8").strip())
    assert "pool_ctx" not in rec


def test_ingest_treats_empty_pool_view_as_uninstrumented(pool_file):
    """空 dict ≠ 插桩：否则每条记录都成"池外"且计入覆盖率（假达标，审查 MEDIUM）。"""
    _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}], {})

    rec = json.loads(pool_file.read_text(encoding="utf-8").strip())
    assert "pool_ctx" not in rec


def test_ingest_survives_broken_pool_view(pool_file):
    """戳表**内容**异常不得带走整批决策入库（观测层绝不能反噬主链路）。"""
    assert _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}],
                   {"shown": None, "dropped": "坏", "full": 3, "file": None}) == 1

    rec = json.loads(pool_file.read_text(encoding="utf-8").strip())
    assert rec["pool_ctx"]["shown"] is False and rec["pool_ctx"]["n_shown"] == 0


def test_ingest_without_stamp_omits_key_on_pool_stamp_crash(pool_file, monkeypatch):
    """pool_stamp 自身抛异常 → 整批不写戳（与未插桩同形），决策仍全部入库。

    docstring 承诺的两条降级路径之一。不写"全体池外"是为了不谎报覆盖率——
    报告会把这类记录计进"未插桩"，看得出插桩坏了（09-18 审查 MEDIUM）。
    """
    import decision_track as DT

    def boom(*a, **k):
        raise RuntimeError("戳表构造炸了")

    monkeypatch.setattr(DT, "pool_stamp", boom)

    assert _ingest(pool_file, [{"action": "buy", "code": BUY_CODE, "pct": 0.2}],
                   {"shown": [{"code": BUY_CODE, "rank": 1}]}) == 1

    rec = json.loads(pool_file.read_text(encoding="utf-8").strip())
    assert "pool_ctx" not in rec


# ---------- 09:35 主入口接线（此前整条路径不进记分卡） ----------

class FakeBroker:
    def __init__(self, oid="B9001"):
        self.orders = []
        self.oid = oid

    def buy(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("buy", code, vol, price))
        return {"order_id": self.oid, "status": "submitted", "message": "提交交易成功！"}

    def sell(self, sig, date, code, vol, price=None, plan_id=None):
        self.orders.append(("sell", code, vol, price))
        return {"order_id": self.oid, "status": "submitted"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    import agent_commands
    import live_breaker
    import live_hourly_analysis as H
    import live_llm_trade as LT
    import live_prompt_context as PC

    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(LT, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(LT, "now_cn", lambda: NOW)
    monkeypatch.setattr(H, "now_cn", lambda: NOW)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(live_breaker, "check_and_trip", lambda *a, **k: (False, ""))
    monkeypatch.setattr(H, "daily_buy_codes", lambda agent, day=None: set())
    monkeypatch.setattr(H, "append_log", lambda *a, **k: None)
    monkeypatch.setattr(agent_commands, "apply_forced_sells", lambda *a, **k: None)
    monkeypatch.setattr(agent_commands, "has_halt_buy", lambda *a, **k: False)
    monkeypatch.setattr(LT, "inflight_codes", lambda side: set())
    monkeypatch.setattr(LT, "reconcile", lambda *a, **k: [])
    monkeypatch.setattr(LT, "log_line", lambda *a, **k: None)
    monkeypatch.setattr(PC, "filter_affordable", lambda pool, budget: (pool, []))
    monkeypatch.setattr(LT, "build_prompt", lambda *a, **k: "P")
    monkeypatch.setattr(LT, "pool_rows", lambda *a, **k: [])
    monkeypatch.setattr(LT, "lq_klines_batch", lambda codes, **k: {
        c: [{"close": 9.8, "volume": 1000}, {"close": 10.0, "volume": 1000}] for c in codes})
    monkeypatch.setattr(LT, "lq_klines", lambda code, **k: [
        {"close": 9.8, "volume": 1000}, {"close": 10.0, "volume": 1000}])
    monkeypatch.setattr(LT, "lq_quote", lambda *a, **k: {"close": 10.0})
    monkeypatch.setattr(LT, "PER_STOCK_PCT", 0.2)
    monkeypatch.setattr(LT, "MAX_NEW_BUYS", 3)
    monkeypatch.setattr(LT, "LEVERAGE_MAX", 1.5)

    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100_000.0,
        "positions": {BUY_CODE: {"volume": 100, "cost_price": 10.0,
                                 "buy_ts": TS, "last_ts": TS}}}}})
    broker = FakeBroker()
    monkeypatch.setattr(LT, "_query_account_with_retry", lambda: (
        broker, {"asset": {"asset": 100_000.0, "cash": 50_000.0},
                 "positions": [{"code": BUY_CODE, "total_volume": 100}]}))
    monkeypatch.setattr(LT, "holding_rows", lambda broker, positions: [
        {"code": BUY_CODE, "name": "江西铜业", "volume": 100, "cost": 10.0,
         "price": 10.0, "pnl_pct": 0.0, "day_chg": 1.0, "avail": 100}])
    monkeypatch.setattr(LT, "load_pool_meta", lambda top=20: (
        [{"code": BUY_CODE, "name": "江西铜业", "rank": 4, "score": 0.71}],
        {"direction": "—"},
        {"file": "20260912_agent_picks.json", "date": "20260912", "kind": "agent"}))
    monkeypatch.setattr(LT, "decide_with_retry", lambda prompt, agent: (
        [{"action": "buy", "code": BUY_CODE, "pct": 0.2, "reason": "加仓"}],
        "content", None, None))
    monkeypatch.setattr(LT, "wait_fill", lambda *a, **k: {
        "order_id": "B9001", "status": "submitted",
        "filled_volume": broker.orders[-1][2], "filled_price": 10.1})

    calls = []
    import decision_track as DT

    monkeypatch.setattr(DT, "ingest_decisions",
                        lambda *a, **k: (calls.append((a, k)), 0)[1])
    return SimpleNamespace(LT=LT, calls=calls, pool_file=tmp_path / "decision_pool.jsonl")


def test_0935_main_entry_feeds_scorecard_with_pool_view(env):
    """09:35 是每天第一笔真实调仓，却整条路径不进记分卡——接线后必须带池位置戳。

    用 --execute 走真实下单段（闸门/成交桩全开），断言 ingest 被调用且
    pool_view 含展示集与池文件名（P2 的分档分析全指望这两个字段）。
    """
    args = SimpleNamespace(execute=True, agents=AGENT, top=20, catch_up=False)

    assert env.LT._run(args) == 0

    assert len(env.calls) == 1
    a, k = env.calls[0]
    assert a[0] == AGENT and a[2] == NOW.isoformat()
    assert k["source"] == "live_llm_trade"
    assert k["held_codes"] == {BUY_CODE}
    view = k["pool_view"]
    assert view["file"] == "20260912_agent_picks.json"
    assert [r["code"] for r in view["shown"]] == [BUY_CODE]
    assert view["full"] and view["dropped"] == []


def test_0935_main_entry_survives_scorecard_failure(env, monkeypatch):
    """记分卡入库抛异常不得影响执行（观测层与交易主链路的失效边界）。"""
    import decision_track as DT

    def boom(*a, **k):
        raise RuntimeError("池文件锁死")

    monkeypatch.setattr(DT, "ingest_decisions", boom)
    args = SimpleNamespace(execute=True, agents=AGENT, top=20, catch_up=False)

    assert env.LT._run(args) == 0               # 只要求 rc=0 且不抛


# ---------- 接线漂移防线 ----------

def _ingest_call_sites(src: str, fname: str) -> list[tuple[str, set[str]]]:
    """源码里每处 `ingest_decisions(...)` 调用 → [(位置, 关键字实参名集)]。

    用 **ast** 而不是文本匹配（09-18 审查 MEDIUM）：注释/docstring 里提到
    `ingest_decisions(...)` 不该假红，写成 `**{"pool_view": v}` 也不该假绿。
    语法坏的文件跳过——它跑不起来，本条防线不该替编译错误背锅。
    """
    import ast

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for i, node in enumerate(ast.walk(tree), 1):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name != "ingest_decisions":
            continue
        kws = {k.arg for k in node.keywords if k.arg}
        if any(k.arg is None for k in node.keywords):    # `**kwargs` 透传：无法静态判定
            kws.add("pool_view")
        out.append((f"{fname}:{node.lineno}", kws))
    return out


def test_every_ingest_call_site_passes_pool_view():
    """**接线漂移防线**：ingest_decisions 的每个调用点都必须带 pool_view。

    09-18 实录：09:35 主入口此前压根不进记分卡——"某条路径悄悄不记"从行为上
    完全不可见，直到几周后做 P2 分析才发现样本缺了每天的第一笔调仓。整点轮
    全路径没有测试脚手架（函数依赖太多），所以这里用静态检查兜底：新增调用点
    忘了插桩 = 红灯，而不是数据缺口。

    覆盖边界（评审 LOW）：扫全仓**出货代码**（scripts/、backend/、agent_tools/…），
    排除 .venv/node_modules 与 tests/——测试**故意**要覆盖"不传 pool_view"的
    旧路径（见 test_ingest_without_pool_view_writes_no_pool_ctx），把它们算进来
    就自相矛盾了。`**kwargs` 透传形式按"已带"放行：它无法静态判定，真跑起来的
    形态由上面的行为用例覆盖。
    """
    skip = {".venv", "node_modules", "__pycache__", ".git", "tests"}
    calls = []
    for p in sorted(ROOT.rglob("*.py")):
        if skip & set(p.parts):
            continue
        calls += _ingest_call_sites(p.read_text(encoding="utf-8", errors="replace"),
                                    str(p.relative_to(ROOT)))

    assert calls, "找不到任何调用点——测试自身已失效，先修测试"
    missing = [loc for loc, kws in calls if "pool_view" not in kws]
    assert not missing, f"未插桩的 ingest_decisions 调用点：{missing}"
