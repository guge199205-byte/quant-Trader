"""每日分析预算（2026-09-12 P1-2）：轮次硬兜底。

2026-09-11 实录：状态被采样进程抹掉 → 20 分钟节流/60 分钟冷却失效 → 一天
**78 轮完整分析**（≈每 3 分钟一轮）、三 agent 约 215 万 tokens。P0-1 修了根因
（单写者 + 原子写），本闸是第二道：每 agent 当日轮次计数（走 update_state，
跨进程锁内读-改-写，采样进程抹不掉），达到 `DAILY_ROUND_LIMIT` 后**唤醒触发轮**
（波动/板块/L2/新闻唤醒统一走「波动触发: 」前缀）跳过 + 记事件；整点轮、尾盘轮、
断线补跑、手动一律不受限——兜底不漏事。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_round_budget.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_fills as F                # noqa: E402
import live_hourly_analysis as H      # noqa: E402
import live_ledger as L               # noqa: E402

AGENT = "deepseek-v4-pro"
OTHER = "glm-5.3-flash"
CODE = "001312.SZ"
VOLUME = 1000
PRICE = 10.0


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    """状态与锁都落 tmp（与 test_live_analysis_state 同法）。"""
    p = tmp_path / "live_analysis_state.json"
    monkeypatch.setattr(H, "STATE_PATH", p)
    monkeypatch.setattr(H, "STATE_LOCK_PATH", p.with_name(p.name + ".lock"))
    return p


def _today() -> str:
    return H.now_cn().strftime("%Y-%m-%d")


# ---------- 纯函数：唤醒判据与预算闸 ----------

def test_wakeup_reason_detection():
    assert H.is_wakeup_reason("波动触发: 板块联动：银行 +3.0pp")
    assert not H.is_wakeup_reason("每小时定时")
    assert not H.is_wakeup_reason("断线恢复补跑")
    assert not H.is_wakeup_reason("手动触发")


def test_budget_gate_only_blocks_wakeup_rounds():
    today = _today()
    over = {"rounds_date": {AGENT: today}, "rounds_today": {AGENT: H.DAILY_ROUND_LIMIT}}

    assert H.wakeup_over_budget(over, AGENT, today, "波动触发: 个股涨跌")
    assert not H.wakeup_over_budget(over, AGENT, today, "每小时定时")
    assert not H.wakeup_over_budget(over, AGENT, today, "断线恢复补跑")
    assert not H.wakeup_over_budget(over, AGENT, today, "手动触发")

    under = {"rounds_date": {AGENT: today}, "rounds_today": {AGENT: H.DAILY_ROUND_LIMIT - 1}}
    assert not H.wakeup_over_budget(under, AGENT, today, "波动触发: 个股涨跌")

    # 跨日：昨日的计数（哪怕 999）不算今天
    stale = {"rounds_date": {AGENT: "2026-09-11"}, "rounds_today": {AGENT: 999}}
    assert not H.wakeup_over_budget(stale, AGENT, today, "波动触发: 个股涨跌")
    # 不株连其他 agent
    assert not H.wakeup_over_budget(over, OTHER, today, "波动触发: 个股涨跌")
    # 状态整份缺失（首次运行）不炸
    assert not H.wakeup_over_budget({}, AGENT, today, "波动触发: 个股涨跌")


def test_counter_increments_and_persists(state):
    today = _today()
    assert H.bump_round_counter(AGENT, today) == 1
    assert H.bump_round_counter(AGENT, today) == 2
    st = json.loads(state.read_text(encoding="utf-8"))
    assert st["rounds_today"][AGENT] == 2 and st["rounds_date"][AGENT] == today
    assert H.rounds_today_of(H.load_state(), AGENT, today) == 2


def test_counter_is_per_agent(state):
    today = _today()
    H.bump_round_counter(AGENT, today)
    assert H.bump_round_counter(OTHER, today) == 1
    st = H.load_state()
    assert H.rounds_today_of(st, AGENT, today) == 1
    assert H.rounds_today_of(st, OTHER, today) == 1


def test_counter_resets_on_new_day(state):
    H.update_state({"rounds_date": {AGENT: "2026-09-11"}, "rounds_today": {AGENT: 77}})
    assert H.rounds_today_of(H.load_state(), AGENT, "2026-09-12") == 0
    assert H.bump_round_counter(AGENT, "2026-09-12") == 1


# ---------- 接线：run_analysis 的跳过/放行 ----------

class _Broker:
    """最小桥桩：账户有一条持仓（够 run_analysis 走到 per-agent 分析段）。"""

    def _account_query(self):
        return {"asset": {"asset": 60000.0, "cash": 20000.0},
                "positions": [{"stock_code": CODE, "total_volume": VOLUME,
                               "available_volume": VOLUME, "cost_price": 9.0,
                               "last_price": PRICE}]}

    def get_quote(self, code, _=""):
        return {"close": PRICE}

    def get_klines(self, code, interval="daily"):
        return [{"close": PRICE}, {"close": PRICE}]


@pytest.fixture
def quiet(monkeypatch):
    """把分析段的外围依赖全部静音：本测试只关心预算闸与计数器。

    rotated_modes → []：agent 进入分析段但一轮 LLM 都不调用（预算闸在它之前），
    所以既不需要 LLM 桩、也证明「闸门在开销发生前生效」。
    """
    import prompts.analysis_modes as AM

    monkeypatch.setattr(AM, "rotated_modes", lambda agent: [])
    monkeypatch.setattr(H, "record_equity", lambda *a, **k: None)
    monkeypatch.setattr(H, "market_data_stale", lambda broker: False)
    monkeypatch.setattr(H, "build_trade_recap", lambda agent: "")
    monkeypatch.setattr(H, "load_review_recap", lambda agent: "")
    monkeypatch.setattr(H, "load_orderbook", lambda broker, codes: {})
    monkeypatch.setattr(L, "load_ledger", lambda: {"agents": {AGENT: {
        "virtual_cash": 50000.0,
        "positions": {CODE: {"volume": VOLUME, "cost_price": 9.0,
                             "buy_ts": "2026-09-01T09:35:00+08:00"}}}}})


def _events() -> list:
    if not F.EVENTS_FILE.is_file():
        return []
    doc = json.loads(F.EVENTS_FILE.read_text(encoding="utf-8"))
    return list(doc.values())


def test_wakeup_round_over_budget_is_skipped_with_event(state, quiet, capsys):
    today = _today()
    H.update_state({"rounds_date": {AGENT: today},
                    "rounds_today": {AGENT: H.DAILY_ROUND_LIMIT}})

    H.run_analysis(_Broker(), "波动触发: 板块联动：银行 +3.0pp",
                   dry_run=True, agents=[AGENT])

    st = H.load_state()
    assert st["rounds_today"][AGENT] == H.DAILY_ROUND_LIMIT   # 跳过的轮次不自增
    assert "预算" in capsys.readouterr().out
    hits = [e for e in _events() if e["kind"] == "round_budget_skip"]
    assert len(hits) == 1 and AGENT in hits[0]["msg"]
    assert hits[0]["alert"] is True                            # 静默跳过不许无声


def test_scheduled_and_recovery_rounds_not_limited_by_budget(state, quiet):
    """整点轮/补跑在超预算时照跑（兜底不漏事），计数继续累加。"""
    today = _today()
    H.update_state({"rounds_date": {AGENT: today},
                    "rounds_today": {AGENT: H.DAILY_ROUND_LIMIT}})

    H.run_analysis(_Broker(), "每小时定时", dry_run=True, agents=[AGENT])
    assert H.load_state()["rounds_today"][AGENT] == H.DAILY_ROUND_LIMIT + 1
    assert not [e for e in _events() if e["kind"] == "round_budget_skip"]

    H.run_analysis(_Broker(), "断线恢复补跑", dry_run=True, agents=[AGENT])
    assert H.load_state()["rounds_today"][AGENT] == H.DAILY_ROUND_LIMIT + 2


def test_normal_wakeup_round_below_budget_counts_one_round(state, quiet):
    today = _today()
    H.run_analysis(_Broker(), "波动触发: 个股涨跌", dry_run=True, agents=[AGENT])
    assert H.load_state()["rounds_today"][AGENT] == 1


def test_skipped_wakeup_round_has_no_side_effects(state, quiet, monkeypatch):
    """被跳过的唤醒轮必须完全无副作用：不加载候选池、不留「池已注入」记忆。

    闸必须在 agent 块**最前**（池加载/pool_sig 写入之前）——否则空仓 agent 的
    pool_sig 先于实际注入落盘，下一轮把首次注入错判成重复注入（紧凑摘要），
    而那一轮 LLM 其实什么都没看到。"""
    import live_llm_trade as T

    calls = []
    monkeypatch.setattr(T, "load_pool", lambda top: (
        calls.append(top), ([{"code": CODE, "name": "X", "score": 9.0}], "震荡"))[1])
    monkeypatch.setattr(L, "load_ledger", lambda: {"agents": {AGENT: {
        "virtual_cash": 50000.0, "positions": {}}}})
    today = _today()
    H.update_state({"rounds_date": {AGENT: today},
                    "rounds_today": {AGENT: H.DAILY_ROUND_LIMIT}})

    H.run_analysis(_Broker(), "波动触发: 个股涨跌", dry_run=True, agents=[AGENT])

    st = H.load_state()
    assert calls == []                                     # 池加载也在闸后
    assert st.get("last_pool_sig") in (None, {})           # 无「已注入」记忆
    assert st.get("last_pool_sig_day") in (None, {})
    assert H.rounds_today_of(st, AGENT, today) == H.DAILY_ROUND_LIMIT
