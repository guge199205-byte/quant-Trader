"""实盘持仓「买入时间」推导（/api/live/account 的 buy_time 回填）。

背景（2026-09-15）：Live「持仓」tab 加日期筛选后暴露——桥账户返回的持仓
buy_time 大多为空。旧实现只认 `mode == 'execute' and result` 的行且不辨买卖
（卖出行的时间也会被记成买入），而现行成交流走 `fill` / `fill_confirm` 形状，
于是全被跳过。现改为两级（与前端「买入日未知」的展示口径配套）：

  1) 分账账本（logs/live_ledger.json）优先：仍持有该 code 的各 agent 取最早
     buy_ts——与「持仓」tab 按模型展示的买入时刻同源；
  2) 交易日志 FIFO 兜底（账本未覆盖的仓位，如人工单）：成交流按时间正序
     「买建仓、卖冲销」，取仍有剩余的最老批次 ts。

计数口径对齐 live_breaker.realized_loss_today：跳过 error/pending 行；
fill_confirm 用顶层 volume、其余用 fill.filled_volume；同一 order_id 的
execute 与 fill_confirm 双写按 tradability_audit 的去重说明只计一次。
两路都推不出的 code 不产出（账单显示为空，前端标注「买入日未知」）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def ledger_buy_times(ledger: Dict[str, Any]) -> Dict[str, str]:
    """账本 → {code: 最早 buy_ts}：遍历所有 agent 仍持有的仓位取最早。"""
    out: Dict[str, str] = {}
    for rec in (ledger.get("agents") or {}).values():
        if not isinstance(rec, dict):
            continue
        for code, pos in (rec.get("positions") or {}).items():
            if not isinstance(pos, dict):
                continue
            if int(pos.get("volume") or 0) <= 0:
                continue
            ts = str(pos.get("buy_ts") or "")
            if ts and (code not in out or ts < out[code]):
                out[code] = ts
    return out


def _order_id(rec: Dict[str, Any]) -> str:
    return str((rec.get("fill") or {}).get("order_id") or rec.get("order_id") or "")


def _volume_of(rec: Dict[str, Any]) -> int:
    """成交流归一：fill_confirm 用顶层 volume，其余用 fill.filled_volume（对齐 live_breaker）。"""
    if rec.get("mode") == "fill_confirm":
        return int(rec.get("volume") or 0)
    return int((rec.get("fill") or {}).get("filled_volume") or 0)


def fifo_buy_times(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """成交流 FIFO：每 code 买建仓、卖冲销，返回仍有剩余的最老批次 ts。

    卖大于在册批次（日志窗口外的历史买入）只冲掉已知部分，不产生负库存；
    同一 order_id 只计一次（execute 与 fill_confirm 是同一单的双写）。
    """
    lots: Dict[str, List[List[Any]]] = {}  # code -> [[ts, remaining_volume], ...]
    seen_orders: set = set()
    for rec in sorted(records, key=lambda r: str(r.get("ts") or "")):
        code = str(rec.get("code") or "")
        side = str(rec.get("side") or "").lower()
        if not code or side not in ("buy", "sell") or rec.get("error") or rec.get("pending"):
            continue
        oid = _order_id(rec)
        if oid:
            if oid in seen_orders:
                continue
            seen_orders.add(oid)
        vol = _volume_of(rec)
        ts = str(rec.get("ts") or "")
        if vol <= 0 or not ts:
            continue
        queue = lots.setdefault(code, [])
        if side == "buy":
            queue.append([ts, vol])
            continue
        left = vol
        while left > 0 and queue:
            used = min(left, queue[0][1])
            queue[0][1] -= used
            left -= used
            if queue[0][1] <= 0:
                queue.pop(0)
    return {code: q[0][0] for code, q in lots.items() if q}


def live_buy_times(ledger: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, str]:
    """合并两路：FIFO 兜底打底，账本口径（优先）覆盖。"""
    out = fifo_buy_times(records)
    out.update(ledger_buy_times(ledger))
    return out


def read_trade_records(logs_dir: Path) -> List[Dict[str, Any]]:
    """读 live_trade_*.jsonl（跳过美股变体与坏行；容错口径同 live_breaker）。"""
    records: List[Dict[str, Any]] = []
    for path in sorted(Path(logs_dir).glob("live_trade_*.jsonl")):
        if path.name.startswith("live_trade_us_"):
            continue
        try:
            # errors="replace"：中文追加流水可能截断在多字节字符中间（同 live_breaker）
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records