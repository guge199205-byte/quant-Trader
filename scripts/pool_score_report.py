#!/usr/bin/env python3
"""P2 读数面：候选池位置 → 远期超额（只读离线，2026-09-18）。

回答一个问题：**池内分数更低（=排名更靠后）的买入，远期超额更差吗？**
只有答"是"且幅度稳定，才谈得上给 candidate 池设分数门限；答案含糊或相反，
设闸就是凭空砍掉选择面。

为什么按「先分组、再按 rank 分档」：
  * **选择集内**（state=shown）才是"模型在低分票与高分票之间怎么选"；
  * **池内被剔除**（state=dropped）是"高分票因为买不起，压根没进模型的选择集"
    ——09-17 实录 20 只池剔除 11 只（含 rank 1/2/3/4/6/8）。把这一档混进
    "低分买入"会把**资金约束**读成**模型无视分数**，方向完全相反；
  * **池外**（state=off）是模型凭持仓/新闻自选，与池子分数无关。
分组读戳里的 `state` 而不是从 `rank` 反推：池行可能没有 rank（md 兜底分支），
那时"被剔除"与"池外"在 rank 上同形——反推会把两者互换。rank 只用来分档，
池内行缺 rank 时单列"未标排名"档。

数据来自 logs/decision_pool.jsonl 的 pool_ctx 字段（2026-09-18 起写入）。
没有该字段的记录（旧格式）单独计数——覆盖面本身是本报告的第一行读数。

用法：
  python scripts/pool_score_report.py                 # 近 30 天，打印
  python scripts/pool_score_report.py --days 60 --out logs/pool_score/report.md
  python scripts/pool_score_report.py --push          # 附带给 QQ 推一行摘要
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from decision_track import MIN_SAMPLE, POOL  # noqa: E402  唯一出处：门槛与池路径

BJ = timezone(timedelta(hours=8))

GROUP_SHOWN = "选择集内"
GROUP_DROPPED = "池内被剔除"
GROUP_OFF = "池外"
GROUP_ORDER = (GROUP_SHOWN, GROUP_DROPPED, GROUP_OFF)

BAND_OFF = "池外"
BAND_UNRANKED = "未标排名"
#: 档位顺序**显式写死**：`sorted()` 是字典序，会把表排成 #1-5 → #11-20 → #21+ →
#: #6-10（这份报告的全部意义是自上而下读梯度，错序等于读反）。
BAND_ORDER = ("#1-5", "#6-10", "#11-20", "#21+", BAND_UNRANKED, BAND_OFF)


def rank_band(rank, state: str = "") -> str:
    """池内排名 + 位置态 → 档位标签。

    rank 不可用（缺失/非数值）时按位置态区分：池外标的本来就是"池外"档；
    池内行没标排名（select_from_reports 的 md 兜底分支会给所有行 rank=None）
    是**另一件事**——把它并进"池外"会把池内样本读成模型自选（审查 MEDIUM）。
    """
    if state == "off":
        return BAND_OFF
    if rank is None:
        return BAND_UNRANKED
    try:
        r = int(rank)          # OverflowError：json 里的 Infinity（审查 LOW）
    except (TypeError, ValueError, OverflowError):
        return BAND_UNRANKED
    if r <= 5:
        return "#1-5"
    if r <= 10:
        return "#6-10"
    if r <= 20:
        return "#11-20"
    return "#21+"


def state_of(ctx: dict | None) -> str:
    """位置戳 → 三态 "shown"/"dropped"/"off"；无戳/坏戳返回 ""（不入统计）。

    优先读戳里显式的 state（decision_track.pool_stamp 写的，与 rank 分开记）；
    没有 state 的旧戳按 rank 反推——**兼容读法**，池行缺 rank 时会退化成
    "池外"，所以新戳一律由 pool_stamp 显式写 state（审查 MEDIUM）。
    """
    if not isinstance(ctx, dict):
        return ""
    st = ctx.get("state")
    if st in ("shown", "dropped", "off"):
        return str(st)
    if ctx.get("shown"):
        return "shown"
    return "dropped" if ctx.get("rank") is not None else "off"


def group_of(ctx: dict | None) -> str:
    """位置戳 → 分组；无戳/坏戳返回 ""（不入统计）。"""
    return {"shown": GROUP_SHOWN, "dropped": GROUP_DROPPED,
            "off": GROUP_OFF}.get(state_of(ctx), "")


def load_records(path: Path | str = POOL, since: str = "", until: str = "") -> list[dict]:
    """读决策池；坏行跳过不炸。[since, until] 为 YYYY-MM-DD 闭区间，空=不限。"""
    out: list[dict] = []
    p = Path(path)
    if not p.is_file():
        return out
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        day = str(rec.get("date") or "")
        if since and day < since:
            continue
        if until and day > until:
            continue
        out.append(rec)
    return out


def _excess(fwd: dict | None, key: str) -> float | None:
    """fwd.<key>.excess → 有限浮点数；坏值/NaN/inf 一律 None（降级不抛）。"""
    sub = fwd.get(key) if isinstance(fwd, dict) else None
    val = sub.get("excess") if isinstance(sub, dict) else None
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None   # NaN 静默进均值会污染结论（审查 MEDIUM）


def summarize(records: list[dict]) -> dict:
    """按 (分组, 档位) 汇总远期超额。只有 kind=bullish（选股能力）入统计。

    四个口径（缺一不可）：
      * **没有 pool_ctx 的记录单独计入 n_unstamped**：覆盖面上报告面，否则
        "样本看起来够了"其实是插桩之后才够的；
      * **远期收益未回填/坏值单独计入 n_pending**：新决策当天没有 T+1 数据，
        脏值（非数值、NaN、inf）与未回填同样不可用；
      * **t1 与 t5 各算各的 n**：t5 天然稀疏，拿 t1 的样本量替 t5 说话是高估；
      * **分组用戳里的 state 而不是 rank 反推**（见 state_of）。
    """
    n_total = n_ctx = n_pending = 0
    groups: dict[str, dict[str, dict]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "") != "bullish":
            continue
        n_total += 1
        ctx = rec.get("pool_ctx")
        if not isinstance(ctx, dict):
            continue
        n_ctx += 1
        fwd = rec.get("fwd") if isinstance(rec.get("fwd"), dict) else {}
        t1 = _excess(fwd, "t1")
        if t1 is None:
            n_pending += 1
            continue
        band = rank_band(ctx.get("rank"), state_of(ctx))
        s = groups.setdefault(group_of(ctx), {}).setdefault(
            band, {"n": 0, "sum": 0.0, "win": 0, "n_t5": 0, "sum_t5": 0.0})
        s["n"] += 1
        s["sum"] += t1
        s["win"] += 1 if t1 > 0 else 0
        t5 = _excess(fwd, "t5")
        if t5 is not None:
            s["n_t5"] += 1
            s["sum_t5"] += t5
    for bands in groups.values():
        for s in bands.values():
            s["mean"] = s["sum"] / s["n"] if s["n"] else None
            s["win_rate"] = s["win"] / s["n"] if s["n"] else None
            s["mean_t5"] = (s["sum_t5"] / s["n_t5"]) if s["n_t5"] else None
    return {"n_total": n_total, "n_ctx": n_ctx, "n_unstamped": n_total - n_ctx,
            "n_pending": n_pending, "n_used": n_ctx - n_pending, "groups": groups}


def _verdict_lines(summary: dict) -> list[str]:
    """判读：先看样本，再看方向；样本不足时**只说样本不足**。"""
    shown = (summary.get("groups") or {}).get(GROUP_SHOWN) or {}
    comparable = [b for b in BAND_ORDER if shown.get(b, {}).get("n", 0) >= MIN_SAMPLE]
    lines = [f"- **入统计样本 n={summary['n_used']}**（每档需 ≥{MIN_SAMPLE} 才谈方向，"
             f"当前达标档位：{('、'.join(comparable) if comparable else '无')}）。"]
    if len(comparable) < 2:
        lines.append("- **样本不足**：选择集内至少要有两个达标档位才能比较"
                     "「低分 vs 高分」的远期超额。当前不下任何结论——"
                     "**更不应据此设闸**（凭均值差设闸等于给噪声立规矩）。")
    else:
        lines.append("- 达标档位已可比较：只有当**排名靠后的档位远期超额持续更差**"
                     "（跨周稳定、不是单日行情），才谈得上设分数门限；"
                     "幅度还须大于该 agent 的日常波动。")
    lines += [
        "- **`池内被剔除` 档要看两个数**：它的 n 说明「高分票因买不起而退出"
        "选择集」发生得多不多；它的远期超额说明被剔除的那批票到底好不好。若它"
        "明显优于 `选择集内`，问题在**资金规模/池子价格结构**，不在分数门限。",
        "- **`池外` 档是模型自选**（持仓盯盘/新闻驱动），与池内分数无关，"
        "不能拿来评价池子质量。",
        "- 设闸的前置条件（缺一不可）：两组各 ≥30 样本、方向跨周稳定、"
        "且闸门必须能区分池种类——quantmind 盘后池与晚间研究池的 score 量纲"
        "不同（同日 CV 仅 0.02~0.09，且晚间池根本没有 fusion 字段）。",
    ]
    return lines


def render(summary: dict, since: str, until: str, pool_missing: bool = False) -> str:
    """汇总 → markdown（表格 + 口径说明 + 判读）。判读永远在最后，先给数再给话。"""
    lines = [f"# P2 候选池位置 → 远期超额（{since} ~ {until}）", ""]
    cov = (f"bullish 决策 {summary['n_total']} 条：带池位置戳 {summary['n_ctx']} 条、"
           f"旧格式未插桩 {summary['n_unstamped']} 条、"
           f"待回填远期收益 {summary['n_pending']} 条 → 入统计 {summary['n_used']} 条")
    lines += [cov, ""]
    if pool_missing:
        # 路径写错/文件不在 ≠ 没插桩：两者都"零样本"，但修法完全相反（审查 LOW）
        lines += ["⚠️ 决策池文件不存在——这是**路径问题**，不是插桩缺失："
                  "确认 --pool（或默认 logs/decision_pool.jsonl）指向真实文件。", ""]
    if not summary["n_used"]:
        lines += ["⚠️ 无可用样本：先确认两条执行路径都在写 pool_ctx"
                  "（tests/test_pool_ctx_ingest.py 的接线防线）。", ""]
        lines += ["## 判读", ""] + _verdict_lines(summary)
        return "\n".join(lines) + "\n"      # 无样本不渲染表格（表头后空一行只会误导）
    lines += ["| 分组 | 档位 | n | 均超额 T+1 | 胜率 | n | 均超额 T+5 |",
              "|---|---|---|---|---|---|---|"]
    for group in GROUP_ORDER:
        bands = (summary["groups"] or {}).get(group) or {}
        order = [b for b in BAND_ORDER if b in bands] + [b for b in bands if b not in BAND_ORDER]
        for band in order:                 # 显式序：字典序会把 #6-10 排到 #21+ 后面
            s = bands[band]
            m5 = "—" if s["mean_t5"] is None else f"{s['mean_t5']:+.2%}"
            lines.append(f"| {group} | {band} | {s['n']} | {s['mean']:+.2%} | "
                         f"{s['win_rate']:.0%} | {s['n_t5']} | {m5} |")
    lines += ["", "口径：入场价 = T+1 开盘，超额 = 个股 − 全市场等权（decision_track）；"
                  "rank = 该票在候选池文件内的原始排名。", ""]
    lines += ["## 判读", ""] + _verdict_lines(summary)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2 候选池位置 → 远期超额（只读）")
    ap.add_argument("--pool", default=str(POOL), help="决策池 jsonl 路径")
    ap.add_argument("--days", type=int, default=30, help="回看天数（默认 30）")
    ap.add_argument("--out", default="", help="写 markdown 到该路径（默认只打印）")
    ap.add_argument("--push", action="store_true", help="附带给 QQ 推一行摘要")
    args = ap.parse_args(argv)

    if args.days < 1:
        print("❌ --days 至少为 1", file=sys.stderr)
        return 2
    until = datetime.now(BJ).date()
    since = until - timedelta(days=args.days - 1)
    summary = summarize(load_records(args.pool, since.isoformat(), until.isoformat()))
    md = render(summary, since.isoformat(), until.isoformat(),
                pool_missing=not Path(args.pool).is_file())
    print(md)

    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            # 原子落盘（同 decision_track._rewrite）：中断不留半截 markdown
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(md, encoding="utf-8")
            os.replace(tmp, out)
            print(f"✅ 报告已落盘 → {out}")
        except OSError as exc:
            print(f"❌ 报告落盘失败：{exc}", file=sys.stderr)   # rc≠0：cron 视角不能是"成功"
            return 1

    if args.push:
        try:
            brief = (f"bullish {summary['n_total']} 条｜带池戳 {summary['n_ctx']}"
                     f"｜入统计 {summary['n_used']}｜未插桩 {summary['n_unstamped']}")
            shown = (summary["groups"] or {}).get(GROUP_SHOWN) or {}
            bands = "；".join(f"{b} n={shown[b]['n']} {shown[b]['mean']:+.2%}"
                              for b in BAND_ORDER if b in shown)
            from push_notify import notify

            notify("📊 P2 池位置→远期超额",
                   f"近 {args.days} 天：{brief}\n选择集内：{bands or '（无）'}\n"
                   f"每档 ≥{MIN_SAMPLE} 才谈方向；样本不足时不下结论"
                   "（详见 docs/INSTITUTIONAL_GRADE_PLAN.md P2）")
        except Exception as exc:  # noqa: BLE001 推送失败不影响报告
            print(f"⚠️ QQ 推送失败：{str(exc)[:100]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
