#!/usr/bin/env python3
"""QMT 桥只读体检：连上 Windows 大 QMT，查账户/持仓/委托/成交——**不下任何单**。

用途：阶段一验收。链路配置好后先跑它，确认「能连、能查、数对得上」，
再谈接执行。失败信息尽量给到"下一步该查什么"。

用法：
  /home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py            # 只读体检
  /home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py --json     # 机器可读（日报/告警消费）
  # 临时覆盖（不落盘，便于联调）：
  /home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py \
      --account-id 123456 --redis-host 10.0.0.1 --redis-port 6380 --redis-password '***'

配置：config/qmt_bridge.json（不入库）或环境变量 QMT_EXEC_*；见 qmt_bridge.py 头注释。
"""
import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_tools.brokers.base import BrokerError  # noqa: E402
from agent_tools.brokers.qmt_bridge import QmtBridgeBroker, qmt_overrides  # noqa: E402

CN_TZ = ZoneInfo("Asia/Shanghai")


def _mask(account: str) -> str:
    return f"***{account[-4:]}" if len(account) > 4 else "***"


def _config_source() -> str:
    """说明关键项来自哪里——排错时第一眼要看的信息。"""
    keys = ("account_id", "redis_host")
    ov = qmt_overrides()
    if not any(ov.get(k) for k in keys):
        return "环境变量/命令行"
    missing = "、".join(k for k in keys if not ov.get(k))
    if not missing:
        return "config/qmt_bridge.json"
    return f"config/qmt_bridge.json（{missing} 未填，该值走环境变量/命令行）"


def collect(broker: QmtBridgeBroker) -> dict:
    """只读五连查：ping → 资产 → 持仓 → 委托 → 成交。任一失败记入 errors 而非抛出。"""
    out: dict = {"ok": True, "errors": []}
    try:
        broker.ping()
        out["ping"] = True
    except BrokerError as exc:
        out["ping"] = False
        out["ok"] = False
        out["errors"].append(str(exc))
        return out                     # ping 不通，后面的查询没有意义
    try:
        acct = broker._account_query()
        out["asset"] = acct.get("asset")
        out["positions"] = acct.get("positions")
    except BrokerError as exc:
        out["ok"] = False
        out["errors"].append(str(exc))
    try:
        out["orders"] = broker.get_orders()
    except BrokerError as exc:
        out["ok"] = False
        out["errors"].append(str(exc))
    try:
        out["trades"] = broker.get_trades()
    except BrokerError as exc:
        out["ok"] = False
        out["errors"].append(str(exc))
    return out


def render(broker: QmtBridgeBroker, res: dict) -> str:
    lines = [f"QMT 桥只读体检 · 账号 {_mask(broker.account_id)}"
             f"（{broker.account_type}） @ {broker.redis['host']}:{broker.redis['port']}"
             f"/db{broker.redis['db']}"]
    lines.append(f"  配置来源：{_config_source()}")
    if not res.get("ping"):
        lines.append("  ❌ ping 不通——桥没在听。检查：")
        lines.append("     1) Windows 上 QMT 是否开着、策略编辑器里 BIGQMT_REDIS_DRYRUN.py 是否在运行")
        lines.append("        （QMT 重启后要重新运行该策略；输出面板应有 [bigqmt_rpc] started ...）")
        lines.append("     2) 本机能否连到桥 Redis（host/port/密码），防火墙是否放行")
        lines.extend(f"  └ {e}" for e in res.get("errors") or [])
        return "\n".join(lines)

    lines.append("  ✅ ping")
    asset = res.get("asset") or {}
    if asset:
        lines.append(f"  账户：总资产 ¥{asset.get('asset', 0):,.0f}   "
                     f"可用 ¥{asset.get('cash', 0):,.0f}   "
                     f"市值 ¥{asset.get('market_value', 0):,.0f}")
    pos = res.get("positions") or []
    lines.append(f"  持仓（{len(pos)}）")
    for p in pos:
        lines.append(f"    {p['stock_code']} {p.get('stock_name', ''):<6} "
                     f"{p['total_volume']:>8.0f} 股（可用 {p['available_volume']:>8.0f}）"
                     f"  成本 {p['cost_price']:>8.2f}  现价 {p['last_price']:>8.2f}"
                     f"  市值 ¥{p['market_value']:,.0f}")
    for key, label in (("orders", "当日委托"), ("trades", "当日成交")):
        if key in res:
            lines.append(f"  {label}（{len(res[key])}）")
            for o in res[key]:
                lines.append("    " + json.dumps(o, ensure_ascii=False))
    for e in res.get("errors") or []:
        lines.append(f"  ⚠️ {e}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="QMT 桥只读体检（不下单）")
    ap.add_argument("--account-id", default="")
    ap.add_argument("--account-type", default="")
    ap.add_argument("--redis-host", default="")
    ap.add_argument("--redis-port", type=int, default=0)
    ap.add_argument("--redis-db", type=int, default=-1)
    ap.add_argument("--redis-password", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg = {k: v for k, v in {
        "account_id": a.account_id, "account_type": a.account_type,
        "redis_host": a.redis_host, "redis_port": a.redis_port or None,
        "redis_db": a.redis_db if a.redis_db >= 0 else None,
        "redis_password": a.redis_password,
    }.items() if v not in (None, "")}
    try:
        broker = QmtBridgeBroker(cfg)
    except BrokerError as exc:
        print(f"❌ {exc}")
        print("   配置方法：建 config/qmt_bridge.json（含 redis_password，不入库），"
              "或设 QMT_EXEC_ACCOUNT_ID / QMT_EXEC_REDIS_HOST / QMT_EXEC_REDIS_PASSWORD")
        return 2

    res = collect(broker)
    if a.json:
        print(json.dumps({"account_id": _mask(broker.account_id), **res},
                         ensure_ascii=False, indent=1))
    else:
        print(render(broker, res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
