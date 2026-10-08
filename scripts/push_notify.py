#!/usr/bin/env python3
"""QQ 机器人推送（腾讯官方 QQ 机器人 OpenAPI，直连，不依赖 dsh 容器）。

消息链：交易事件 → 本模块 → bots.qq.com → 用户 QQ（C2C 单聊）。
与 dsh 的 dsh-qq 集成（integrations/dsh-qq，2026-09-13 配置）共用同一官方机器人
（appId 1905604147），owner 即本机器人的所有者 openid。

凭据（一律只从 .service.env 读，不落代码/日志）：
  DSH_QQBOT_APP_SECRET_8F253DAF61C7170664F218F8
  OWNER_OPENID = EBDB9B9BB923B3B891E98EA24A62976D（用户本人）

接口：
  POST https://bots.qq.com/app/getAppAccessToken  {appId, clientSecret} → access_token（缓存 100min）
  POST https://api.sgroup.qq.com/v2/users/{openid}/messages  {content, msg_type: 0}

纪律（本模块被交易执行路径调用，绝不阻断交易）：
  - 任何失败只写 logs/push_notify.log，不抛异常
  - 网络超时 8s；令牌失败自动重取一次
  - 股票名称：logs/stock_names.json 缓存 → 缺失时经 quantdb MCP（127.0.0.1:8105）查
    stocks 表（GET 不到名就只报代码，不影响推送）

用法：
  python scripts/push_notify.py send <title> <content>   # 主动发一条
  python scripts/push_notify.py test                     # 发一条测试消息
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_REF = "DSH_QQBOT_APP_SECRET_8F253DAF61C7170664F218F8"
OWNER_OPENID = "EBDB9B9BB923B3B891E98EA24A62976D"
APP_ID = "1905604147"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
# C2C 单聊发送走腾讯开放平台 v2 接口（bots.qq.com/v3 会被 stgw 网关 503 拦掉，
# 2026-09-14 实测；dsh-qq 插件同源用的是 api.sgroup.qq.com）
SEND_URL = "https://api.sgroup.qq.com/v2/users/{openid}/messages"
QUANTDB_MCP = "http://127.0.0.1:8105/mcp"
TOKEN_CACHE = ROOT / "logs" / "qqbot_token.json"
NAME_CACHE = ROOT / "logs" / "stock_names.json"
LOG = ROOT / "logs" / "push_notify.log"
CN_TZ = timezone(timedelta(hours=8))
_TIMEOUT = 8


def _load_env() -> None:
    """读 .service.env / .env（与 live_fills 同口径），不覆盖已有环境变量。"""
    for p in (ROOT / ".service.env", ROOT / ".env"):
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)
        except OSError:
            continue


_load_env()


def _log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(CN_TZ):%F %T} {msg}\n")
    except OSError:
        pass


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


# ---------- 名称解析（缓存 → quantdb MCP → 仅代码） ----------


def _load_name_cache() -> dict:
    try:
        return json.loads(NAME_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_name_cache(cache: dict) -> None:
    try:
        NAME_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _mcp_lookup_name(symbol: str) -> str:
    """经 quantdb MCP（8105）查 stocks 表；任何失败返回 ""（名拿不到不影响推送）。"""
    import requests

    sql = f"SELECT symbol,name FROM stocks WHERE symbol='{symbol}' LIMIT 1"
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    sess_id = ""
    # initialize
    r = requests.post(QUANTDB_MCP, headers=headers, timeout=4,
                      json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "baymax-push-notify",
                                                      "version": "1.0"}}})
    sess_id = r.headers.get("mcp-session-id", "")
    if sess_id:
        headers["mcp-session-id"] = sess_id
    if sess_id:
        requests.post(QUANTDB_MCP, headers=headers, timeout=4,
                      json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r2 = requests.post(QUANTDB_MCP, headers=headers, timeout=4,
                       json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "query_quantdb",
                                        "arguments": {"sql": sql}}})
    # SSE/流式响应缺 charset 时 requests .text 会按 latin-1 解 → 中文名乱码
    # （2026-09-14 实测：缓存里存进 é¦æ©è¡ä»½ 这类字节）。必须按 UTF-8 显式解码。
    text = r2.content.decode("utf-8", errors="replace")
    if "data:" in text:  # SSE 响应时取最后一个 data 行
        text = text.rstrip().rsplit("data:", 1)[-1].strip()
    payload = json.loads(text)
    content = (payload.get("result") or {}).get("content") or []
    for block in content:
        txt = str(block.get("text") or "")
        try:
            rows = json.loads(txt)
        except ValueError:
            continue
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return str(rows[0].get("name") or "")
    return ""


def _repair_name(name: str) -> str:
    """把 latin-1 误解码的中文名修回来（UTF-8 字节被按 latin-1 解成了两段乱码）。

    只在名称全部落在 latin-1 区间时尝试重编码；真正的外文名重解码失败则原样返回。
    """
    try:
        fixed = name.encode("latin-1").decode("utf-8")
        return fixed if fixed != name else name
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def stock_name(symbol: str) -> str:
    """股票中文名（缓存 → quantdb → ''）。失败不影响调用方。"""
    if not symbol:
        return ""
    cache = _load_name_cache()
    if symbol in cache:
        return _repair_name(cache[symbol])
    name = ""
    try:
        name = _mcp_lookup_name(symbol)
    except Exception as exc:  # noqa: BLE001
        _log(f"名称查询失败 {symbol}: {exc}")
    name = _repair_name(name)
    if name and name != "None":
        cache[symbol] = name
        _save_name_cache(cache)
    return name or ""


# ---------- 令牌与发送 ----------


def _get_token() -> str:
    secret = os.environ.get(SECRET_REF) or ""
    if not secret:
        raise RuntimeError(f"缺少 {SECRET_REF}（请写入 .service.env）")
    cached = None
    try:
        if TOKEN_CACHE.exists():
            cached = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    if cached and float(cached.get("expires_at") or 0) > time.time() + 120:
        return str(cached["token"])
    import requests

    r = requests.post(TOKEN_URL, timeout=_TIMEOUT,
                      json={"appId": APP_ID, "clientSecret": secret})
    r.raise_for_status()
    data = r.json()
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"机器人令牌接口未返回 access_token: {str(data)[:200]}")
    try:
        TOKEN_CACHE.write_text(json.dumps({
            "token": token,
            "expires_at": time.time() + float(data.get("expires_in") or 7200) - 60,
        }), encoding="utf-8")
    except OSError:
        pass
    return token


def send_text(content: str) -> dict:
    """发一条 C2C 纯文本给所有者；失败抛异常（调用方自行兜底）。"""
    import requests

    token = _get_token()
    r = requests.post(
        SEND_URL.format(openid=OWNER_OPENID), timeout=_TIMEOUT,
        headers={"Authorization": f"QQBot {token}",  # v2 接口要求 QQBot 前缀（Bearer 会 11241）
                 "Content-Type": "application/json"},
        json={"content": content, "msg_type": 0,
              "msg_seq": int(time.time() * 1000) % (2**31)})
    r.raise_for_status()
    return r.json()


def send_markdown(content: str) -> dict:
    """发一条 C2C markdown（**加粗**等富文本）；失败抛异常（调用方自行兜底）。"""
    import requests

    token = _get_token()
    r = requests.post(
        SEND_URL.format(openid=OWNER_OPENID), timeout=_TIMEOUT,
        headers={"Authorization": f"QQBot {token}",
                 "Content-Type": "application/json"},
        json={"msg_type": 2, "markdown": {"content": content}})
    r.raise_for_status()
    return r.json()


def bold(s) -> str:
    """markdown 加粗（文本降级路径会剥掉 **）"""
    return f"**{s}**"


def _strip_bold(s: str) -> str:
    return str(s).replace("**", "")


def notify(title: str, content: str) -> None:
    """最外层安全网：发 QQ 通知，任何失败只写日志，绝不影响主流程。

    排版约定：标题一行说完「动作+标的+量价」（▲买/▼卖），正文每行一个字段
    （原因/止损止盈/委托/余额/T+1/账号）；股票名、金额、价位等关键信息加粗
    （markdown）；markdown 被拒/不可用时自动剥掉 ** 降级纯文本重发。
    """
    content = "\n".join(line for line in content.splitlines() if line.strip())
    md = f"{title}\n\n{content}"
    if len(md) > 1600:  # markdown 消息上限兜底（正常推送远小于此）
        md = md[:1590] + "…"
    try:
        resp = send_markdown(md)
    except Exception as exc:  # noqa: BLE001
        _log(f"QQ markdown 推送失败，降级纯文本: {title} → {exc}")
        try:
            resp = send_text(_strip_bold(md))
        except Exception as exc2:  # noqa: BLE001
            _log(f"QQ 推送失败: {title} → {exc2}")
            return
    _log(f"已发送 QQ: {title}（{str(resp)[:160]}）")


# ---------- 交易事件格式化 ----------


def _ledger_cash(agent: str) -> float | None:
    try:
        d = json.loads((ROOT / "logs" / "live_ledger.json").read_text(encoding="utf-8"))
        return float((d.get("agents") or {}).get(agent, {}).get("virtual_cash") or 0)
    except (OSError, ValueError, TypeError):
        return None


def _ledger_hold_volume(agent: str, symbol: str) -> int:
    try:
        d = json.loads((ROOT / "logs" / "live_ledger.json").read_text(encoding="utf-8"))
        pos = ((d.get("agents") or {}).get(agent, {}).get("positions") or {}).get(symbol) or {}
        return int(pos.get("volume") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _clamp_reason(text: str, limit: int = 300) -> str:
    """原因截断：优先在分句边界（、，。；！？）收尾，不从半句中间腰斩。

    上限 300 字（2026-09-15 用户口径：原因不能被切在「30min动量…」半句上；
    本系统 agent 证据链普遍 100-250 字 → 基本全量显示）。超限才在 limit 内
    最后一个标点收口；毫无标点的长文做硬截。
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    idx = max(window.rfind(ch) for ch in "、，。；！？,;.")
    if idx >= limit // 2:            # 边界别收得太靠前（至少留半窗内容）
        return window[: idx + 1] + "…"
    return window + "…"


