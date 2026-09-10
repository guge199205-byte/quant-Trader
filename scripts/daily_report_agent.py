#!/usr/bin/env python3
"""系统运行日报 agent（P1-1）：每天收盘后汇总全系统状态 → 一页纸 + 对话流。

聚合：agent 决策轮数/成交流水/净值变动/复盘完成度/仲裁/预算档位/晚间池/
服务健康/异常计数 → dsh(glm) 写导读与待办 → logs/daily_report/{date}.md/.json
+ 对话流（market-research，kind=daily_report）。
cron：交易日 北京 17:00。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_cal import is_trading_day, why_not  # noqa: E402


def now_cn() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _load_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _rounds_from_log(text: str) -> tuple[int, int]:
    """当日 log.jsonl → (分析轮数, 未执行轮数)。

    2026-09-10 实录：7 轮全是「本轮分析未执行」（桥假活），日报却写"8 轮满额运行、
    流程纪律正常"——只数条数不看内容，等于替哑火的链路盖章。"""
    rounds = failed = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("kind") or "") == "review":
            continue          # 盘后复盘不算盘中轮次
        rounds += 1
        body = " ".join(str(m.get("content") or "")
                        for m in (rec.get("new_messages") or []) if isinstance(m, dict))
        if "本轮分析未执行" in body:
            failed += 1
    return rounds, failed


def _err_lines_for_date(text: str, date: str) -> int:
    """异常行计数，按日期归属：无时间戳的行归到最近一条带 [YYYY-MM-DD …] 前缀的行。

    2026-09-10 实录：旧口径把整份日志的累计值（369 行）当成当日值写进日报，
    量级对不上（当日实际 5 行）还查不出源头。"""
    cur = ""
    n = 0
    for line in text.splitlines():
        m = re.match(r"\[(\d{4}-\d{2}-\d{2}) ", line)
        if m:
            cur = m.group(1)
        if cur == date and ("失败:" in line or "❌" in line):
            n += 1
    return n


def _exec_channel(agents: list) -> dict:
    """执行通道体检（2026-09-10 实录）。

    当日「桥假活 + 执行开关关闭」双哑火：账户通道整日 asset=0、7 轮分析全失败、
    3 次调仓一单未动、哨兵条件位全空，日报却写"零成交、观望一致"。链路状态必须
    有独立字段，日报 LLM 才可能写进去（数据先要有，写不写是提示词的事）。"""
    ec: dict = {"rounds_failed": sum(int(r.get("rounds_failed") or 0) for r in agents)}
    st = _load_json(ROOT / "logs" / "live_llm_trade_state.json")
    ec["trade_state"] = {"day": st.get("day"), "ok": st.get("ok"),
                         "orders_attempted": st.get("orders_attempted"),
                         "note": str(st.get("note") or "")[:80]} if st else {}
    an = _load_json(ROOT / "logs" / "live_analysis_state.json")
    ec["last_good_account_ts"] = an.get("last_good_sample_ts")
    ec["last_sample_asset"] = an.get("last_sample_asset")
    ec["exec_enabled"] = bool(_load_json(ROOT / "configs" / "intraday_exec.json").get("enabled"))
    ec["watch_rules"] = {a: len(v) for a, v in _load_json(ROOT / "data" / "live_watch.json").items() if v}
    rt = _load_json(ROOT / "logs" / "rt_status.json")
    ec["account_probe"] = {"ok": (rt.get("account") or {}).get("ok"),
                           "error": (rt.get("account") or {}).get("error")}
    pf = _load_json(ROOT / "logs" / "preflight.json")
    if pf:
        ec["preflight_ok"] = pf.get("ok")
        ec["preflight_problems"] = (pf.get("problems") or [])[:3]
    return ec


def collect(date: str | None = None) -> dict:
    from live_trade_picks import enabled_agents

    agents = enabled_agents()
    today = date or now_cn()
    out = {"date": today, "agents": [], "system": {}}

    # 每 agent：轮次/成交/净值/复盘
    for a in agents:
        rec = {"agent": a}
        lf = ROOT / "data" / "agent_data_astock" / a / "log" / today / "log.jsonl"
        rounds = failed = 0
        try:
            rounds, failed = _rounds_from_log(lf.read_text(encoding="utf-8"))
        except OSError:
            pass
        rec["rounds"] = rounds
        rec["rounds_failed"] = failed
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
    # 循环熔断（当日亏损/回撤超限 → 禁买）：有则进日报，让用户看到风控真的拦过
    try:
        from live_breaker import load_trips

        out["system"]["breaker"] = load_trips(today)
    except Exception:  # noqa: BLE001
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
        err_count = _err_lines_for_date(text, today)
    except OSError:
        pass
    out["system"]["err_lines"] = err_count
    out["system"]["exec_channel"] = _exec_channel(out["agents"])
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    if not a.date:
        from zoneinfo import ZoneInfo

        bj = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if not is_trading_day(bj):
            print(f"⏭️ {bj} 非交易日（{why_not(bj)}），跳过系统运行日报（休市无运行数据）")
            return 0
    facts = collect(date=a.date or None)
    today = facts["date"]
    facts_json = json.dumps(facts, ensure_ascii=False)
    task = (
        f"[系统运行日报 {today}] 以下是今日自动化事实，请写一页纸中文日报：\n{facts_json}\n"
        "结构：①一句话总评 ②各 agent 表现表（轮次/成交/净值变动/复盘是否完成）"
        "③系统状态（预算档位/晚间池/仲裁/服务/异常计数/执行通道）④待办与风险（≤3 条，可执行）"
        "\n硬要求：exec_channel 是执行链路体检——若 account_probe.ok 为假、"
        "rounds_failed>0、trade_state.ok 为假或 exec_enabled 为假，"
        "说明链路出故障，必须写进①和④（例：账户通道掉线导致 N 轮分析未执行、一单未动），"
        "禁止写成\"零成交、观望一致、系统平稳\"这类把故障说成策略选择的措辞；"
        "err_lines 是当日计数（非累计）。"
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