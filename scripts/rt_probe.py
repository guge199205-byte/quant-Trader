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
if str(ROOT / "scripts") not in sys.path:     # 直接 import live_quote（同目录）
    sys.path.insert(0, str(ROOT / "scripts"))


def _env_kv(prefix: str = "") -> dict:
    """读 .env 键值。读失败返回空 dict——探针不能因为 .env 不可读就整个停更
    （rt_status.json 停更时 alert.sh 的 2d 会静默跳过 = fail-open）。"""
    out: dict = {}
    try:
        text = (ROOT / ".env").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        if prefix and not line.startswith(prefix):
            continue
        k, _, v = line.partition("=")
        if k.strip():
            out[k.strip()] = v.strip().strip('"')
    return out


def _bridge_urls() -> list:
    """候选桥地址：运行时覆盖（config/tdx_bridge.json，broker 断线自动发现
    会写这里）优先，其次 .env 声明地址——IP 变动自愈后看板不误报 DOWN。"""
    out: list = []
    try:
        ov = json.loads((ROOT / "config" / "tdx_bridge.json").read_text(encoding="utf-8"))
        u = str(ov.get("bridge_url") or "").rstrip("/")
        if u:
            out.append(u)
    except (OSError, json.JSONDecodeError):
        pass
    u = str(_env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_URL", "")).rstrip("/") \
        or "http://192.168.31.13:8550"
    if u not in out:
        out.append(u)
    return out


def probe_bridge() -> dict:
    token = _env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_TOKEN", "")
    import urllib.request

    last_err = "无候选桥地址"
    for url in _bridge_urls():
        try:
            req = urllib.request.Request(
                url + "/api/v1/tdx/call",
                data=json.dumps({"method": "get_market_snapshot",
                                 "params": {"stock_code": "600519.SH"}}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + token},
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
            res = d.get("result") or {}
            now = float(res.get("Now") or 0)
            if now > 0:
                return {"ok": True, "now": now, "via": url,
                        "ts": datetime.now().isoformat(timespec="seconds")}
        except Exception as e:  # noqa: BLE001 单地址失败继续探下一候选
            last_err = str(e)[:120]
    return {"ok": False, "error": last_err}


def probe_fuyao() -> dict:
    """Fuyao 探活 = live_quote 的同一函数：**目标行真的匹配到有效价**才算 OK。

    2026-09-11 前只看 `len(items)>0`：上游忽略 thscode、返回全市场别家行情也判健康
    （day 里桥断时备胎恒给平安银行价，看板却全绿）。条数降级为 count_hint 提示。
    """
    try:
        import live_quote

        return live_quote.fuyao_check("600519.SH")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_aidata() -> dict:
    # TdxAiData 实际跑在 Windows 侧（.so 无法在 Linux 加载）：
    # 判据 = 客户端文件在位（共享目录镜像 / 本机默认目录），盘中健康看分钟特征
    import os as _os

    cands = [Path("/mnt/tdx-shared/TdxAiData"), Path("/opt/tdx-aidata")]
    files_ok = any((p / "TdxAiData.ini").is_file() and (p / "libTdxAiData.so").is_file()
                   for p in cands)
    return {"ok": files_ok, "dirs": [str(p) for p in cands if p.is_dir()]}


def probe_tencent() -> dict:
    """腾讯行情（免 key 公开单只，第三备胎：现价/开高低/量/五档简化）。"""
    try:
        import urllib.request

        with urllib.request.urlopen(
                "http://qt.gtimg.cn/q=sh600519", timeout=5) as r:
            raw = r.read().decode("gbk", "ignore")
        f = raw.split("=", 1)[1].split("~") if "=" in raw else []
        price = float(f[3]) if len(f) > 3 else 0
        return {"ok": price > 0, "now": price}
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


def probe_account() -> dict:
    """账户通道探针（与行情通道分开的一条链路）。

    2026-09-10 实录：桥行情侧全通（快照/K线正常），account/query 却整日返
    asset=0、positions=[]——交易账号掉线。当日 7 轮盘中分析 + 3 次 09:35 调仓
    + 全部哨兵条件位静默哑火，而看板只探行情 → 零告警，收盘后复盘才发现。
    判据与 live_llm_trade._query_account_with_retry 同口径：asset > 0 才算通。
    """
    token = _env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_TOKEN", "")
    env = _env_kv()
    body = {"account": env.get("TDX_ACCOUNT", ""),
            "account_type": env.get("TDX_ACCOUNT_TYPE", "") or "tdx"}
    import urllib.request

    last_err = "无候选桥地址"
    for url in _bridge_urls():
        try:
            req = urllib.request.Request(
                url + "/api/v1/account/query",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + token},
                method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read().decode())
            res = d.get("result") if isinstance(d.get("result"), dict) else d
            asset = float((res.get("asset") or {}).get("asset") or 0)
            n_pos = len(res.get("positions") or [])
            if asset > 0:
                return {"ok": True, "asset": asset, "positions": n_pos, "via": url,
                        "ts": datetime.now().isoformat(timespec="seconds")}
            last_err = f"asset={asset:,.0f} positions={n_pos}（行情通/账号掉线＝桥假活）"
        except Exception as e:  # noqa: BLE001 单地址失败继续探下一候选
            last_err = str(e)[:120]
    return {"ok": False, "error": last_err}


SOURCES = ("bridge", "fuyao", "tencent", "aidata", "quantdb", "account")


def _prev_board() -> dict:
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def apply_streaks(board: dict, prev: dict, ts: str) -> dict:
    """把「连续失败次数 / 首次失败时刻」并进看板（纯函数，便于测试）。

    单点 DOWN 多为 5-10 分钟自愈的抖动（fuyao 一天十几次），告警侧据此去抖；
    没有 streak 就只能「一次失败就报」→ 刷屏，或者不报 → 漏真故障。
    """
    for k in SOURCES:
        v = board.get(k)
        if not isinstance(v, dict):
            continue
        p = prev.get(k) if isinstance(prev.get(k), dict) else {}
        if v.get("ok"):
            v["fail_streak"] = 0
            v.pop("first_fail_ts", None)
        else:
            v["fail_streak"] = int(p.get("fail_streak") or 0) + 1
            v["first_fail_ts"] = p.get("first_fail_ts") or ts
    return board


def _cell(board: dict, key: str, label: str) -> str:
    v = board.get(key) or {}
    if v.get("ok"):
        return f"{label}=OK"
    err = str(v.get("error") or "").strip()
    detail = f"{v.get('fail_streak') or 0}次" + (f"/{err[:60]}" if err else "")
    return f"{label}=DOWN({detail})"


def main() -> int:
    ts = datetime.now().isoformat(timespec="seconds")
    board = {
        "ts": ts,
        "bridge": probe_bridge(), "fuyao": probe_fuyao(), "tencent": probe_tencent(),
        "aidata": probe_aidata(), "quantdb": probe_quantdb(),
        "account": probe_account(),
    }
    apply_streaks(board, _prev_board(), ts)
    tmp = OUT.with_name(OUT.name + ".tmp")   # 原子写：alert.sh 可能正在读
    tmp.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    bad = [k for k in SOURCES if not (board.get(k) or {}).get("ok")]
    cells = " ".join(_cell(board, k, lab) for k, lab in
                     (("bridge", "桥"), ("fuyao", "Fuyao"), ("tencent", "腾讯"),
                      ("aidata", "AI数据"), ("quantdb", "quantdb"), ("account", "账户")))
    print(f"✅ 行情源: {cells}" + (f"  ⚠️ 降级: {','.join(bad)}" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())