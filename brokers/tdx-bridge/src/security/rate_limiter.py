"""
IP 限流中间件 - 防暴力破解 / DDoS

- 每 IP 每分钟最多 N 次请求 (默认 60)
- 写操作 (plans/execute, push/*) 每 IP 每分钟最多 M 次 (默认 10)
- 错误 token 连续 5 次 → 临时封 IP 30 秒
- 超限返回 429 Too Many Requests
"""
import logging
import time
from collections import defaultdict
from typing import Callable

from aiohttp import web

log = logging.getLogger(__name__)

# 写操作路径前缀 (敏感操作, 限流更严)
WRITE_PATHS = ("/api/v1/plans/execute", "/api/v1/orders/cancel",
               "/api/v1/push/", "/api/v1/auth/reset-token")


class RateLimiter:
    def __init__(self, per_minute: int = 60, write_per_minute: int = 10,
                 fail_ban_threshold: int = 5, fail_ban_seconds: int = 30):
        self.per_minute = per_minute
        self.write_per_minute = write_per_minute
        self.fail_ban_threshold = fail_ban_threshold
        self.fail_ban_seconds = fail_ban_seconds
        # ip -> {window_start, count}
        self._windows = defaultdict(lambda: {"ts": 0, "count": 0})
        self._write_windows = defaultdict(lambda: {"ts": 0, "count": 0})
        # 错误 token 计数: ip -> {ts, count}
        self._fail_counts = defaultdict(lambda: {"ts": 0, "count": 0})
        # 被封 IP: ip -> until_ts
        self._banned = {}

    def _check_window(self, bucket, ip: str, limit: int) -> bool:
        now = time.monotonic()
        rec = bucket[ip]
        if now - rec["ts"] > 60:
            rec["ts"] = now
            rec["count"] = 0
        rec["count"] += 1
        return rec["count"] <= limit

    def check(self, ip: str, path: str, auth_ok: bool) -> str | None:
        """检查是否放行. 返回 None=放行, 否则返回错误消息."""
        now = time.monotonic()
        # 被封 IP
        if ip in self._banned:
            if now < self._banned[ip]:
                return "IP 已被临时封禁"
            del self._banned[ip]

        # 写操作单独限流
        is_write = any(path.startswith(p) for p in WRITE_PATHS)
        bucket = self._write_windows if is_write else self._windows
        limit = self.write_per_minute if is_write else self.per_minute
        if not self._check_window(bucket, ip, limit):
            return f"请求过于频繁 (限 {limit}/分钟)"

        # 鉴权失败计数 → 封 IP
        if not auth_ok:
            rec = self._fail_counts[ip]
            if now - rec["ts"] > 60:
                rec["ts"] = now
                rec["count"] = 0
            rec["count"] += 1
            if rec["count"] >= self.fail_ban_threshold:
                self._banned[ip] = now + self.fail_ban_seconds
                rec["count"] = 0
                log.warning(f"[安全] IP {ip} 连续 {self.fail_ban_threshold} 次鉴权失败, 封禁 {self.fail_ban_seconds}s")
                return f"鉴权失败过多, IP 已临时封禁 {self.fail_ban_seconds}s"
        else:
            # 鉴权成功则清零失败计数
            if ip in self._fail_counts:
                self._fail_counts[ip]["count"] = 0

        return None

    def stats(self) -> dict:
        """限流状态统计."""
        return {
            "banned_ips": len(self._banned),
            "active_ips": len(self._windows),
            "write_active": len(self._write_windows),
        }