def notify_order(*, side: str, symbol: str, volume: int, price: float | None = None,
                 agent: str = "", reason: str = "", order_id: str = "",
                 held_before: int | None = None, source: str = "",
                 stop_loss: float | None = None,
                 take_profit: float | None = None) -> None:
    """下单成功事件 → QQ（▲买/▼卖，正文含原因/止损止盈/委托/余额/T+1/账号）。"""
    side_label = "买入" if side == "buy" else "卖出"
    icon = "▲" if side == "buy" else "▼"
    name = stock_name(symbol)
    sym = f"{name}({symbol})" if name else symbol
    price_txt = f" @¥{price:.2f}" if price else ""
    held = held_before if held_before is not None else _ledger_hold_volume(agent, symbol)
    clearing = side == "sell" and held and volume >= held
    act = f"{icon} {bold('清仓') if clearing else side_label} " \
          f"{bold(sym)} {bold(f'{volume}股{price_txt}')}".strip()
    body = []
    reason = (reason or "").strip()
    body.append(f"原因：{_clamp_reason(reason)}" if reason else "原因：—")
    meta = []
    if stop_loss or take_profit:
        sl = f"止损 {bold(f'¥{stop_loss:.2f}')}" if stop_loss else "止损 —"
        tp = f"止盈 {bold(f'¥{take_profit:.2f}')}" if take_profit else "止盈 —"
        meta.append(f"{sl} ｜ {tp}")
    if order_id:
        meta.append(f"委托 {bold(order_id)}")
    cash = _ledger_cash(agent)
    if cash is not None:
        meta.append(f"余额 {bold(f'¥{cash:,.0f}')}")
    if meta:
        body.append(" ｜ ".join(meta))
    if side == "buy":
        body.append("T+1：今日买入，明日可卖")
    who = f"{agent}" + (f"（{source}）" if source else "")
    if who:
        body.append(f"账号：{who}")
    notify(act, "\n".join(body))


def main() -> int:
    ap = argparse.ArgumentParser(description="BayMax QQ 通知")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="主动发送一条通知")
    s.add_argument("title")
    s.add_argument("content")
    sub.add_parser("test", help="发送测试消息")
    args = ap.parse_args()
    if args.cmd == "test":
        notify("通知通道测试 ✅",
               f"BayMax 已接通 QQ 机器人通知（{_now_cn():%F %T}）\n"
               "之后买入/卖出/清仓、调仓轮失败、Windows/桥离线都会推送到这里。")
    else:
        notify(args.title, args.content)
    return 0


if __name__ == "__main__":
    sys.exit(main())