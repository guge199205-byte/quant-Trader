#!/usr/bin/env python3
"""盘前健检（北京 09:12，开盘前 18 分钟）：把"今天能不能下单/能不能卖"验一遍。

2026-09-10 实录：桥行情通道正常、账户通道整日 asset=0，7 轮盘中分析 + 3 次 09:35
调仓 + 全部哨兵条件位静默哑火，直到收盘后复盘才发现，全天零成交。事后看，只要
开盘前问一次 account/query 并跟台账对一遍，就能在 09:30 前把问题摆到台面上。

四项检查（每一项都能单独让人倒霉）：
  1) 账户通道：asset > 0 且能读出持仓（掉了 → 行情能看、下单/查询全哑火）
  2) 台账对账：分账台账里的每个持仓在券商快照里都能找到（漂移 → 卖错/漏卖）
  3) 执行开关：configs/intraday_exec.json（关 = 哨兵只打印不真卖）
  4) 条件位清单：当前挂了几条、各在谁名下（给人一眼看清今天守什么）

输出 logs/preflight.json（供日报/告警消费）+ 一行摘要；有问题则追加一行到
/home/zbox/backups/baymax/alerts.log（与 alert.sh 同渠道）并返回 1。

用法：python scripts/preflight_bridge.py [--date YYYY-MM-DD] [--force]
cron（本机 JST，北京=JST-1）: 12 10 * * 1-5
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CN_TZ = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "logs" / "preflight.json"
ALERT_LOG = Path("/home/zbox/backups/baymax/alerts.log")
EXEC_CFG = ROOT / "configs" / "intraday_exec.json"
WATCH_FILE = ROOT / "data" / "live_watch.json"


def reconcile(ledger: dict, positions: list) -> dict:
    """分账台账 × 券商持仓快照 → {"by_agent": {...}, "drift": [...], "unassigned": [...]}。

    drift = 台账说持有、券商说没有（危险方向：条件位会挂空、止损保护不了）
    unassigned = 券商有、台账没有（2026-08-31 那批不入分账的总账户持仓，属预期）
    """
    pos_codes = {str(p.get("stock_code") or "") for p in positions or []}
    by_agent, drift = {}, []
    for agent, rec in sorted((ledger.get("agents") or {}).items()):
        codes = sorted(((rec or {}).get("positions") or {}).keys())
        if not codes:
            continue
        by_agent[agent] = codes
        drift += [{"agent": agent, "code": c} for c in codes if c not in pos_codes]
    assigned = {c for codes in by_agent.values() for c in codes}
    unassigned = sorted(pos_codes - assigned - {""})
    return {"by_agent": by_agent, "drift": drift, "unassigned": unassigned}


def evaluate(acct: dict | None, acct_err: str, ledger: dict,
             exec_enabled: bool, watch: dict) -> dict:
    """四项检查 → 结果 dict（纯函数，便于单测）。acct=None 表示账户查询失败。"""
    problems = []
    if acct is None:
        account = {"ok": False, "error": acct_err or "账户查询失败"}
        problems.append(f"账户通道不可用（{account['error'][:80]}）——"
                        f"盘中分析/调仓/哨兵条件位会静默停摆，需 RDP 重登通达信交易端")
    else:
        asset = float((acct.get("asset") or {}).get("asset") or 0)
        positions = acct.get("positions") or []
        account = {"ok": asset > 0, "asset": asset, "positions": len(positions)}
        if asset <= 0:
            problems.append(f"账户返回资产 {asset:,.0f}（行情通/账号掉线＝桥假活）——"
                            f"需 RDP 重登通达信交易端")
        else:
            # 快照不可信时不对账：asset=0 的持仓列表必然空，报出来的"漂移"只是
            # 账户掉线的复读（2026-09-10 实况），会把真问题埋进噪音里。
            rec = reconcile(ledger, positions)
            account["ledger"] = rec
            for d in rec["drift"]:
                problems.append(f"台账说 {d['agent']} 持有 {d['code']}，券商快照里没有"
                                f"——分账漂移，条件位会挂空/卖错")
    if not exec_enabled:
        problems.append("自动执行开关为关（configs/intraday_exec.json）——"
                        "哨兵触发只打印不真卖，条件位等于纸面预案")
    rules = {a: len(v) for a, v in (watch or {}).items() if v}
    return {"ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "ok": not problems, "account": account, "exec_enabled": exec_enabled,
            "watch_rules": rules, "problems": problems}


def _account_query() -> tuple[dict | None, str]:
    try:
        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        return TdxBridgeBroker()._account_query(), ""
    except Exception as exc:  # noqa: BLE001 盘前健检失败不抛——写报告是它的职责
        return None, str(exc)


def _ledger() -> dict:
    try:
        from live_ledger import load_ledger

        return load_ledger()
    except Exception:  # noqa: BLE001
        return {"agents": {}}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _notify(res: dict) -> None:
    """有问题 → 追加到告警日志（与 alert.sh 同渠道）。cron 每天只跑一次，无需去重。"""
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(CN_TZ).strftime("%F %T")
        with ALERT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] 盘前健检：{len(res['problems'])} 项问题\n")
            for p in res["problems"]:
                f.write(f"[{stamp}]   ⚠️ {p}\n")
    except OSError as exc:
        print(f"⚠️ 写告警日志失败: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前健检：账户通道/台账对账/执行开关/条件位")
    ap.add_argument("--date", default="")
    ap.add_argument("--force", action="store_true", help="忽略交易日检查（调试）")
    a = ap.parse_args()

    today: date = (date.fromisoformat(a.date) if a.date else datetime.now(CN_TZ).date())
    if not a.force:
        from trading_cal import is_trading_day, why_not

        if not is_trading_day(today):
            print(f"⏭️ {today} 非交易日（{why_not(today)}），跳过盘前健检")
            return 0

    acct, err = _account_query()
    res = evaluate(acct, err, _ledger(),
                   bool(_load(EXEC_CFG, {}).get("enabled")), _load(WATCH_FILE, {}))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)

    if res["ok"]:
        rules = res["watch_rules"]
        detail = " ".join(f"{a}:{n}" for a, n in rules.items()) or "无"
        print(f"✅ 盘前健检通过（资产 ¥{res['account']['asset']:,.0f}，"
              f"持仓 {res['account']['positions']} 只；条件位 {detail}）")
        return 0
    print(f"❌ 盘前健检发现 {len(res['problems'])} 项问题：")
    for p in res["problems"]:
        print(f"   ⚠️ {p}")
    _notify(res)
    return 1


if __name__ == "__main__":
    sys.exit(main())
