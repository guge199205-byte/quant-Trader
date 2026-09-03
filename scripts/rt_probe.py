#!/usr/bin/env python3
"""实时行情源健康看板：桥 / Fuyao / TdxAiData / quantdb 四路状态一次探齐。

输出 logs/rt_status.json（供日报/告警消费）+ 一行摘要。
cron：每 5 分钟（alert 联动前先看这里）。探针失败=该路降级，不影响主流程。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "rt_status.json"


def probe_bridge() -> dict:
    try:
        import os

        env = {}
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TDX_BRIDGE_"):
                k, _, v = line.partition("=")
                env[k] = v.strip().strip('"')
        import urllib.request

        req = urllib.request.Request(
            env.get("TDX_BRIDGE_URL", "http://192.168.31.13:8550") + "/api/v1/tdx/call",
            data=json.dumps({"method": "get_market_snapshot",
                             "params": {"stock_code": "600519.SH"}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + env.get("TDX_BRIDGE_TOKEN", "")},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())
        res = d.get("result") or {}
        now = float(res.get("Now") or 0)
        return {"ok": now > 0, "now": now, "ts": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_fuyao() -> dict:
    try:
        sys.path.insert(0, str(ROOT / "dsh/skills/ths-fuyao/scripts"))
        from ths_fuyao import get

        d = get("/api/a-share/prices/snapshot", {"thscode": "600519.SH"})
        items = (d.get("data") or {}).get("item") or []
        return {"ok": d.get("code") == 0 and len(items) > 0,
                "count_hint": len(items), "error": d.get("message") if d.get("code") else None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_aidata() -> dict:
    try:
        sys.path.insert(0, str(ROOT / "agent_tools"))
        from agent_tools.datasources import tdx_aidata

        return {"ok": bool(tdx_aidata.available()), "dir": str(tdx_aidata.AIDATA_DIR)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_quantdb() -> dict:
    import glob

    for b in (ROOT.parent / "projects/quantmind/data/quantdb/1_kline_data/daily_backward",
              Path("/data/quantdb/1_kline_data/daily_backward")):
        if b.is_dir():
            parts = sorted(glob.glob(f"{b}/dt=*"))
            return {"ok": bool(parts), "latest": parts[-1].rsplit("dt=", 1)[-1] if parts else None}
    return {"ok": False, "error": "quantdb 目录缺失"}


def main() -> int:
    board = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "bridge": probe_bridge(), "fuyao": probe_fuyao(),
        "aidata": probe_aidata(), "quantdb": probe_quantdb(),
    }
    OUT.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [k for k, v in board.items() if k != "ts" and not v.get("ok")]
    print("✅ 行情源: 桥={} Fuyao={} AI数据={} quantdb={}{}".format(
        "OK" if board["bridge"]["ok"] else "DOWN",
        "OK" if board["fuyao"]["ok"] else "DOWN",
        "OK" if board["aidata"]["ok"] else "DOWN",
        "OK" if board["quantdb"]["ok"] else "DOWN",
        f"  ⚠️ 降级: {','.join(bad)}" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())