#!/usr/bin/env python3
"""P1 影子 A/B 周报（只读离线，2026-09-18）。

数据源：logs/shadow_context/{date}.jsonl（由 live_llm_trade --shadow-context 写）。
实验设计：A1/A2 = 同一「现状提示词」重跑两次（temperature=0.3 的**采样噪声基线**），
B = 现状提示词 + 盘面状态 + 新闻分子（处理臂）。

判据：noise = diff(A1, A2)；effect = diff(A1, B)。**只有 effect 稳定高于 noise，
才说明"多给的信息改变了决策"**——这是灰度上线（把上下文接进 09:35 主入口）
的前置证据，而不是"A/B 有差异就上线"。

forced=True 的手动冒烟记录不计入统计。

用法：
  python scripts/shadow_context_report.py                 # 近 7 天，打印
  python scripts/shadow_context_report.py --days 14 --out logs/shadow_context/report.md
  python scripts/shadow_context_report.py --push          # 附带给 QQ 推一行摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "logs" / "shadow_context"
MAX_EXAMPLES = 3          # 每个 agent 每类差异最多列几个例子
BJ = timezone(timedelta(hours=8))   # 记录文件名是北京日期（live_llm_trade.now_cn）


def load_records(dir_path: Path | str, since: str, until: str) -> list[dict]:
    """读 [since, until]（含边界，YYYY-MM-DD）内的记录；坏行/坏文件跳过不炸。"""
    d = Path(dir_path)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.jsonl")):
        stem = f.stem
        if not (since <= stem <= until):
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("agents"), dict):
                out.append(rec)
    return out


def _norm(decisions) -> dict:
    """决策列表 → {code: "action pct[标志]"}；`?`=未表达比例、`!`=脏值。

    pct 三态必须参与比较（评审 MEDIUM）："未给比例"与"给了 0"与"脏值停手"
    在 parse_decision 层面是三种不同语义，塌成一个 0.0 会低估 effect。
    """
    out: dict[str, str] = {}
    for d in decisions or []:
        if not isinstance(d, dict) or not d.get("code"):
            continue
        action = str(d.get("action") or "?")
        pct = d.get("pct")
        try:
            tag = f"{action} {round(float(pct), 4):g}" if pct is not None else action
        except (TypeError, ValueError):
            tag = action
        if not d.get("pct_given", True):
            tag += "?"
        if d.get("pct_bad_raw"):
            tag += "!"
        out[str(d["code"])] = tag
    return out


def compare_decisions(a, b) -> list[dict]:
    """A/B 决策对比 → [{"code", "from", "to"}]，完全一致返回 []。"""
    na, nb = _norm(a), _norm(b)
    changes = []
    for code in sorted(set(na) | set(nb)):
        fa, tb = na.get(code, "absent"), nb.get(code, "absent")
        if fa != tb:
            changes.append({"code": code, "from": fa, "to": tb})
    return changes


def _arm(rec: dict, agent: str, key: str) -> dict:
    v = ((rec.get("agents") or {}).get(agent) or {}).get(key)
    return v if isinstance(v, dict) else {}


def summarize(records: list[dict]) -> dict:
    """按 agent 汇总：noise（A1 vs A2）/ effect（A1 vs B）。

    三条口径（2026-09-18 双评审修正，直接决定"要不要灰度上线"）：
      * **失败臂不参与判定**（HIGH-1）：臂 fail≠"" 时 decisions=None，若照算
        会被折算成"全仓缺席 → 差异"，把纯 API 失败印成 effect——且 B 臂提示词
        更长、失败率天然更高，偏差方向恰是"支持上线"。失败日只计入 fails 面。
      * **同 (date, agent) 多行保留最后一条**（MEDIUM-2）：cron 重试/手动补跑的
        追加行不能把"天数"灌水（采样侧已有当日幂等，这里是第二道）。
      * **块缺失必须上报告面**（HIGH-1）：B 臂没加进上下文的日子，effect 无意义，
        单独计数并在判读中提示。
    forced 记录整体剔除。
    """
    recs = [r for r in records if not r.get("forced")]
    latest: dict[tuple, dict] = {}
    for rec in recs:
        for agent in rec.get("agents") or {}:
            latest[(str(rec.get("date")), agent)] = rec   # 后写覆盖 = 保留最后一条
    block_missing_dates = {day for (day, _a), rec in latest.items()
                           if not (rec.get("market_state") and rec.get("news_present"))}
    per: dict[str, dict] = {}
    for (day, agent), rec in sorted(latest.items()):
        s = per.setdefault(agent, {"days": 0, "noise_days": 0, "effect_days": 0,
                                   "noise_examples": [], "effect_examples": [],
                                   "buys_a1": 0, "buys_b": 0,
                                   "sells_a1": 0, "sells_b": 0,
                                   "fails": {"a1": 0, "a2": 0, "b": 0},
                                   "missing_block_days": 0})
        a1, a2, b = _arm(rec, agent, "a1"), _arm(rec, agent, "a2"), _arm(rec, agent, "b")
        fails = {k: str(arm.get("fail") or "")
                 for k, arm in (("a1", a1), ("a2", a2), ("b", b))}
        for k, f in fails.items():
            if f:
                s["fails"][k] += 1
        if not (rec.get("market_state") and rec.get("news_present")):
            s["missing_block_days"] += 1
        if any(fails.values()):
            continue                          # 失败样本不入判定（HIGH-1）
        s["days"] += 1
        noise = compare_decisions(a1.get("decisions"), a2.get("decisions"))
        effect = compare_decisions(a1.get("decisions"), b.get("decisions"))
        if noise:
            s["noise_days"] += 1
            if len(s["noise_examples"]) < MAX_EXAMPLES:
                s["noise_examples"].append({"date": day, "changes": noise})
        if effect:
            s["effect_days"] += 1
            if len(s["effect_examples"]) < MAX_EXAMPLES:
                s["effect_examples"].append({"date": day, "changes": effect})
        for arm, tag in ((a1, "a1"), (b, "b")):
            for d in arm.get("decisions") or []:
                if not isinstance(d, dict):
                    continue
                act = str(d.get("action") or "")
                if act == "buy":
                    s["buys_" + tag] += 1
                elif act == "sell":
                    s["sells_" + tag] += 1
    return {"days": len({d for d, _a in latest}),
            "dates": sorted({d for d, _a in latest}),
            "block_missing_dates": sorted(block_missing_dates),
            "per_agent": per}


def render(summary: dict, since: str, until: str) -> str:
    lines = [f"# P1 影子 A/B 周报（{since} ~ {until}）",
             "",
             f"记录日期 {len(summary['dates'])} 天（含未进入判定的失败日）："
             f"{'、'.join(summary['dates']) or '（无）'}",
             ""]
    if not summary["dates"]:
        lines.append("⚠️ 窗口内无记录（检查 cron 是否在跑 / forced 冒烟不计入）。")
        return "\n".join(lines) + "\n"
    if summary["block_missing_dates"]:
        lines.append(f"🚨 **块缺失日**：{('、'.join(summary['block_missing_dates']))}"
                     "——B 臂当天的上下文没加进去，该日 effect 无意义（已单独计数，"
                     "但仍计入判定前请人工剔除）。")
        lines.append("")
    lines += ["| agent | 判定天数 | noise（A1≠A2） | effect（A1≠B） | B 新增买/卖 "
              "| 臂失败 A1/A2/B | 块缺失 |",
              "|---|---|---|---|---|---|---|"]
    for agent, s in sorted(summary["per_agent"].items()):
        n, e = s["noise_days"], s["effect_days"]
        f = s["fails"]
        lines.append(f"| {agent} | {s['days']} | {n}/{s['days']} | {e}/{s['days']} | "
                     f"买 {s['buys_b'] - s['buys_a1']:+d} / "
                     f"卖 {s['sells_b'] - s['sells_a1']:+d} | "
                     f"{f['a1']}/{f['a2']}/{f['b']} | {s['missing_block_days']} 天 |")
    lines += ["", "## 判读（先看这行）",
              "",
              f"- **当前样本 n={summary['days']}（三臂全成功且块齐的判定日）**："
              "n<10 个交易日只观察、不下上线结论。",
              "- **noise 是采样噪声基线**：temperature=0.3 下同一提示词重跑本就有差异；"
              "effect 与 noise 相当 → 多给的信息**没有**改变决策倾向，上线收益存疑。",
              "- effect **稳定高于** noise，且方向合理（如更少追高、更多依据新闻调整）→ "
              "支持灰度：把上下文接进 09:35 主入口（build_prompt 已带 extra_context 参数）。",
              "- **臂失败（fail≠\"\"）的样本已被排除在判定之外**：失败数是质量面，"
              "B 臂失败显著多于 A 臂 → 先查 token/截断，别急着上线。",
              "- 块缺失日（盘面/新闻没加进去）的样本 effect 无意义——若块缺失天数多，"
              "先修采样链路（build_daily_context_blocks 的 ⚠️ 日志在 shadow_context.log）。",
              ""]
    for agent, s in sorted(summary["per_agent"].items()):
        if s["effect_examples"]:
            lines.append(f"## {agent} · effect 示例（最多 {MAX_EXAMPLES} 例）")
            for ex in s["effect_examples"]:
                chg = "；".join(f"{c['code']} {c['from']}→{c['to']}"
                                for c in ex["changes"][:4])
                lines.append(f"- {ex['date']}：{chg}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P1 影子 A/B 周报（只读）")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="记录目录")
    ap.add_argument("--days", type=int, default=7, help="回看天数（默认 7）")
    ap.add_argument("--out", default="", help="写 markdown 到该路径（默认只打印不落盘）")
    ap.add_argument("--push", action="store_true", help="附带给 QQ 推一行摘要")
    args = ap.parse_args(argv)

    if args.days < 1:
        print("❌ --days 至少为 1", file=sys.stderr)
        return 2
    until = datetime.now(BJ).date()
    since = until - timedelta(days=args.days - 1)
    recs = load_records(args.dir, since.isoformat(), until.isoformat())
    summary = summarize(recs)
    md = render(summary, since.isoformat(), until.isoformat())
    print(md)

    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            print(f"✅ 周报已落盘 → {out}")
        except OSError as exc:
            # 落盘失败必须让 cron 能看到（rc≠0）且不推"已生成"的假消息（评审 M-4）
            print(f"❌ 周报落盘失败：{exc}", file=sys.stderr)
            return 1

    if args.push:
        agg = summary["per_agent"]
        brief = "；".join(
            f"{a}: noise {s['noise_days']}/{s['days']} vs effect {s['effect_days']}/{s['days']}"
            for a, s in sorted(agg.items())) or "（窗口内无有效记录）"
        quality = []
        if summary["block_missing_dates"]:
            quality.append(f"块缺失 {len(summary['block_missing_dates'])} 天（effect 存疑）")
        fails = sum(s["fails"]["a1"] + s["fails"]["a2"] + s["fails"]["b"]
                    for s in agg.values())
        if fails:
            quality.append(f"臂失败 {fails} 次（已排除判定）")
        if quality:
            brief += "\n⚠️ " + "；".join(quality)
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from push_notify import notify

            notify("🧪 P1 影子A/B 周报",
                   f"近 {args.days} 天（判定样本 n={summary['days']}）：{brief}\n"
                   "判读口径：n<10 只观察；effect 需稳定高于 noise 才支持上线"
                   "（详见 docs/INSTITUTIONAL_GRADE_PLAN.md P1）")
        except Exception as exc:  # noqa: BLE001 推送失败不影响报告
            print(f"⚠️ QQ 推送失败：{str(exc)[:100]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
