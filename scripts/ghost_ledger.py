#!/usr/bin/env python3
"""影子代价账 v1 —— 把**被拦下的**决策落池，事后用同一套远期收益引擎定价。

解决什么问题：风控闸门只留下了"拦掉了什么"的痕迹（stdout 里一句中文），
没有任何数据能回答"拦对了吗"。日复一日加闸 = 单向棘轮：每条规则都有存在的
理由，但没有任何一条会因**代价大于收益**而下线。影子账给每条规则记一本账：
如果当时放行，后来是涨是跌？涨了=这条闸在花钱，跌了=它在省钱。

与决策池（decision_track）的关系——**同一引擎、两个池**：
  * schema 完全一致（多三个字段：rule / kind / ref_px），因此
    `decision_track.update_forward(path=GHOST)` 直接可用，远期收益口径
    （T+1 开盘入场、一字板剔除、超额=个股−全市场等权）**不存在第二套实现**；
  * 写入原语共用 `decision_track.append_rows`（"先整体序列化再开文件"的纪律）；
  * 幂等键不同：决策池按 (agent|日|标的|动作)，影子账**多带 rule**——
    同一天同一只票被"现金不足"和"集中度"分别拦下是两件事，合并记就丢了一条
    规则的成本。同规则同日同标的重复触发只记一次（多轮反复拦同一只票不是
    多个独立样本，重复计数会把 n 撑成假的）。

事件三类（kind，读法相反，见 gate_rules.kind_of）：
  * veto   模型明确想买、被规则拦下 → 正超额 = 该规则的成本
  * unseen 候选在进入模型视野前被剔除（买不起）→ "假设模型会选它"的弱反事实
  * unknown 历史回填，原因已不可考 → 只读"落地率"

姿态（不可违反）：
  1. **纯观测**。record/record_event 任何异常都吞掉返回 0/False，绝不抛进
     交易主链路（与决策记分卡同一姿态）。
  2. **fail-open 不 fail-silent**：写失败往 stderr 留一行，不静默。
  3. 本模块不判定任何交易；读数面在 scripts/ghost_report.py。

用法：
  python scripts/ghost_ledger.py --update     # 滚动回填远期收益（复用决策池引擎）
  python scripts/ghost_ledger.py --check      # 登记表健康：未登记规则 / 复核过期
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules  # noqa: E402
from decision_track import append_rows, entry_dt_of, load_pool  # noqa: E402

GHOST = ROOT / "logs" / "ghost_ledger.jsonl"
REGISTRY = ROOT / "configs" / "gate_registry.json"
REASON_MAX = 200          # 与决策池同口径：理由留痕够复盘即可，不进统计

#: 骨架字段（extra 不得改写；见 row()）
_CORE = frozenset({
    "id", "agent", "date", "ts", "action", "kind", "code", "name", "rule", "pct",
    "ref_px", "reason", "source", "entry_dt", "entry_px", "tradable", "tags",
    "fwd", "updated",
})


def make_id(agent: str, day: str, code: str, rule: str) -> str:
    """幂等键：agent|日|标的|规则（**含 rule**，见模块头）。"""
    return hashlib.sha1(f"{agent}|{day}|{code}|{rule}".encode()).hexdigest()[:16]


def _num(v):
    """有限浮点或 None（脏值不进统计口径；bool 不算数字）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def row(agent: str, code: str, rule: str, day: str = "", ts: str = "", name: str = "",
        pct=None, reason: str = "", ref_px=None, source: str = "", action: str = "buy",
        extra: dict | None = None) -> dict | None:
    """构造一条影子账记录（纯函数；字段不自洽返回 None，由调用方丢弃）。

    entry_dt 取不到（day 非日期）时留 None 而不是丢弃整条：事件本身有价值
    （"这条规则今天动了"），只是它进不了收益统计——报告会把未回填单列一档，
    不让"样本不够"看起来像"没发生过"。
    """
    agent, code, rule = str(agent or "").strip(), str(code or "").strip(), str(rule or "").strip()
    if not (agent and code and rule):
        return None
    ts = str(ts or "")
    day = str(day or "")[:10] or ts[:10]
    if not day:
        return None
    try:
        entry_dt = entry_dt_of(day)
    except (ValueError, TypeError, OverflowError):   # 非交易日格式/脏 day
        entry_dt = None
    r = {
        "id": make_id(agent, day, code, rule), "agent": agent, "date": day, "ts": ts,
        "action": str(action or "buy"), "kind": gate_rules.kind_of(rule), "code": code,
        "name": str(name or ""), "rule": rule, "pct": _num(pct), "ref_px": _num(ref_px),
        "reason": str(reason or "")[:REASON_MAX], "source": str(source or ""),
        "entry_dt": entry_dt, "entry_px": None, "tradable": None, "tags": [],
        "fwd": {}, "updated": None,
    }
    if isinstance(extra, dict):     # 业务附注（min_cost/n_shown 等），不许改写骨架
        for k, v in extra.items():
            key = str(k)
            if key not in _CORE:
                r[key] = v
    return r


def load_ghost(path: Path | None = None) -> list[dict]:
    """读影子账（与决策池同一读取实现，含坏行容错）。"""
    return load_pool(Path(path or GHOST))


