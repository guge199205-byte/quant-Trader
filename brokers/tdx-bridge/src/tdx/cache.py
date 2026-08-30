"""
内存缓存层 - 保护通达信 17709 不被高频请求压垮

高并发场景: 几百台机器查询 → 桥内存缓存拦截 99% 请求 → 通达信只被"首次/过期"触发

缓存策略:
- 行情快照: 3s TTL
- K线日线: 5min TTL
- 股票信息: 24h TTL
- 财务数据: 24h TTL
- 股票列表/板块: 1h TTL

提供: 缓存读写 / 命中率统计 / 缓存穿透保护(单飞锁)
"""
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

# 单飞锁等待超时 (秒)
INFLIGHT_TIMEOUT = 10.0


class MemoryCache:
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        # 单飞锁: 用条件变量, 等待时不持有锁, 避免死锁
        self._inflight = set()
        self._cond = threading.Condition(self._lock)

    def get(self, key: str, ttl: float) -> tuple[bool, Any]:
        """返回 (是否命中, 值)."""
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry and now - entry["ts"] < ttl:
                self._hits += 1
                return True, entry["value"]
            self._misses += 1
            return False, None

    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = {"ts": time.monotonic(), "value": value}

    def clear(self):
        with self._lock:
            self._data.clear()

    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return round(self._hits / total * 100, 1) if total else 0.0

    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses,
                "hit_rate": self.hit_rate(), "cache_entries": len(self._data),
                "inflight": len(self._inflight)}

    def get_or_fetch(self, key: str, ttl: float, fetch: Callable[[], Any],
                     stale_ok: bool = True) -> Any:
        """缓存读取, 未命中则调用 fetch 获取并缓存.

        stale_ok=True: 通达信不可达时返回过期缓存 (优雅降级).
        使用条件变量单飞锁: 并发请求同 key 时只有一个去上游, 其余等待, 不持锁不死锁.
        """
        # 快速路径: 缓存命中直接返回
        hit, value = self.get(key, ttl)
        if hit:
            return value

        # 单飞: 尝试成为 in-flight 请求
        became_owner = False
        with self._cond:
            if key not in self._inflight:
                self._inflight.add(key)
                became_owner = True
            else:
                # 已有请求在途, 等待它完成 (不持锁)
                self._cond.wait(timeout=INFLIGHT_TIMEOUT)

        if became_owner:
            # 我是 in-flight 请求: 去上游取数据
            try:
                value = fetch()
                self.set(key, value)
                return value
            except Exception:
                # 通达信不可达 → 尝试返回过期缓存 (优雅降级)
                if stale_ok:
                    hit, stale_val = self.get(key, ttl=float("inf"))
                    if hit:
                        log.warning(f"[缓存] {key} 上游失败, 返回过期缓存")
                        return stale_val
                raise
            finally:
                # 释放 in-flight 标记并通知等待者
                with self._cond:
                    self._inflight.discard(key)
                    self._cond.notify_all()
        else:
            # 我是等待者: 被唤醒后重新检查缓存
            hit, value = self.get(key, ttl)
            if hit:
                return value
            # 等待超时仍未拿到, 自己尝试取一次
            try:
                value = fetch()
                self.set(key, value)
                return value
            except Exception:
                if stale_ok:
                    hit, stale_val = self.get(key, ttl=float("inf"))
                    if hit:
                        return stale_val
                raise
