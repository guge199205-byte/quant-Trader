"""执行损耗报告（读数面）的单元测试。

读数面的纪律与影子账一致：**样本不足只展示不结论**、**不可定价的单独计数**、
**去重按委托号**（同一笔单的内联成交 + 对账补记是同一件事，不能算两笔）。

历史数据的现实（2026-09-19 实测，决定本报告的形状）：
  * 买入限价可从 stdout 受理行恢复（`✅ [agent] 买入 CODE N股 限价 ¥X …委托号 OID`），
    与成交流水的 `fill.order_id` 精确 join；
  * **卖出限价历史上没有落盘**（受理行只印"卖出 CODE 已受理"）——补不出就是补不出，
    报告必须写明，不许用"成交价 ±1%"倒推一个假的基准；
  * 手续费不在账（桥只回报价量）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import tca_report  # noqa: E402

TAPE_DAY = "2026-09-17"
ACK = ("2026-09-17 09:41:11  ✅ [deepseek-v4-flash] 买入 002709.SZ 300股 "
       "限价 ¥33.40 已受理（委托号 206163）: {'order_id': '206163'}")
ACK_SELL = "2026-09-15 09:41:11  ✅ [deepseek-v4-flash] 卖出 600817.SH 已受理（委托号 111693）: {}"


def _write_tape(tmp: Path, rows: list[dict]) -> Path:
    d = tmp / "logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"live_trade_{TAPE_DAY.replace('-', '')}.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def _buy_fill(**kw):
    row = {"ts": "2026-09-17T09:41:14+08:00", "mode": "execute", "agent": "deepseek-v4-flash",
           "code": "002709.SZ", "side": "buy", "volume": 300, "price": 32.99,
           "remaining": 0, "ref_px": 33.069, "limit_px": 33.40, "tca_path": "llm_trade",
           "fill": {"order_id": "206163", "filled_price": 32.99, "filled_volume": 300}}
    row.update(kw)
    return row


# ---------------------------------------------------------------- 解析受理行

def test_parse_ack_line_recovers_buy_limit():
    got = tca_report.parse_ack_line(ACK)
    assert got == {"order_id": "206163", "limit_px": 33.40, "side": "buy",
                   "code": "002709.SZ", "agent": "deepseek-v4-flash", "volume": 300}


def test_parse_ack_line_admits_sell_limit_is_absent():
    """卖出受理行没有限价：如实返回 limit_px=None（**不许**按成交价倒推一个）。"""
    got = tca_report.parse_ack_line(ACK_SELL)
    assert got is not None and got["side"] == "sell" and got["limit_px"] is None
    assert got["order_id"] == "111693"


def test_parse_ack_line_ignores_non_ack_lines():
    assert tca_report.parse_ack_line("  ⏭️ [a] 买入 X: 虚拟现金不足") is None
    assert tca_report.parse_ack_line("") is None


# ---------------------------------------------------------------- 合并与去重

def test_merge_partial_fills_of_same_order_weight_by_volume():
    """同委托号的内联成交 + 对账补记 = 同一笔单：按量加权合成一个样本。

    分部成交（100@10.0 + 200@10.3）合成 300@10.2，而不是算成两笔平均值 10.15——
    后者会把小单的口径放大。
    """
    rows = [
        {"order_id": "o1", "side": "buy", "fill_px": 10.0, "filled": 100, "ref_px": 10.0,
         "limit_px": 10.1, "ts": "2026-09-17T09:40:00+08:00", "path": "llm_trade"},
        {"order_id": "o1", "side": "buy", "fill_px": 10.3, "filled": 200, "ref_px": 10.0,
         "limit_px": 10.1, "ts": "2026-09-17T09:45:00+08:00", "path": "llm_trade"},
    ]
    merged = tca_report.merge_orders(rows)
    assert len(merged) == 1
    assert merged[0]["filled"] == 300
    assert abs(merged[0]["fill_px"] - 10.2) < 1e-9
    assert merged[0]["ts"] == "2026-09-17T09:40:00+08:00"    # 取首次


def test_merge_keeps_rows_without_order_id_separate():
    """对不上委托号的成交（桥没给号）不能互相合并——那是不同的单，只是都没号。"""
    rows = [{"order_id": "", "side": "buy", "fill_px": 10.0, "filled": 100, "ts": "t1"},
            {"order_id": "", "side": "buy", "fill_px": 9.0, "filled": 100, "ts": "t2"}]
    assert len(tca_report.merge_orders(rows)) == 2


# ---------------------------------------------------------------- 汇总

def test_collect_joins_legacy_limit_and_keeps_unpriced_honest(tmp_path):
    """历史买入：限价从日志补上、基准不臆造；无基准的行走"不可定价"计数。"""
    tape = _write_tape(tmp_path, [
        _buy_fill(limit_px=None, ref_px=None, price=32.99),          # 老行：无三段价
        _buy_fill(fill={"order_id": "208888", "filled_volume": 100}, volume=100,
                  price=50.0, limit_px=None, ref_px=None, code="603678.SH"),
    ])
    log = tmp_path / "live_llm_trade.log"
    log.write_text("\n".join([
        ACK,
        ("2026-09-17 10:02:03  ✅ [deepseek-v4-flash] 买入 603678.SH 100股 "
         "限价 ¥50.82 已受理（委托号 208888）: {}"),
    ]) + "\n", encoding="utf-8")

    rep = tca_report.collect(tape_dir=tape, log_paths=[log], days=0)
    assert rep["n_rows"] == 2
    assert rep["n_limit_recovered"] == 2          # 两条限价都从日志补回
    assert rep["n_priced"] == 0                   # 基准补不出 → 不可定价
    assert rep["sample"]["n"] == 0 and rep["sample"]["n_unpriced"] == 2


def test_collect_prices_rows_that_have_all_three(tmp_path):
    """接好线之后的新行（三段价齐全）直接可定价，不依赖日志。"""
    tape = _write_tape(tmp_path, [_buy_fill()])
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=0)
    assert rep["n_priced"] == 1
    s = rep["sample"]
    assert s["n"] == 1 and s["slip_bps_w"] == -23.89      # (32.99-33.069)/33.069 bps
    assert s["fill_rate"] == 1.0


def test_collect_counts_zero_fill_terminals_in_fill_rate(tmp_path):
    """零成交终态（废单）进成交率分母：只统计成交笔数会把"挂了十单成一单"读成 100%。"""
    tape = _write_tape(tmp_path, [
        _buy_fill(),
        {"ts": "2026-09-17T16:08:24+08:00", "mode": "fill_abort", "agent": "glm-5.3-flash",
         "code": "600176.SH", "side": "sell", "volume": 0, "filled": 0, "remaining": 200,
         "status": "rejected", "price": 44.57},
    ])
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=0)
    assert rep["n_zero_fill"] == 1
    assert abs(rep["sample"]["fill_rate"] - 0.5) < 1e-9


def test_collect_excludes_submit_traces_and_counts_them(tmp_path):
    """提交回执行（mode=execute + result 回执，价写的是限价）不是成交。

    08-31 实录：执行路径提交时刻写 `{price: 限价, volume: 委托量, result: {...}}`。
    被读成成交 = "按我们自己报的限价成交"的幽灵样本（恒 +1% 缓冲滑点）。
    """
    tape = _write_tape(tmp_path, [
        {"ts": "2026-08-31T11:13:51+08:00", "mode": "execute", "agent": "a1",
         "code": "600183.SH", "volume": 1200, "price": 151.38,
         "result": {"order_id": "196557", "status": "submitted"}},
    ])
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=0)
    assert rep["n_rows"] == 0 and rep["n_not_fill"] == 1
    assert rep["sample"]["n"] == 0


def test_collect_infer_ref_labels_inferred_samples_separately(tmp_path):
    """历史基准价由 限价÷缓冲 反推：单列 .inf 组，不与实测混算（默认关）。"""
    tape = _write_tape(tmp_path, [_buy_fill(limit_px=None, ref_px=None, price=33.40)])
    log = tmp_path / "live_llm_trade.log"
    log.write_text(ACK + "\n", encoding="utf-8")

    off = tca_report.collect(tape_dir=tape, log_paths=[log], days=0)
    assert off["n_priced"] == 0 and off["n_inferred_ref"] == 0

    on = tca_report.collect(tape_dir=tape, log_paths=[log], days=0, infer_ref=True)
    assert on["n_inferred_ref"] == 1 and on["n_priced"] == 1
    row = on["rows"][0]
    assert abs(row["ref_px"] - 33.40 / 1.01) < 1e-3
    assert row["path"].endswith(".inf")            # 推理样本单列分组
    assert "推理" in tca_report.render(on)


def test_collect_respects_days_window(tmp_path):
    """窗口按成交流水日期过滤（老行不参与）。"""
    tape = _write_tape(tmp_path, [_buy_fill()])
    old = tape / "live_trade_20260101.jsonl"
    old.write_text(json.dumps(_buy_fill(), ensure_ascii=False) + "\n", encoding="utf-8")
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=30,
                             today="2026-09-19")
    assert rep["n_rows"] == 1 and rep["window"] == ["2026-09-17"]


def test_render_marks_small_samples_as_no_conclusion(tmp_path):
    """样本 <30：只展示不结论（每条统计后面带 n），并写明已知边界。"""
    tape = _write_tape(tmp_path, [_buy_fill()])
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=0)
    text = tca_report.render(rep)
    assert "n=1" in text
    assert "不做结论" in text or "只展示" in text
    for kw in ("手续费", "轮询", "卖出限价", "基准"):
        assert kw in text, f"报告的已知边界缺了「{kw}」"


def test_render_is_safe_on_empty_tape(tmp_path):
    """没有数据也要出报告（印"无样本"），不许崩、不许印 0.00 bps 冒充读数。"""
    tape = tmp_path / "logs"
    tape.mkdir()
    rep = tca_report.collect(tape_dir=tape, log_paths=[], days=0)
    text = tca_report.render(rep)
    assert "无样本" in text
    assert "0.00 bps" not in text
