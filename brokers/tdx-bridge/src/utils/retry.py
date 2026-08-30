import functools
import logging
import random
import time

log = logging.getLogger(__name__)


def with_retry(max_retries: int = 3, base_delay: float = 1.0,
               max_delay: float = 30.0, backoff_factor: float = 2.0,
               jitter: bool = True, retryable_exceptions=(
                   ConnectionError, TimeoutError, OSError)):
    """指数退避重试装饰器(同步)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last = e
                    if attempt == max_retries:
                        log.error(f"{func.__name__} 重试{max_retries}次后仍失败: {e}")
                        raise
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()
                    log.warning(f"{func.__name__} 第{attempt + 1}次失败: {e}, {delay:.1f}s 后重试")
                    time.sleep(delay)
            raise last
        return wrapper
    return decorator
