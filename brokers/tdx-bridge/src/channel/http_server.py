import logging
from aiohttp import web

from ..api.routes import build_app
from ..tdx.client import TdxClient
from ..executor.plan_executor import PlanExecutor
from ..executor.order_tracker import OrderTracker

log = logging.getLogger(__name__)


async def start_http_server(host: str, port: int, token: str, tdx: TdxClient,
                            executor: PlanExecutor, tracker: OrderTracker, sltp,
                            cache_db=None, extra_tokens=None, rate_limiter=None,
                            memory_cache=None):
    app = build_app(token, tdx, executor, tracker, sltp, cache_db=cache_db, port=port,
                    extra_tokens=extra_tokens, rate_limiter=rate_limiter,
                    memory_cache=memory_cache)
    # 2026-09-01 事故：异常请求中止留下的 CLOSE_WAIT 堆积会吃掉监听队列，
    # 出现"局域网连不上、本机正常"的假死。缩短 keep-alive + TCP keepalive
    # 主动回收半关闭连接（旧版 aiohttp 无这些参数时忽略不升级）。
    try:
        runner = web.AppRunner(app, keepalive_timeout=30, tcp_keepalive=True)
    except TypeError:  # 旧版 aiohttp
        runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info(f"HTTP 桥已监听 http://{host}:{port}")
    return runner
