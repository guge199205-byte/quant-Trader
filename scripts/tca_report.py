#!/usr/bin/env python3
"""执行损耗 TCA 读数面 —— "下单这件事花了多少钱"。

数据源 = 成交流水（logs/live_trade_*.jsonl，各下单路径共用同一本流水）：
  * 接线后（2026-09-19 起）：每笔成交自带三段价（ref/limit/fill）+ 三个时刻，
    由 `exec_cost.tape_fields` 写入，与写入方向同一口径（`exec_cost.slip_bps`）；
  * 接线前：买入限价可从 stdout 受理行按**委托号**精确 join 回来
    （`✅ [agent] 买入 CODE N股 限价 ¥X …委托号 OID`）；**卖出限价当时没落盘**，
    补不出就是补不出——不用"成交价 ±1%"倒推假基准。

判读纪律（同影子账）：
  * 样本 <30 只展示不结论；
  * "不可定价"与"0 bps"分开计数——没数据不许读成没问题；
  * 成交率分母含零成交终态（只有成交笔数会把"挂十单成一单"读成 100%）；
  * 与成交同名但不是成交的行（提交回执/在途 trace）单列计数，不进样本——
    它们若被读成成交，就是"按我们自己报的限价成交"的幽灵样本（恒 +1% 滑点）。

历史重建（`--infer-ref`，默认关）：老行的基准价没落过盘，但买入限价可从受理行
按委托号精确补回 → 反推 ref=限价÷缓冲（±1~2bp，限价 round 到分）。推理样本单列 `.inf` 组，**绝不**
与实测混算；补不出限价的卖单就留在"不可定价"，不拿成交价倒推假基准。

已知边界（报告里原样印出，不藏在文档里）：
  1. 手续费/印花税不在账：桥只回报成交价量，账本无费用字段；
  2. fill_ts 是 **wait_fill 轮询观察到成交**的时刻（粒度 3s），不是交易所成交时刻；
  3. 历史卖单限价未落盘 → 卖侧滑点不可重建（接线后才有）；
  4. ref_px 是**取价时刻**的行情（下单前拉的那根 K 线现价），不是模型决策时刻的价；
     decided_ts 是**轮级**决策产出时刻（同轮各单共用），故"决策→提交"延迟是轮级粒度。

用法：
  python scripts/tca_report.py                    # 全窗口打印
  python scripts/tca_report.py --days 30 --out logs/tca_scorecard.json
  python scripts/tca_report.py --push             # 摘要推 QQ
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import exec_cost  # noqa: E402

TAPE_GLOB = "live_trade_[0-9]*.jsonl"
#: 历史限价的来源（按委托号 join）：09:35 主入口 + 整点轮
DEFAULT_LOGS = ("live_llm_trade.log", "live_hourly_analysis.log")
MIN_SAMPLE = 30

#: `✅ [agent] 买入 002709.SZ 300股 限价 ¥33.40 已受理（委托号 206163）`
_ACK_BUY = re.compile(
    r"\[(?P<agent>[^\]]+)\]\s*买入\s+(?P<code>[0-9]{6}\.[A-Z]{2})\s+(?P<vol>\d+)\s*股"
    r"\s*限价\s*¥\s*(?P<limit>[0-9.]+).*?委托号\s*(?P<oid>\d+)")
#: `✅ [agent] 卖出 600817.SH 已受理（委托号 111693）`——注意**没有限价**
_ACK_SELL = re.compile(
    r"\[(?P<agent>[^\]]+)\]\s*卖出\s+(?P<code>[0-9]{6}\.[A-Z]{2}).*?委托号\s*(?P<oid>\d+)")


def parse_ack_line(line: str) -> dict | None:
    """受理行 → {order_id, limit_px, side, code, agent, volume}；非受理行 → None。

    卖出受理行历史上不含限价 → `limit_px=None`（如实缺项，不臆造）。
    """
    if not line or "委托号" not in line:
        return None
    m = _ACK_BUY.search(line)
    if m:
        return {"order_id": m.group("oid"), "limit_px": float(m.group("limit")),
                "side": "buy", "code": m.group("code"),
                "agent": m.group("agent"), "volume": int(m.group("vol"))}
    m = _ACK_SELL.search(line)
    if m:
        return {"order_id": m.group("oid"), "limit_px": None, "side": "sell",
                "code": m.group("code"), "agent": m.group("agent"), "volume": None}
    return None


def legacy_limits(log_paths) -> dict[str, dict]:
    """stdout 日志 → {委托号: 受理记录}（只读；文件缺失/不可读跳过）。"""
    out: dict[str, dict] = {}
    for p in log_paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            got = parse_ack_line(line)
            if got and got["order_id"] not in out:
                out[got["order_id"]] = got
    return out


def merge_orders(rows: list[dict]) -> list[dict]:
    """同委托号的多笔成交合成一笔（按量加权的成交价），无号行各自保留。

    部分成交（内联记 100 股 + 对账补 200 股）是**一笔单的两次回报**：按量加权
    合成的均价才是这笔单的执行价格；当成两笔算会让小份的口径被放大。
    """
    merged: dict[str, dict] = {}
    out: list[dict] = []
    for r in rows:
        oid = str(r.get("order_id") or "")
        if not oid:
            out.append(dict(r))
            continue
        cur = merged.get(oid)
        if cur is None:
            merged[oid] = dict(r)
            continue
        v0, v1 = cur.get("filled") or 0, r.get("filled") or 0
        tot = v0 + v1
        if tot > 0:
            cur["fill_px"] = round(((cur.get("fill_px") or 0) * v0
                                    + (r.get("fill_px") or 0) * v1) / tot, 4)
        cur["filled"] = tot or None
        if cur.get("ts") and r.get("ts") and str(r["ts"]) < str(cur["ts"]):
            cur["ts"], cur["date"] = r["ts"], r.get("date") or cur.get("date")
        for k in ("ref_px", "limit_px", "decided_ts", "submit_ts"):
            if not cur.get(k) and r.get(k):
                cur[k] = r[k]
        if r.get("fill_ts"):
            cur["fill_ts"] = max(str(cur.get("fill_ts") or ""), str(r["fill_ts"]))
    return out + list(merged.values())


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            got = json.loads(line)
        except ValueError:
            continue
        if isinstance(got, dict):
            rows.append(got)
    return rows


def _day_of(path: Path) -> str:
    return path.stem.replace("live_trade_", "")


def _infer_ref(limit_px, code: str):
    """历史买入限价 → 基准价（限价 ÷ 缓冲）——**推理值**，仅供量级判断。

    知道我们报了 ¥33.40 的限价、且口径是 ref×1.01（港 1.005）→ ref≈33.069。
    它与实测基准差的是限价 round 到分 带来的 ±1~2bp 舍入，用来分辨"滑点是 1bp 还是
    100bp"够用，做精细归因不够格——启用时推理样本单列 `.inf` 组（见 collect），
    绝不与实测混算。
    """
    try:
        lim = float(limit_px)
    except (TypeError, ValueError):
        return None
    if not lim > 0:
        return None
    buf = 1.005 if str(code or "").upper().endswith(".HK") else 1.01
    return round(lim / buf, 4)


def collect(tape_dir: Path | None = None, log_paths=None, days: int = 0,
            today: str = "", infer_ref: bool = False) -> dict:
    """读成交流水（+ 历史受理行）→ 读数 dict。只读，不写任何状态。

    infer_ref（默认关）：历史行没有基准价时，按 限价÷缓冲 反推（`_infer_ref`），
    推理样本单列 `.inf` 组，与实测的 `.inf` 之外的分组分开读。
    """
    tape_dir = Path(tape_dir) if tape_dir else (ROOT / "logs")
    if log_paths is None:
        log_paths = [tape_dir / n for n in DEFAULT_LOGS]
    legacy = legacy_limits(log_paths)

    cutoff = None
    if days and days > 0:
        base = datetime.fromisoformat(today).date() if today else date.today()
        cutoff = (base - timedelta(days=int(days))).isoformat()

    rows, zero, dates = [], 0, []
    recovered_oids: set[str] = set()     # 限价补回按**委托**计（同单多行只算一笔）
    inferred = not_fill = 0              # inferred 合并后重算（内部标记 _inf）
    for f in sorted(tape_dir.glob(TAPE_GLOB)):
        day = _day_of(f)
        if not re.fullmatch(r"\d{8}", day):
            continue
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if cutoff and iso < cutoff:
            continue
        dates.append(iso)
        for rec in _read_jsonl(f):
            if str(rec.get("mode") or "") == "fill_abort":
                zero += 1
                continue
            oid = str(rec.get("order_id")
                      or ((rec.get("fill") or {}).get("order_id")
                          if isinstance(rec.get("fill"), dict) else "") or "")
            legacy_limit = (legacy.get(oid) or {}).get("limit_px")
            norm = exec_cost.normalize_row(rec, legacy_limit=legacy_limit)
            if norm is None:
                # 与成交同名但不是成交的行（提交回执/在途 trace）：单列计数——它们
                # 此前会被读成"按自己报的限价成交"的幽灵样本（见 exec_cost）
                if rec.get("code") and not rec.get("error") \
                        and exec_cost.is_submit_trace(rec):
                    not_fill += 1
                continue
            if legacy_limit is not None and rec.get("limit_px") is None:
                recovered_oids.add(oid or f"@{iso}:{len(recovered_oids)}")
            if infer_ref and norm["ref_px"] is None and norm["limit_px"] is not None:
                got = _infer_ref(norm["limit_px"], norm["code"])
                if got:
                    norm["ref_px"] = got
                    norm["path"] = f"{norm['path']}.inf"
                    norm["_inf"] = True     # 内部标记：计数按合并后的委托
            rows.append(norm)

    merged = merge_orders(rows)
    inferred = sum(1 for r in merged if r.pop("_inf", False))   # 计数后抹掉内部标记
    sample = exec_cost.summarize(merged, zero_fill_orders=zero)
    return {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": sorted(set(dates)),
        "n_rows": len(rows),
        "n_orders": len(merged),
        "n_limit_recovered": len(recovered_oids),
        "n_inferred_ref": inferred,
        "n_not_fill": not_fill,
        "n_priced": sample["n"],
        "n_unpriced": sample["n_unpriced"],
        "n_zero_fill": zero,
        "sample": sample,
        "rows": merged,
    }


def _fmt(v, unit: str = "", nd: int = 2) -> str:
    return "—" if v is None else f"{v:,.{nd}f}{unit}"


def render(rep: dict) -> str:
    """人读报告（含判读纪律与已知边界）。"""
    L = [f"📉 执行损耗 TCA（{str(rep.get('generated') or '')[:10]}）"]
    win = rep.get("window") or []
    win_s = f"{win[0]} ~ {win[-1]}" if win else "无"
    L.append(f"窗口 {win_s} · 成交行 {rep['n_rows']} · 合并后委托 {rep['n_orders']} 笔 · "
             f"可定价 {rep['n_priced']} · 不可定价 {rep['n_unpriced']} · "
             f"限价从日志补回(笔) {rep['n_limit_recovered']} · 零成交终态 {rep['n_zero_fill']}"
             + (f" · 非成交行(回执/在途) {rep['n_not_fill']}" if rep.get("n_not_fill") else ""))
    if rep.get("n_inferred_ref"):
        L.append(f"⚠️ 其中 {rep['n_inferred_ref']} 笔基准价为**推理值**（限价÷缓冲，±1~2bp）"
                 f"——单列 .inf 组，只做量级判断，不与实测混算")

    s = rep.get("sample") or {}
    if not s.get("n"):
        L.append("按方向：**无样本**（接线前的老行没有基准价；09-19 接线后新成交才有）")
    else:
        L.append("按方向/路径（**正 = 比基准差**；加权按成交额）：")
        L.append(f"  {'分组':<22}{'n':>4}{'加权':>10}{'中位':>10}{'p10':>9}{'p90':>9}")
        for k, g in (s.get("groups") or {}).items():
            if not g.get("n"):
                continue
            L.append(f"  {k:<22}{g['n']:>4}{_fmt(g['slip_bps_w']):>10}"
                     f"{_fmt(g['slip_bps_med']):>10}{_fmt(g['slip_bps_p10']):>9}"
                     f"{_fmt(g['slip_bps_p90']):>9}")
        L.append(f"  合计 加权 {_fmt(s.get('slip_bps_w'))} bps · 中位 {_fmt(s.get('slip_bps_med'))} bps"
                 f" · 成交额 ¥{_fmt(s.get('notional'), '', 0)}")
    if s.get("fill_rate") is not None:
        L.append(f"成交率 {s['fill_rate'] * 100:.1f}%（{rep['n_orders']}/{s['n_orders']} 笔；"
                 f"分母含零成交终态）")
    if s.get("decide_to_submit_min_med") is not None:
        L.append(f"延迟：决策→提交 中位 {s['decide_to_submit_min_med']:.1f} 分钟（轮级）· "
                 f"提交→确认成交 中位 {_fmt(s.get('submit_to_fill_min_med'))} 分钟（轮询粒度 3s）")

    max_n = max([g.get("n") or 0 for g in (s.get("groups") or {}).values()] or [0])
    L.append(f"判读：每组样本 <{MIN_SAMPLE} 只展示不做结论"
             + (f"（当前最大 n={max_n}）" if s.get("n") else "")
             + "；正号=比基准差；不可定价与 0 bps 是两件事")
    L.append("已知边界：①手续费/印花税**不在账**（桥只回报价量）；②fill_ts 是轮询"
             "**观察到**成交的时刻（粒度 3s），不是交易所成交时刻；③**卖出限价历史未落盘**"
             "（受理行只印「已受理」）→ 历史卖单滑点不可重建；④ref_px 是取价时刻行情、"
             "decided_ts 是轮级决策基准，决策→取价的那段延迟不在本读数内。")
    return "\n".join(L)


def push_line(rep: dict) -> str:
    """QQ 摘要一行（没有可读数字时如实说没有）。"""
    s = rep.get("sample") or {}
    head = (f"执行损耗 TCA {rep['n_orders']} 笔委托 · 可定价 {rep['n_priced']}"
            f" · 零成交 {rep['n_zero_fill']}")
    if rep.get("n_inferred_ref"):
        head += f"（含推理基准 {rep['n_inferred_ref']}）"   # 不许把推理值当实测报
    if not s.get("n"):
        return head + "（接线后样本累积中，暂无可读数字）"
    return head + (f"：加权 {_fmt(s.get('slip_bps_w'))} bps"
                   f" · 成交率 {(s.get('fill_rate') or 0) * 100:.0f}%"
                   f"（n={s['n']}，样本 <{MIN_SAMPLE} 仅供参考）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="执行损耗 TCA 读数面")
    ap.add_argument("--tape", default="", help="成交流水目录（默认 logs/）")
    ap.add_argument("--logs", default="", help="历史受理行日志（逗号分隔，默认两条主日志）")
    ap.add_argument("--days", type=int, default=0, help="窗口天数（0=全部）")
    ap.add_argument("--out", default="", help="把读数写成 JSON（原子写）")
    ap.add_argument("--push", action="store_true", help="摘要推 QQ")
    ap.add_argument("--infer-ref", action="store_true",
                    help="历史行按 限价÷缓冲 反推基准价（推理值，单列 .inf 组）")
    args = ap.parse_args(argv)

    logs = [p.strip() for p in args.logs.split(",") if p.strip()] or None
    rep = collect(Path(args.tape) if args.tape else None, logs, args.days,
                  infer_ref=args.infer_ref)
    print(render(rep))
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)          # 原子替换：读到一半的 JSON 比没有更坏
        print(f"✅ 已写入 {p}")
    if args.push:
        from push_notify import notify

        notify("执行损耗 TCA", push_line(rep))
        print("📤 已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
