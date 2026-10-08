"""P1 影子 A/B（live_llm_trade --shadow-context）：只记录、绝不下单。

实验设计（2026-09-18）：同一输入快照下 A1/A2 同提示词重跑两次（温度 0.3 的
采样噪声基线）+ B 为处理臂（现状提示词 + 盘面状态 + 新闻分子）。判据是
effect=diff(A1,B) 是否显著高于 noise=diff(A1,A2)，而不是"A/B 有差异就上线"。

本文件覆盖：
  * build_prompt 的 extra_context 插入位置（空值时零扰动）；
  * --shadow-context 与 --execute/--catch-up 互斥、非交易日闸；
  * _run_shadow 端到端：写记录、不写状态/不写对话流/不下单、B 臂确实含块；
  * 时段窗口 guard 与 --shadow-force 冒烟通道。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_shadow_context_ab.py -q
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
NOW = datetime(2026, 9, 18, 9, 40, 0, tzinfo=CN)   # 周五盘中（交易日）
TS = NOW.isoformat()
BLOCKS = "【盘面状态（系统判定）】TESTBLOCK-盘面\n【新闻分子（主编 09:25）】TESTBLOCK-新闻"


# ---------- build_prompt 的 extra_context 插入 ----------

#: 逐字节金标 = 2026-09-18 从 git HEAD（重构前）的 live_llm_trade.build_prompt 生成：
#: 冻结 now_cn/load_ledger、档位默认 0.2/3、空持仓空池。任何"空 extra_context
#: 时的输出漂移"（多/少空行、块被挪动）都会被这条钉住（评审 MEDIUM：此前的
#: 同路径自比较是恒真断言，钉不住任何东西）。
GOLDEN_PROMPT = '现在是北京时间 2026-09-18 09:35:00（开盘后）。你是 deepseek-v4-pro 的 A股实盘调仓决策模型，管理 ¥100,000 虚拟额度（已用 ¥0，剩余 ¥100,000）。\n\n【你名下的现有持仓】（成本与盈亏% = 你名下分账账本口径；现价/数量/可卖量 = 桥账户实时口径，可卖量 0 = 今日买入 T+1 不可卖）：\n\n| 代码 | 名称 | 数量 | 成本 | 现价 | 盈亏% | 今日涨跌% | 可卖量 |\n|------|------|------|------|------|-------|-----------|--------|\n\n口径说明：**成本列 = 你名下分账账本**（你自己的加权平均买入成本，加仓加权、减仓不动余仓成本），该列盈亏/% 均按此成本计算；数量与可卖量 = 桥账户实时口径，执行（可卖量/手数）一律以桥为准。两本账在成交回报到账前可能短暂不一致（账本数量滞后于桥），属正常对账窗口。成本列带 `*` = 账本暂无该票记录、暂用券商账户混合成本（可能含历史仓位，仅供参照，勿据此算盈亏）。\n\n【今日大盘方向】（最新研究产出）：\n偏多\n\n\n【决策规则】\n1. 逐只现有持仓判断：hold（继续持有）/ sell（减仓或清仓换股）。如果现有持股趋势/基本面仍优于候选池，可以全部 hold 不换股。\n2. 需要买入时从候选池选：优先分数高、行业顺大盘方向的；允许换仓（同轮先 sell 再 buy），以信号分数+板块主线+新闻分子综合权衡，不必拘泥原有持仓。\n3. sell 的 pct = 卖出可卖量的比例（0~1）；buy 的 pct = 使用剩余额度的比例（每票 ≤20%，当日新开仓 ≤3 只；超出的会被闸门裁掉）。候选池已按你的资金量剔除买不起的标的（单票预算 = 剩余额度×20%；最小一手 100 股、科创板 200 股都超预算的票不会出现在表里），表里没有的代码不要报买入。\n4. T+1：可卖量 0 的持仓不能卖。ST/*ST/退市整理股与黑名单标的**不可买入**（闸门硬拦）。\n5. 输出**严格 JSON**（不要 markdown 代码块、不要额外文字），格式：\n{"decisions": [{"action": "hold|sell|buy", "code": "600519.SH", "pct": 0.2, "reason": "一句话理由"}]}'


def test_build_prompt_empty_extra_context_matches_head_golden(monkeypatch):
    """空 extra_context（默认值）与重构前（HEAD）输出**逐字节一致**。

    金标生成于 HEAD worktree——那里没有未入库的 data/risk_block.json /
    data/industry_risk.json，两个数据依赖块为空；这里打桩同一状态，
    钉住的是**提示词框架**（结构/措辞/顺序），不是当天的风控清单。"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    import live_prompt_context as PC
    import live_llm_trade as LT

    bj = _tz(_td(hours=8))
    monkeypatch.setattr(LT, "now_cn", lambda: _dt(2026, 9, 18, 9, 35, 0, tzinfo=bj))
    monkeypatch.setattr(LT, "load_ledger", lambda: {"version": 1, "agents": {}})
    monkeypatch.setattr(LT, "PER_STOCK_PCT", 0.2)   # 金标生成时的档位（隔离线上预算）
    monkeypatch.setattr(LT, "MAX_NEW_BUYS", 3)
    monkeypatch.setattr(PC, "risk_warning_block", lambda *a, **k: "")
    monkeypatch.setattr(PC, "industry_caution_block", lambda *a, **k: "")

    out = LT.build_prompt("deepseek-v4-pro", [], [], {"direction": "偏多"}, 100000.0)

    assert out == GOLDEN_PROMPT


