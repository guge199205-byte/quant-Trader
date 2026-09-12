"""提示词持仓成本改分账账本口径（2026-09-12 P1-1）。

桥 account 的 cost_price 是券商**共享账户混合成本**（历史仓位、别的来源都算在
同一个账户里），与账本（本策略自己的加权平均买入成本，加仓加权、减仓不动余仓）
不是一回事。把桥成本原样喂给 LLM，口径差会被当成盈亏——glm 曾把实际浮亏 −2.8%
讲成 +83% 并据此决策。

修法（计划书 P1-1）：**只在提示词展示层**把成本/浮盈换成账本口径并加口径说明；
波动触发基线 / compute_forced_trims / 执行路径 holdings 消费的 `build_rows` 行
（桥口径）一律不动。测试钉住：
  - 桥成本 ≠ 账本成本 → 提示词含账本成本、浮盈按账本算；
  - 账本无该票 → 回退桥值并显式标注（`*` + 说明句）；
  - helper 不修改入参（判定字段保持桥口径的机制）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_ledger_cost_display.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import live_hourly_analysis as H      # noqa: E402
import live_ledger as L               # noqa: E402
import live_llm_trade as T            # noqa: E402
import live_prompt_context as PC      # noqa: E402

AGENT = "deepseek-v4-pro"
CODE = "001312.SZ"
PRICE = 10.0
BRIDGE_COST = 100.0     # 券商共享账户混合成本 → 桥口径 −90%
LEDGER_COST = 8.5       # 本策略账本成本 → 实际 +17.65%
VOLUME = 1000


def _hourly_row(code=CODE, name="福恩股份"):
    return {"code": code, "name": name, "price": PRICE, "cost": BRIDGE_COST,
            "volume": VOLUME, "pnl": round((PRICE - BRIDGE_COST) * VOLUME, 2),
            "pnl_pct": -90.0, "day_chg": 0.0, "avail": VOLUME}


def _trade_holding():
    """live_llm_trade.holding_rows 的行形状（无 pnl 键）。"""
    r = _hourly_row()
    del r["pnl"]
    return r


def _positions(cost=LEDGER_COST):
    return {CODE: {"volume": VOLUME, "cost_price": cost,
                   "buy_ts": "2026-09-01T09:35:00+08:00",
                   "last_ts": "2026-09-01T09:35:00+08:00"}}


def _ledger_doc(positions=None):
    return {"agents": {AGENT: {"virtual_cash": 50000.0,
                               "positions": positions if positions is not None else _positions()}}}


# ---------- helper：展示副本重写 ----------

def test_display_row_uses_ledger_cost_and_recomputes_pnl():
    (row,) = PC.ledger_cost_rows([_hourly_row()], _positions())
    assert row["cost"] == LEDGER_COST
    assert row["pnl_pct"] == pytest.approx(17.65)
    assert row["pnl"] == pytest.approx((PRICE - LEDGER_COST) * VOLUME)
    assert row["cost_src"] == "ledger"


def test_input_rows_keep_bridge_basis_for_judgement():
    """判定字段（pnl_pct 等）保持桥口径的机制：helper 返回新 dict，入参不变。"""
    src = _hourly_row()
    out = PC.ledger_cost_rows([src], _positions())
    assert out[0] is not src
    assert src["cost"] == BRIDGE_COST and src["pnl_pct"] == -90.0
    assert "cost_src" not in src


def test_missing_ledger_position_falls_back_to_bridge_and_marks_source():
    (row,) = PC.ledger_cost_rows([_hourly_row()], {})
    assert row["cost"] == BRIDGE_COST and row["pnl_pct"] == -90.0
    assert row["cost_src"] == "bridge"


def test_ledger_row_without_cost_price_falls_back():
    """账本有该票但成本 0/坏值（历史脏数据）→ 同样回退桥值，不许除零。"""
    (row,) = PC.ledger_cost_rows([_hourly_row()], {CODE: {"volume": VOLUME, "cost_price": 0}})
    assert row["cost"] == BRIDGE_COST and row["cost_src"] == "bridge"


def test_broken_positions_types_fall_back():
    """账本结构坏掉（positions 非 dict / 条目非 dict）→ 回退桥值，不许炸穿提示词。"""
    for bad in (["not-a-dict"], {CODE: "oops"}):
        (row,) = PC.ledger_cost_rows([_hourly_row()], bad)
        assert row["cost"] == BRIDGE_COST and row["cost_src"] == "bridge"


def test_non_finite_ledger_cost_falls_back_to_bridge():
    """成本列非有限数（Inf/NaN，手改账本或写入侧脏数据）→ 回退桥值。

    旧实现 `lc > 0` 对 inf 为真 → 成本列渲染 ¥inf、盈亏 `(price-inf)/inf` 渲染
    +nan%，模型照着 nan% 决策（2026-09-12 审查 LOW）。"""
    for bad in (float("inf"), float("-inf"), float("nan")):
        (row,) = PC.ledger_cost_rows([_hourly_row()], {CODE: {"volume": VOLUME,
                                                              "cost_price": bad}})
        assert row["cost"] == BRIDGE_COST and row["pnl_pct"] == -90.0, bad
        assert row["cost_src"] == "bridge"


def test_trade_row_without_pnl_key_is_safe():
    out = PC.ledger_cost_rows([_trade_holding()], _positions())
    assert out[0]["cost"] == LEDGER_COST
    assert out[0]["pnl_pct"] == pytest.approx(17.65)
    assert "pnl" not in out[0]


def test_degraded_summary_uses_ledger_basis():
    """LLM 降级摘要与提示词表同口径（2026-09-12 审查 LOW）：同一轮对话流里
    提示词按账本、摘要按桥混合成本 = 前后自相矛盾。"""
    text = PC.degraded_summary("极限", [_hourly_row()], _positions())
    assert "¥8.5" in text and "+17.65%" in text and "-90.00%" not in text
    assert "分账账本" in text

    # 账本无该票 → 桥值 + `*` 标注，说明句里注明含义
    text2 = PC.degraded_summary("极限", [_hourly_row()], {})
    assert "¥100.0*" in text2 and "回退桥值" in text2


# ---------- 提示词接线：整点轮 ----------

def test_hourly_prompt_shows_ledger_cost(monkeypatch):
    monkeypatch.setattr(L, "load_ledger", lambda: _ledger_doc())
    text = H.build_user_content([_hourly_row()], 10000.0, 5000.0, AGENT)

    assert "| 8.5 |" in text                    # 账本成本进提示词
    assert "17.65%" in text                     # 浮盈按账本成本算
    assert "-90.00%" not in text                # 桥口径浮亏不再出现
    assert "口径说明" in text                    # 口径说明句已在
    assert "分账账本" in text


def test_hourly_prompt_marks_bridge_fallback_row(monkeypatch):
    """账本无该票 → 回退桥值 + `*` 标注（该行盈亏仍是桥口径，说明句里注明仅供参照）。"""
    monkeypatch.setattr(L, "load_ledger", lambda: _ledger_doc(positions={}))
    text = H.build_user_content([_hourly_row()], 10000.0, 5000.0, AGENT)

    assert "| 100.0* |" in text and "-90.00%" in text
    assert "口径说明" in text and "勿据此算盈亏" in text


def test_hourly_prompt_ledger_unavailable_keeps_all_bridge(monkeypatch):
    """账本整个读不到（启动即抛）→ 全部回退桥值 + 标注，绝不炸穿提示词。"""
    def _boom():
        raise RuntimeError("ledger broken")

    monkeypatch.setattr(L, "load_ledger", _boom)
    text = H.build_user_content([_hourly_row()], 10000.0, 5000.0, AGENT)
    assert "| 100.0* |" in text and "口径说明" in text


# ---------- 提示词接线：09:35 调仓轮 ----------

def test_llm_trade_prompt_shows_ledger_cost(monkeypatch):
    monkeypatch.setattr(T, "load_ledger", lambda: _ledger_doc())
    text = T.build_prompt(AGENT, [_trade_holding()], [], {}, 100000.0)

    assert "| 8.5 |" in text
    assert "+17.65%" in text
    assert "-90.00%" not in text
    assert "口径说明" in text


def test_llm_trade_prompt_marks_bridge_fallback(monkeypatch):
    monkeypatch.setattr(T, "load_ledger", lambda: _ledger_doc(positions={}))
    text = T.build_prompt(AGENT, [_trade_holding()], [], {}, 100000.0)
    assert "| 100.0* |" in text
    assert "口径说明" in text


def test_llm_trade_prompt_header_states_ledger_basis(monkeypatch):
    """表头措辞必须与表体口径一致（2026-09-12 审查 MEDIUM）：盈亏% 列由账本成本
    重算，仍说「现价/盈亏%/可卖量 = 桥账户实时口径」会自相矛盾（下一行就是
    LEDGER_COST_NOTE 的「该列盈亏/% 均按此成本计算」）。"""
    monkeypatch.setattr(T, "load_ledger", lambda: _ledger_doc())
    text = T.build_prompt(AGENT, [_trade_holding()], [], {}, 100000.0)

    head = text.split("【你名下的现有持仓】")[1].split("\n")[0]
    assert "盈亏%" in head and "账本口径" in head
    assert "现价/盈亏%" not in head          # 旧措辞：把盈亏% 也说成桥口径
