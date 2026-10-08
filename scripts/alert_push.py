#!/usr/bin/env python3
"""alert.sh 告警 → QQ 转发器（2026-09-18）。

背景（2026-09-18 实录）：alert.sh 的收尾只把 ALERTS 写
/home/zbox/backups/baymax/alerts.log 与可选 WEBHOOK；ALERT_WEBHOOK 未配置时，
全部告警——两笔止损卖单被柜台判废、"需人工核对"、新闻停更 19 小时、因子库滞后
6 天——静默躺在本地日志里，QQ 一条都没收到；收到的反而是两条"下单即发"的
「清仓」误报。本模块把告警转发到 QQ（与买卖通知同渠道）。

口径（用户 2026-09-18）：
  - 成功才推送、失败必推原因——对告警转发而言，"成功"= 消息送达 QQ；
  - 指纹去重 + TTL：文本里的数字（分钟数/金额/委托号）归一为 `#` 后取指纹，
    同指纹 TTL（默认 2h）内只发一次，防止"每 5 分钟一轮、分钟数递增"的持续
    故障刷屏；指纹变化（新问题）立即发；
  - 发送失败绝不阻断 alert.sh：消息本身已落 alerts.log，这里只写 stderr 并退 0。

用法: python scripts/alert_push.py "<message>"    # 或以 stdin 传入
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "logs" / "alert_push_state.json"
TTL_SEC = 2 * 3600
MAX_LEN = 1800


def fingerprint(msg: str) -> str:
    """指纹 = 全部数字归一为 `#` 后的空白归一化文本。

    "新闻分子停更 1144 分钟" 与 "…1149 分钟" 指纹相同（同一持续故障）；
    委托号/金额变动同理。指纹不同 = 新问题，立即放行。
    """
    text = re.sub(r"\d+", "#", str(msg or ""))
    return re.sub(r"\s+", " ", text).strip()[:400]


def should_push(state: dict, fp: str, now: float, ttl: float = TTL_SEC) -> bool:
    """同指纹在 ttl 秒内已发过 → False；否则 True（含首次）。"""
    last = state.get(fp)
    if not isinstance(last, (int, float)):
        return True
    return (now - last) >= ttl


def prune(state: dict, now: float, keep: float = 24 * 3600) -> dict:
    """清掉 24h 前的指纹记录（与 alert.sh 的事件保留期同量级）。"""
    return {k: v for k, v in state.items()
            if isinstance(v, (int, float)) and (now - v) < keep}


def main() -> int:
    msg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    msg = (msg or "").strip()
    if not msg:
        return 0
    now = time.time()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    fp = fingerprint(msg)
    if not should_push(state, fp, now):
        print(f"[alert_push] 同指纹 {TTL_SEC // 3600}h 内已推送过，跳过")
        return 0
    body = msg if len(msg) <= MAX_LEN else msg[:MAX_LEN] + "…"
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from push_notify import notify

        notify("🔔 交易告警", body)
    except Exception as exc:  # noqa: BLE001  推送失败不能反过来吞掉告警
        print(f"[alert_push] QQ 推送失败（告警已落 alerts.log）: {exc}", file=sys.stderr)
        return 0
    # 先发后记：发送成功才写指纹（写盘失败最坏等于下次重发一条，方向安全）
    state[fp] = now
    state = prune(state, now)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass
    print("[alert_push] 已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