def test_build_prompt_extra_context_inserts_after_direction(monkeypatch):
    """上下文块插在【今日大盘方向】之后、【候选池】之前（与整点轮的注入次序同构）。"""
    import live_llm_trade as LT

    rows = ["| 1 | 600362.SH | 江西铜业 | 有色 | 1.0 | 1.0 | 备注 |"]
    pool = [{"code": "600362.SH", "name": "江西铜业"}]

    text = LT.build_prompt(AGENT, [], rows, {"direction": "偏多"}, 100000.0,
                           pool=pool, extra_context=BLOCKS)

    i_dir = text.index("【今日大盘方向】")
    i_blk = text.index("TESTBLOCK-盘面")
    i_pool = text.index("【候选池】")
    assert i_dir < i_blk < i_pool


# ---------- main() 的互斥与交易日闸 ----------

def test_main_refuses_shadow_with_execute(monkeypatch, capsys):
    import live_llm_trade as LT

    monkeypatch.setattr(sys, "argv",
                        ["live_llm_trade.py", "--shadow-context", "--execute"])
    assert LT.main() == 2
    assert "--execute" in capsys.readouterr().out


def test_main_refuses_shadow_with_catch_up(monkeypatch, capsys):
    import live_llm_trade as LT

    monkeypatch.setattr(sys, "argv",
                        ["live_llm_trade.py", "--shadow-context", "--catch-up"])
    assert LT.main() == 2
    assert "catch-up" in capsys.readouterr().out


def test_main_refuses_orphan_shadow_force(monkeypatch, capsys):
    """--shadow-force 单独使用曾经是静默 no-op（评审 LOW）：显式拒绝，防误以为
    在冒烟影子、实际走了实盘路径。"""
    import live_llm_trade as LT

    monkeypatch.setattr(sys, "argv", ["live_llm_trade.py", "--shadow-force"])
    assert LT.main() == 2
    assert "--shadow-context" in capsys.readouterr().out


def test_main_shadow_blocked_on_non_trading_day(monkeypatch, capsys):
    """周六（2026-09-19）: 交易日历闸先拦（影子也不该在非交易日采数）。"""
    import live_llm_trade as LT

    sat = datetime(2026, 9, 19, 9, 40, 0, tzinfo=CN)
    monkeypatch.setattr(LT, "now_cn", lambda: sat)
    monkeypatch.setattr(sys, "argv", ["live_llm_trade.py", "--shadow-context"])

    assert LT.main() == 0
    assert "非交易日" in capsys.readouterr().out


# ---------- _run_shadow 端到端 ----------

