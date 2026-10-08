"""历史回填解析器（scripts/ghost_backfill_logs.py）。

回填是**生产回测的取材环节**：它把日志里已经发生过的闸门事件翻译成影子账行。
取材翻译错一步，后面所有"这条闸花了多少钱"都是错的，而且错得看不出来
（少解析一只票 = 少一个样本；日期串了 = 远期收益对错窗口）。所以这里钉的是
**格式契约**：两种日志格式各一条真实样例，加上"解析不出就别记"的底线。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_ghost_backfill.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules as GR  # noqa: E402
import ghost_backfill_logs as B  # noqa: E402

# 样例逐字取自 logs/（2026-09-14 / 2026-09-17 实况），不要"整理格式"
LLM_LINE = ("  💸 [deepseek-v4-flash] 单票预算 ¥10,776，候选池剔除 1 只买不起的："
            "300750.SZ宁德时代(最小100股 ¥34,021)")
HOURLY_LINE = ("[2026-09-14 10:19:03] 💸 glm-5.3-flash 单票预算 ¥13,548，"
               "候选池剔除 2 只买不起的：300502.SZ新易盛(最小100股 ¥42,604)、"
               "688256.SH寒武纪(最小200股 ¥225,968)")
LLM_HEAD = "🔴 实盘执行  2026-09-14 09:35:01"
LLM_HEAD_DRY = "🟡 DRY-RUN 决策演练（不下单）  2026-09-15 09:35:01"


# ---------- 单行解析 ----------

def test_parse_llm_format_single_item():
    got = B.parse_drop_line(LLM_LINE, "2026-09-14", "2026-09-14T11:05:01+08:00")

    assert got["agent"] == "deepseek-v4-flash" and got["budget"] == 10776
    assert got["items"] == [{"code": "300750.SZ", "name": "宁德时代",
                             "min_qty": 100, "min_cost": 34021}]


def test_parse_hourly_format_multi_item_and_bare_agent():
    got = B.parse_drop_line(HOURLY_LINE, "2026-09-14", "2026-09-14T10:19:03+08:00")

    assert got["agent"] == "glm-5.3-flash"
    assert [i["code"] for i in got["items"]] == ["300502.SZ", "688256.SH"]
    assert got["items"][1]["min_qty"] == 200 and got["items"][1]["min_cost"] == 225968


def test_parse_rejects_lines_without_agent_or_code():
    """解析不出 agent/代码 → None（宁可少记，不记来源不明的行）。"""
    assert B.parse_drop_line("  💸 单票预算 ¥10,000，候选池剔除 0 只", "2026-09-14", "t") is None
    assert B.parse_drop_line("  💸 [a] 单票预算 ¥10,000，候选池剔除 0 只：", "2026-09-14", "t") is None
    assert B.parse_drop_line(LLM_LINE, "", "") is None           # 没有日期 = 进不了统计


def test_parse_keeps_thousands_separator_intact():
    line = LLM_LINE.replace("¥34,021", "¥1,234,567")
    got = B.parse_drop_line(line, "2026-09-14", "t")

    assert got["items"][0]["min_cost"] == 1234567                 # 千分位不是小数点


# ---------- 整文件：日期靠轮头 / 行前缀 ----------

def test_llm_log_rows_use_round_header_date_and_dry_marker():
    text = "\n".join([LLM_HEAD, LLM_LINE, LLM_HEAD_DRY, LLM_LINE])

    rows = B.rows_from_llm_log(text)

    assert [r["date"] for r in rows] == ["2026-09-14", "2026-09-15"]
    assert rows[0]["source"] == "backfill:live_llm_trade.log"
    assert rows[1]["source"].endswith(".dry")      # 演练轮不混进实盘读法


def test_llm_log_ignores_lines_before_any_header():
    """没有轮头就没有日期——宁可不记，也不给行安一个猜出来的日期。"""
    rows = B.rows_from_llm_log("随便一行\n" + LLM_LINE)

    assert rows == []


def test_hourly_rows_take_ts_from_own_prefix():
    rows = B.rows_from_hourly_log("无关行\n" + HOURLY_LINE)

    assert len(rows) == 2
    assert all(r["ts"] == "2026-09-14T10:19:03+08:00" for r in rows)
    assert {r["rule"] for r in rows} == {GR.BUDGET_UNAFFORDABLE}
    assert {r["kind"] for r in rows} == {GR.KIND_UNSEEN}
    assert rows[1]["n_dropped"] == 2      # 同轮剔了几只：extra 展平进行（不是嵌套字段）


def test_rows_carry_ref_px_from_min_lot():
    """ref_px = 最小一手成本/股数：事后算"当时什么价"不用再翻行情。"""
    rows = B.rows_from_hourly_log(HOURLY_LINE)

    assert rows[1]["ref_px"] == 225968 / 200


# ---------- 落盘幂等 ----------

def test_backfill_is_idempotent_across_rounds(tmp_path, monkeypatch):
    """同一天多轮重复剔同一只票 = 同一件事（幂等键里的规则/日期/标的相同）。"""
    ghost = tmp_path / "ghost.jsonl"
    monkeypatch.setattr(B.ghost_ledger, "GHOST", ghost)
    text = "\n".join([LLM_HEAD, LLM_LINE, LLM_HEAD, LLM_LINE, LLM_HEAD, LLM_LINE])
    rows = B.rows_from_llm_log(text)

    assert len(rows) == 3 and B.ghost_ledger.record(rows) == 1    # 3 条事件 → 1 条记录
    assert B.ghost_ledger.record(B.rows_from_llm_log(text)) == 0  # 再跑一遍：不加行


# ---------- 意图无落地（决策池 × 成交流水） ----------

def _pool(tmp_path, rows) -> Path:
    p = tmp_path / "pool.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def test_noexec_matches_order_evidence_by_order_id(tmp_path):
    """只有委托号才算落地；同日同票同 agent 有委托号 → 不算未落地。"""
    trade = tmp_path / "live_trade_20260917.jsonl"
    trade.write_text(json.dumps({"agent": "a", "code": "600000.SH",
                                 "fill": {"order_id": "195024"}}), encoding="utf-8")
    pool = _pool(tmp_path, [{"action": "buy", "agent": "a", "code": "600000.SH",
                             "date": "2026-09-17", "ts": "2026-09-17T09:35:01+08:00"}])

    rows, st = B.rows_no_execution(pool, tmp_path)

    assert rows == [] and st["matched"] == 1 and st["noexec"] == 0


def test_noexec_flags_intent_without_order_number(tmp_path):
    """有流水但当天该票没有委托号（桥报错/被拒）→ 未落地，kind=unknown。"""
    (tmp_path / "live_trade_20260917.jsonl").write_text(
        json.dumps({"agent": "a", "code": "600000.SH", "error": "rejected"}), encoding="utf-8")
    pool = _pool(tmp_path, [{"action": "buy", "agent": "a", "code": "600000.SH",
                             "date": "2026-09-17", "ts": "2026-09-17T09:35:01+08:00"}])

    rows, st = B.rows_no_execution(pool, tmp_path)

    assert st["noexec"] == 1 and rows[0]["rule"] == GR.UNKNOWN_NO_EXEC
    assert rows[0]["kind"] == GR.KIND_UNKNOWN


def test_noexec_counts_days_without_flow_as_uncomparable(tmp_path):
    """成交流水缺失的日期**不可比**：那是"日志没留"，不是"订单丢了"。

    把不可比算成未落地会凭空造出一批假事件，而且是看起来最"严重"的那种。
    """
    pool = _pool(tmp_path, [{"action": "buy", "agent": "a", "code": "600000.SH",
                             "date": "2026-09-07", "ts": "2026-09-07T09:35:01+08:00"}])

    rows, st = B.rows_no_execution(pool, tmp_path)

    assert rows == [] and st["uncomparable"] == 1
    assert st["uncomparable_days"] == ["2026-09-07"]


def test_noexec_ignores_non_buy_intents(tmp_path):
    pool = _pool(tmp_path, [{"action": "watch", "agent": "a", "code": "600000.SH",
                             "date": "2026-09-17"}])

    rows, st = B.rows_no_execution(pool, tmp_path)

    assert rows == [] and st["n_intents"] == 0
