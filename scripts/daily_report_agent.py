#!/usr/bin/env python3
"""系统运行日报 agent（P1-1）：每天收盘后汇总全系统状态 → 一页纸 + 对话流。

聚合：agent 决策轮数/成交流水/净值变动/复盘完成度/仲裁/预算档位/晚间池/
服务健康/异常计数 → dsh(glm) 写导读与待办 → logs/daily_report/{date}.md/.json
+ 对话流（market-research，kind=daily_report）。
cron：交易日 北京 17:00。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def now_cn() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def collect(date: str | None = None) -> dict:
    from live_trade_picks import enabled_agents

    agents = enabled_agents()
    today = date or now_cn()
    out = {"date": today, "agents": [], "system": {}}

    # 每 agent：轮次/成交/净值/复盘
    for a in agents:
        rec = {"agent": a}
        lf = ROOT / "data" / "agent_data_astock" / a / "log" / today / "log.jsonl"
        rounds = 0
        try:
            rounds = sum(1 for l in lf.read_text(encoding="utf-8").splitlines() if l.strip())
        except OSError:
            pass
        rec["rounds"] = rounds
        # 成交
        fills = []
        for f in (ROOT / "logs").glob("live_trade_*.jsonl"):
            if "_us_" in f.name or "_hk_" in f.name:
                continue
            try:
                for l in f.read_text(encoding="utf-8").splitlines():
                    r = json.loads(l)
                    if r.get("agent") == a and r.get("ts", "").startswith(today) \
                            and not r.get("error") and r.get("side"):
                        fv = int((r.get("fill") or {}).get("filled_volume") or 0)
                        if fv > 0 or r.get("mode") == "fill_confirm":
                            fills.append(r)
            except (OSError, json.JSONDecodeError):
                continue
        rec["fills"] = len(fills)
        # 净值首末
        vals = []
        try:
            for l in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
                r = json.loads(l)
                if r.get("agent") == a and r.get("date") == today and r.get("value") is not None:
                    vals.append(r["value"])
        except (OSError, ValueError):
            pass
        rec["nav_first"] = vals[0] if vals else None
        rec["nav_last"] = vals[-1] if vals else None
        # 复盘产物
        rec["reviewed"] = (ROOT / "logs" / "review" / a / f"{today}.md").is_file()
        out["agents"].append(rec)
    # 系统级
    total_vals = []
    try:
        for l in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("agent") is None and r.get("date") == today and r.get("value") is not None:
                total_vals.append(r["value"])
    except (OSError, ValueError):
        pass
    out["system"]["total_nav"] = {"first": total_vals[0] if total_vals else None,
                                  "last": total_vals[-1] if total_vals else None}
    out["system"]["debates"] = (ROOT / "logs" / "debates" / f"{today}.jsonl").is_file()
    try:
        b = json.loads((ROOT / "configs" / "risk_budget.json").read_text(encoding="utf-8"))
        out["system"]["budget"] = {"level": b.get("level"), "label": b.get("label")}
    except (OSError, ValueError):
        pass
    pool_files = sorted((ROOT.parent / "projects/quantmind/data/reports/stock_picks")
                        .glob(f"{today.replace('-', '')}*_agent_picks.json"))
    out["system"]["night_pool"] = bool(pool_files)
    # 健康
    try:
        svc = json.loads((ROOT / "logs" / "service_status.json").read_text(encoding="utf-8"))
        out["system"]["services"] = svc
    except (OSError, ValueError):
        pass
    err_count = 0
    try:
        text = (ROOT / "logs" / "live_hourly_analysis.log").read_text(encoding="utf-8")
        err_count = text.count("失败:") + text.count("❌")
    except OSError:
        pass
    out["system"]["err_lines"] = err_count
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    facts = collect(date=a.date or None)
    today = facts["date"]
    facts_json = json.dumps(facts, ensure_ascii=False)
    task = (
        f"[系统运行日报 {today}] 以下是今日自动化事实，请写一页纸中文日报：\n{facts_json}\n"
        "结构：①一句话总评 ②各 agent 表现表（轮次/成交/净值变动/复盘是否完成）"
        "③系统状态（预算档位/晚间池/仲裁/服务/异常计数）④待办与风险（≤3 条，可执行）"
        "\n输出 md。")
    try:
        from dsh_agent import run_agent

        content = run_agent(task, timeout_s=180, model="glm")
    except Exception as exc:  # noqa: BLE001
        content = f"（日报 LLM 失败：{exc}）\n\n{facts_json}"
    d = ROOT / "logs" / "daily_report"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{today}.md").write_text(f"# 系统运行日报 · {today}\n\n{content}", encoding="utf-8")
    (d / f"{today}.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    # 对话流（市场研究卡下追加）
    try:
        lf = ROOT / "data" / "agent_data_astock" / "market-research" / "log" / today / "log.jsonl"
        lf.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": datetime.now().astimezone().isoformat(),
               "signature": "market-research", "kind": "daily_report",
               "new_messages": [
                   {"role": "user", "content": f"【系统运行日报】{today}"},
                   {"role": "assistant", "content": content},
               ]}
        with lf.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:  # noqa: BLE001
        print(f"⚠️ 写对话流失败: {exc}")
    print(f"✅ 日报完成 → logs/daily_report/{today}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())