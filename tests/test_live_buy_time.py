"""实盘持仓「买入时间」回填（/api/live/account 的 buy_time）。

2026-09-15「持仓」tab 的日期筛选暴露：桥账户返回的持仓 buy_time 大多为空——
旧实现只认 `mode == 'execute' and result` 的行、且不辨买卖（卖出行也会被当成
买入时刻），而现行成交流走 `fill` / `fill_confirm` 形状 → 全被跳过。改为两级：

  1) 分账账本（logs/live_ledger.json）优先：仍持有该 code 的各 agent 取最早
     buy_ts——与「持仓」tab 按模型展示的买入时刻同源（record_buy 建仓时落）；
  2) 交易日志 FIFO 兜底（账本未覆盖的仓位）：成交流按时间正序「买建仓、卖冲销」，
     取仍有剩余的最老批次 ts。

计数口径对齐 live_breaker.realized_loss_today：跳过 error/pending 行、
fill_confirm 用顶层 volume、其余用 fill.filled_volume；同一 order_id 的
execute 与 fill_confirm 双写按 tradability_audit 的去重说明只计一次。
推不出的 code 不出现在结果里（前端按「买入日未知」展示，不参与日期筛选）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_buy_time.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import live_buy_time as BT  # noqa: E402


def _ledger(agents: dict) -> dict:
    return {"version": 1, "agents": {a: {"positions": pos} for a, pos in agents.items()}}


def _fill(ts: str, code: str, side: str, vol: int, oid: str = "") -> dict:
    fill = {"filled_volume": vol}
    if oid:
        fill["order_id"] = oid
    return {"ts": ts, "code": code, "side": side, "fill": fill}


class TestLedgerBuyTimes:
    def test_takes_earliest_buy_ts_across_agents(self):
        led = _ledger({
            "pro": {"002202.SZ": {"volume": 200, "buy_ts": "2026-09-14T11:08:50.1"}},
            "glm": {"002202.SZ": {"volume": 300, "buy_ts": "2026-09-14T11:11:27.8"}},
        })
        assert BT.ledger_buy_times(led) == {"002202.SZ": "2026-09-14T11:08:50.1"}

    def test_ignores_zero_volume_and_missing_ts(self):
        led = _ledger({
            "a": {
                "X": {"volume": 0, "buy_ts": "2026-09-11T09:37:26"},  # 已清仓残留
                "Y": {"volume": 100},  # 无 buy_ts
            },
            "b": {"Z": {"volume": 100, "buy_ts": "2026-09-11T09:37:26"}},
        })
        assert BT.ledger_buy_times(led) == {"Z": "2026-09-11T09:37:26"}

    def test_empty_or_malformed_ledger(self):
        assert BT.ledger_buy_times({}) == {}
        assert BT.ledger_buy_times({"agents": {"a": {"positions": {"X": "坏形状"}}}}) == {}


class TestFifoBuyTimes:
    def test_consumes_old_lots_and_reports_oldest_remaining(self):
        recs = [
            _fill("2026-09-11T09:37:26", "A", "buy", 700),
            _fill("2026-09-14T10:23:02", "A", "sell", 700),
            _fill("2026-09-14T11:07:27", "A", "buy", 100),
        ]
        assert BT.fifo_buy_times(recs) == {"A": "2026-09-14T11:07:27"}

    def test_partial_sell_keeps_original_lot_ts(self):
        recs = [
            _fill("2026-09-11T09:00:00", "A", "buy", 500),
            _fill("2026-09-14T09:00:00", "A", "buy", 300),
            _fill("2026-09-15T09:00:00", "A", "sell", 500),
        ]
        assert BT.fifo_buy_times(recs) == {"A": "2026-09-14T09:00:00"}

    def test_full_sell_leaves_no_entry(self):
        recs = [
            _fill("2026-09-11T09:00:00", "A", "buy", 300),
            _fill("2026-09-15T09:00:00", "A", "sell", 300),
        ]
        assert BT.fifo_buy_times(recs) == {}

    def test_skips_pending_error_and_unfilled(self):
        recs = [
            # 挂单未成（pending）不算成交
            {"ts": "2026-09-14T11:08:50", "code": "A", "side": "buy", "volume": 600,
             "pending": True, "result": {"status": "submitted"}},
            # 拒单行不算
            {"ts": "2026-09-14T11:20:00", "code": "B", "side": "buy", "error": "桥拒单"},
            # 有 result 但无成交流字段 → 无成交量
            {"ts": "2026-09-14T11:30:00", "code": "C", "side": "buy",
             "result": {"status": "submitted"}},
        ]
        assert BT.fifo_buy_times(recs) == {}

    def test_fill_confirm_counts_but_same_order_is_deduped(self):
        recs = [
            _fill("2026-09-14T11:08:50", "A", "buy", 300, oid="249618"),
            # 同一单的成交回报补记（tradability_audit：execute 与 fill_confirm 双写）→ 不重复计
            {"ts": "2026-09-14T11:09:01", "mode": "fill_confirm", "code": "A",
             "side": "buy", "volume": 300, "order_id": "249618"},
            _fill("2026-09-15T10:29:37", "A", "sell", 300, oid="171531"),
        ]
        assert BT.fifo_buy_times(recs) == {}

    def test_fill_confirm_without_execute_line_counts(self):
        recs = [
            {"ts": "2026-09-14T11:09:01", "mode": "fill_confirm", "code": "A",
             "side": "buy", "volume": 300, "order_id": "249618"},
        ]
        assert BT.fifo_buy_times(recs) == {"A": "2026-09-14T11:09:01"}

    def test_sell_without_inventory_is_noop(self):
        recs = [
            _fill("2026-09-15T09:00:00", "A", "sell", 300),  # 日志窗口外的买入
            _fill("2026-09-15T10:00:00", "A", "buy", 100),
        ]
        assert BT.fifo_buy_times(recs) == {"A": "2026-09-15T10:00:00"}


class TestPrecedence:
    def test_ledger_wins_over_fifo_and_fifo_fills_gaps(self):
        led = _ledger({"pro": {"A": {"volume": 100, "buy_ts": "2026-09-14T11:08:50"}}})
        recs = [
            _fill("2026-09-10T09:00:00", "A", "buy", 100),   # 账本有 A → 以账本为准
            _fill("2026-09-12T09:00:00", "B", "buy", 50),    # 账本无 B → FIFO 兜底
        ]
        assert BT.live_buy_times(led, recs) == {
            "A": "2026-09-14T11:08:50",
            "B": "2026-09-12T09:00:00",
        }


class TestReadTradeRecords:
    def test_reads_live_trade_files_skips_us_and_bad_lines(self, tmp_path):
        (tmp_path / "live_trade_20260914.jsonl").write_text(
            '{"ts":"2026-09-14T11:08:50","code":"A","side":"buy","fill":{"filled_volume":300}}\n'
            'not json\n'
            '\n',
            encoding="utf-8",
        )
        (tmp_path / "live_trade_us_20260901.jsonl").write_text(
            '{"ts":"2026-09-01T22:00:00","code":"AAPL","side":"buy","fill":{"filled_volume":10}}\n',
            encoding="utf-8",
        )
        recs = BT.read_trade_records(tmp_path)
        assert [r["code"] for r in recs] == ["A"]