def record(rows: list, path: Path | None = None) -> int:
    """批量落账（幂等）。返回新增条数；**任何异常都吞掉返回 0**，绝不抛。

    调用点在两条买入路径的热路径上：观测层的故障不得让一笔真实调仓失败。
    失败不是静默的——stderr 留一行（观测面失效必须看得见）。
    """
    p = Path(path or GHOST)
    try:
        clean = [r for r in rows if isinstance(r, dict)
                 and r.get("id") and r.get("rule") and r.get("code")]
        if not clean:
            return 0
        existing = {e.get("id") for e in load_pool(p)}
        fresh, seen = [], set()
        for r in clean:
            if r["id"] in existing or r["id"] in seen:
                continue
            seen.add(r["id"])
            fresh.append(r)
        if not fresh:
            return 0
        append_rows(fresh, p)
        return len(fresh)
    except Exception as exc:  # noqa: BLE001  观测层故障绝不反噬交易链路
        print(f"⚠️ 影子账写入失败（不影响交易）：{type(exc).__name__}: {str(exc)[:120]}",
              file=sys.stderr)
        return 0


def record_event(agent: str, code: str, rule: str, *, day: str = "", ts: str = "",
                 path: Path | None = None, **kw) -> bool:
    """单条便捷入口（调用点只写一行）。返回是否**新增**（重复=false，失败=false）。

    调用点一律用它而不是自己拼 row()+record()：热路径上的调用要短到不会因为
    "太麻烦"而被新加的闸门跳过（接线漂移的根因多半是调用成本高）。
    """
    try:
        r = row(agent, code, rule, day=day, ts=ts, **kw)
        return bool(r) and record([r], path) == 1
    except Exception as exc:  # noqa: BLE001  同上：绝不反噬主链路
        print(f"⚠️ 影子账记录失败（不影响交易）：{type(exc).__name__}: {str(exc)[:120]}",
              file=sys.stderr)
        return False


def veto(agent: str, code: str, rule: str, now, reason: str = "", pct=None,
         ref_px=None, source: str = "", name: str = "", extra: dict | None = None,
         path: Path | None = None) -> bool:
    """热路径调用点入口（签名短到"加一行不嫌麻烦"）。now 用**本轮的时钟**。

    参数取现在的钟（`now_cn()` 一次读、轮内传到底）而不是各调用点自己再读一次：
    同一轮里两个时刻会让影子账的事件时间戳彼此错位（同 9f40630 的教训）。
    """
    return record_event(agent, code, rule, ts=getattr(now, "isoformat", lambda: "")()
                        if now is not None else "", path=path, reason=reason, pct=pct,
                        ref_px=ref_px, source=source, name=name, extra=extra)


# ---------------------------------------------------------------- 登记表

def load_registry(path: Path | None = None) -> dict:
    """读规则登记表；文件缺失/写坏 → {"rules": {}}（fail-open 不抛）。

    写坏读空 = 报告里所有规则都成了"未登记"，是显式可见的降级；而抛异常会
    掀翻读数面。登记表本身写坏还有 test_config_integrity 兜底。
    """
    try:
        doc = json.loads(Path(path or REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rules": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), dict):
        return {"rules": {}}
    return doc


def expired_rules(today: str | None = None, registry: dict | None = None,
                  path: Path | None = None) -> list[tuple[str, str]]:
    """复核日已过的规则 [(rule, review_by)]——规则默认会过期，不默认正确。"""
    reg = registry if isinstance(registry, dict) else load_registry(path)
    today = str(today or date.today().isoformat())[:10]
    out = []
    for rule, ent in sorted((reg.get("rules") or {}).items()):
        rb = str((ent or {}).get("review_by") or "")[:10]
        try:
            d = date.fromisoformat(rb)
        except ValueError:
            continue            # 坏日期由 config_integrity 管，读数面不炸
        if d.isoformat() < today:
            out.append((rule, rb))
    return out


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影子代价账（记录层）")
    ap.add_argument("--path", default="", help="影子账路径（默认 logs/ghost_ledger.jsonl）")
    ap.add_argument("--update", action="store_true", help="滚动回填远期收益")
    ap.add_argument("--check", action="store_true", help="登记表健康检查")
    args = ap.parse_args(argv)
    path = Path(args.path) if args.path else None

    if args.update:
        from decision_track import update_forward

        p = Path(path or GHOST)
        if not p.is_file():
            print(f"⚠️ 影子账还不存在：{p}（尚无被拦事件，或还没接线）")
            return 0
        out = update_forward(path=p)
        print(f"✅ 影子账远期回填：更新 {out['updated']} / 共 {out['n']} 条 → {p}")
        return 0

    if args.check:
        reg = load_registry()
        seen: dict[str, int] = {}
        for r in load_ghost(path):
            seen[str(r.get("rule") or "")] = seen.get(str(r.get("rule") or ""), 0) + 1
        unknown = sorted(k for k in seen if k and k not in reg.get("rules", {}))
        print(f"影子账 {sum(seen.values())} 条，规则 {len(seen)} 种；"
              f"登记表 {len(reg.get('rules') or {})} 条")
        if unknown:
            print(f"⚠️ 未登记规则（有人加了闸没进登记表）：")
            for u in unknown:
                print(f"    {u}  ({seen[u]} 条)")
        dead = [r for r in (reg.get("rules") or {}) if r not in gate_rules.ALL]
        if dead:
            print(f"⚠️ 登记表死条目（词表已无此规则）：{dead}")
        exp = expired_rules()
        if exp:
            print("⚠️ 复核已过期（规则默认会过期，不默认正确）：")
            for rule, rb in exp:
                print(f"    {rule}  复核日 {rb}")
        if not (unknown or dead or exp):
            print("✅ 登记表健康")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