class FakeBroker:
    def __init__(self):
        self.orders = []

    def buy(self, *a, **k):
        self.orders.append(("buy", a))
        return {"order_id": "X", "status": "submitted"}

    def sell(self, *a, **k):
        self.orders.append(("sell", a))
        return {"order_id": "X", "status": "submitted"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    import live_hourly_analysis as H
    import live_llm_trade as LT
    import live_prompt_context as PC

    monkeypatch.setattr(L, "LEDGER_FILE", tmp_path / "ledger.json")
    L.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {BUY_CODE: {"volume": 100, "cost_price": 10.0,
                                 "buy_ts": TS, "last_ts": TS}}}}})

    monkeypatch.setattr(LT, "now_cn", lambda: NOW)
    monkeypatch.setattr(H, "now_cn", lambda: NOW)
    monkeypatch.setattr(LT, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(LT, "SHADOW_DIR", tmp_path / "shadow")
    monkeypatch.setattr(LT, "SHADOW_LOCK", tmp_path / "shadow.lock")
    monkeypatch.setattr(LT, "LOCK_FILE", tmp_path / "live.lock")   # 主入口锁探测用
    monkeypatch.setattr(PC, "filter_affordable", lambda pool, budget: (pool, []))
    monkeypatch.setattr(LT, "pool_rows", lambda *a, **k: [])
    monkeypatch.setattr(LT, "load_pool_meta", lambda top=20: (
        [{"code": BUY_CODE, "name": "江西铜业"}], {"direction": "—"}, {}))
    monkeypatch.setattr(LT, "build_daily_context_blocks", lambda broker: (
        BLOCKS, {"market_state": True, "news_present": True, "news_age_min": 12.4}))

    broker = FakeBroker()
    monkeypatch.setattr(LT, "_query_account_with_retry", lambda: (
        broker, {"asset": {"asset": 100_000.0, "cash": 50_000.0},
                 "positions": [{"code": BUY_CODE, "total_volume": 100}]}))
    monkeypatch.setattr(LT, "holding_rows", lambda b, positions: [
        {"code": BUY_CODE, "name": "江西铜业", "volume": 100, "cost": 10.0,
         "price": 10.0, "pnl_pct": 0.0, "day_chg": 1.0, "avail": 100}])

    calls: list[str] = []

    def fake_decide(prompt, agent):
        calls.append(prompt)
        n = len(calls)
        decision = {"action": "hold", "code": BUY_CODE, "pct": 0.0,
                    "reason": f"call{n}"}
        return [decision], "content", {"total_tokens": 10 + n}, ""

    monkeypatch.setattr(LT, "decide_with_retry", fake_decide)
    return SimpleNamespace(LT=LT, H=H, broker=broker, calls=calls, tmp=tmp_path)


def _args(**kw):
    base = dict(shadow_context=True, shadow_force=False, execute=False,
                catch_up=False, agents=AGENT, top=20)
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_shadow_records_three_arms_and_never_touches_live_state(env, capsys):
    rc = env.LT._run_shadow(_args())

    assert rc == 0
    assert env.broker.orders == []                     # 绝不下单
    assert not (env.tmp / "state.json").exists()       # 不写执行状态（告警判据不受污染）

    rec_file = env.tmp / "shadow" / f"{NOW.date().isoformat()}.jsonl"
    rec = json.loads(rec_file.read_text(encoding="utf-8").strip())
    assert rec["mode"] == "shadow_context_ab" and rec["forced"] is False
    assert rec["market_state"] is True and rec["news_present"] is True
    arm = rec["agents"][AGENT]
    # 三个调用臂：A1/A2 同提示词、B 含上下文块
    assert len(env.calls) == 3
    pa, pa2, pb = env.calls
    assert pa == pa2 and "TESTBLOCK" not in pa
    assert "TESTBLOCK-盘面" in pb and "TESTBLOCK-新闻" in pb
    assert arm["prompt_len_b"] > arm["prompt_len_a"]
    assert arm["a1"]["decisions"][0]["reason"] == "call1"
    assert arm["b"]["decisions"][0]["reason"] == "call3"
    assert arm["a2"]["usage"]["total_tokens"] == 12


def test_run_shadow_window_guard_and_force(env, monkeypatch, capsys):
    """盘后默认不采（数据洁癖）；--shadow-force 仅手动冒烟且记录带 forced。"""
    import live_llm_trade as LT

    night = datetime(2026, 9, 18, 20, 0, tzinfo=CN)
    monkeypatch.setattr(LT, "now_cn", lambda: night)

    assert LT._run_shadow(_args()) == 0
    assert not (env.tmp / "shadow").exists()
    assert "不在影子采样窗口" in capsys.readouterr().out

    assert LT._run_shadow(_args(shadow_force=True)) == 0
    rec = json.loads((env.tmp / "shadow" / "2026-09-18.jsonl")
                     .read_text(encoding="utf-8").strip())
    assert rec["forced"] is True


def test_run_shadow_no_pool_returns_early(env, monkeypatch):
    import live_llm_trade as LT

    monkeypatch.setattr(LT, "load_pool_meta", lambda top=20: ([], {}, {}))
    assert LT._run_shadow(_args()) == 1


def test_run_shadow_skips_when_live_round_still_running(env, capsys):
    """主入口仍持锁（实录可到 10:21）→ 本档跳过，等 cron 重试（评审 HIGH-2）。"""
    import fcntl

    import live_llm_trade as LT

    fh = open(LT.LOCK_FILE, "a+")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert LT._run_shadow(_args()) == 0
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()

    assert env.calls == [] and not (env.tmp / "shadow").exists()
    assert "主入口仍在跑" in capsys.readouterr().out


def test_run_shadow_daily_idempotent(env, capsys):
    """当日已有**正式**记录 → 重试档不再采（cron */5 的幂等靠它，评审 H-2）。"""
    d = env.tmp / "shadow"
    d.mkdir()
    (d / f"{NOW.date().isoformat()}.jsonl").write_text(
        json.dumps({"date": NOW.date().isoformat(), "ts": "2026-09-18T10:02:00+08:00",
                    "forced": False, "agents": {}}) + "\n", encoding="utf-8")

    assert env.LT._run_shadow(_args()) == 0
    assert env.calls == []                       # 不再花钱调 LLM
    assert "当日已有影子记录" in capsys.readouterr().out


def test_news_age_min_tolerates_non_dict_json(env, monkeypatch, tmp_path):
    """顶层非 dict 的 latest.json → None，不抛 AttributeError（评审 M-1：
    旧版会把整次采样带走、已付的 LLM 费全丢）。"""
    import live_llm_trade as LT

    monkeypatch.setattr(LT, "ROOT", tmp_path)
    nb = tmp_path / "data" / "news_brief"
    nb.mkdir(parents=True)
    (nb / "latest.json").write_text('["not-a-dict"]', encoding="utf-8")

    assert LT._news_age_min() is None

    (nb / "latest.json").write_text(
        json.dumps({"ts": "2026-09-18T09:00:00+08:00"}), encoding="utf-8")
    assert LT._news_age_min() == 40.0            # NOW=09:40 → 40 分钟
