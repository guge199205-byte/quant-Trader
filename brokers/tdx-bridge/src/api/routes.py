import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from aiohttp import web

from ..core.trade_plan import TradePlan
from ..core.types import OrderStatus, Side
from ..executor.order_tracker import OrderTracker
from ..executor.plan_executor import PlanExecutor
from ..tdx.client import TdxClient, TdxError
from ..tdx import parser
from ..utils.auth import verify_token

log = logging.getLogger(__name__)

# 通达信调用超时 (秒) - 防止单个阻塞调用挂死整个桥
TDX_CALL_TIMEOUT = 8.0

# stats 账户缓存 (避免网页高频轮询压垮通达信)
_stats_cache = {"ts": 0, "account": None}

# 通达信并发信号量: 延迟初始化 (绑定运行中的 event loop, 避免 loop 冲突)
_tdx_semaphore = None


def _get_semaphore():
    global _tdx_semaphore
    if _tdx_semaphore is None:
        _tdx_semaphore = asyncio.Semaphore(4)
    return _tdx_semaphore


async def _call_tdx(func, *args):
    """在后台线程跑通达信调用, 带超时 + 并发限制, 防止阻塞事件循环."""
    async with _get_semaphore():
        try:
            return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=TDX_CALL_TIMEOUT)
        except asyncio.TimeoutError:
            raise TdxError("通达信响应超时")


def json_error(status: int, code: str, message: str, details=None):
    return web.json_response({"success": False,
                              "error": {"code": code, "message": message,
                                        "details": details or {}}},
                             status=status)


def _cache_result(cache_db, method: str, symbol: str, params: dict, result: dict):
    """把通达信查询结果写入 SQLite 缓存 (尽力而为, 失败不影响主流程)."""
    try:
        if method == "get_market_data":
            # 兼容新旧结构: 新版 Value[symbol], 旧版顶层 Close
            if isinstance(result.get("Value"), dict) and symbol in result["Value"]:
                kline = result["Value"][symbol]
            else:
                kline = result
            closes = kline.get("Close") or []
            if closes:
                dates = kline.get("Date") or [""] * len(closes)
                bars = [{"date": str(dates[i]) if i < len(dates) else "",
                         "open": _at(kline.get("Open"), i), "high": _at(kline.get("High"), i),
                         "low": _at(kline.get("Low"), i), "close": closes[i],
                         "volume": _at(kline.get("Volume"), i), "amount": _at(kline.get("Amount"), i)}
                        for i in range(len(closes))]
                cache_db.save_kline(symbol, params.get("period", "1d"), bars)
        elif method == "get_stock_info":
            cache_db.save_stock_info(symbol, result)
        elif method == "get_market_snapshot":
            cache_db.save_snapshot(symbol, result)
        elif method == "get_financial_data":
            cache_db.save_financial(symbol, params.get("report_type", "report_time"), result)
    except Exception:
        log.warning("缓存写入失败(忽略)", exc_info=True)


def _at(lst, i):
    try:
        return lst[i] if lst and i < len(lst) else None
    except Exception:
        return None


def _run_tdx_sync(tdx, method: str, params: dict) -> dict:
    """同步执行通达信调用 (供缓存 get_or_fetch 的 fetch 回调用)."""
    return tdx.call(method, params or {})


def _extract_symbol(method: str, params: dict) -> str:
    """从参数提取股票代码 (兼容 stock_code/symbol/stock_list)."""
    if not isinstance(params, dict):
        return ""
    symbol = params.get("stock_code") or params.get("symbol") or ""
    if not symbol:
        stock_list = params.get("stock_list") or []
        symbol = stock_list[0] if stock_list else ""
    return symbol


def _record_call(cache_db, method: str, params: dict, result=None, duration_ms=None,
                 error: str = "", category: str = "", side: str = "", volume=None,
                 price=None, order_id="", status=""):
    """精确记录一次桥调用到 SQLite (尽力而为)."""
    if not cache_db:
        return
    try:
        symbol = _extract_symbol(method, params)
        cache_db.log_call(method, symbol, params, result, category=category,
                          side=side, volume=volume, price=price,
                          order_id=order_id, status=status,
                          duration_ms=duration_ms, error=error)
    except Exception:
        log.warning("记录调用日志失败(忽略)", exc_info=True)


def _record_trade(cache_db, plan_id, event, symbol, side, volume, price,
                  order_id, status, message="", detail=None):
    """精确记录一次交易事件到 SQLite."""
    if not cache_db:
        return
    try:
        cache_db.log_trade(plan_id, event, symbol, side, volume, price,
                           order_id, status, message, detail)
    except Exception:
        log.warning("记录交易日志失败(忽略)", exc_info=True)


def build_app(token: str, tdx: TdxClient, executor: PlanExecutor,
              tracker: OrderTracker, sltp, cache_db=None, port: int = 8550,
              extra_tokens: list = None, rate_limiter=None,
              memory_cache=None) -> web.Application:
    app = web.Application()
    # 支持多 token: 主 token + 扩展 tokens
    valid_tokens = [token] + [t for t in (extra_tokens or []) if t]

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        path = request.path
        client_ip = request.remote or "unknown"

        # 请求体大小限制 (防超大 payload 攻击)
        if request.headers.get("Content-Length"):
            try:
                if int(request.headers["Content-Length"]) > 512 * 1024:
                    return json_error(413, "PAYLOAD_TOO_LARGE", "请求体过大 (限 512KB)")
            except ValueError:
                return json_error(400, "INVALID_CONTENT_LENGTH", "Content-Length 非法")

        # Content-Type 白名单 (POST 请求必须 JSON)
        if request.method == "POST":
            ct = (request.headers.get("Content-Type", "") or "").lower()
            if ct and not ct.startswith("application/json"):
                return json_error(415, "UNSUPPORTED_MEDIA_TYPE", "仅支持 application/json")

        # 只读状态/页面豁免鉴权 (仅展示, 不含写操作)
        read_only = ("/api/v1/health", "/api/v1/stats", "/ui", "/favicon.ico",
                     "/api/v1/cache/kline", "/api/v1/cache/logs",
                     "/api/v1/cache/trades", "/api/v1/cache/get")
        if path in read_only or path.startswith("/api/v1/cache/"):
            if rate_limiter:
                # 只读接口也限流 (防 DDoS)
                err = rate_limiter.check(client_ip, path, True)
                if err:
                    return json_error(429, "RATE_LIMITED", err)
            return await handler(request)

        # 鉴权: 主 token + extra_tokens + 数据库动态 token
        auth = request.headers.get("Authorization", "")
        provided = auth.removeprefix("Bearer ").strip() if auth else ""
        auth_ok = any(verify_token(t, provided) for t in valid_tokens)
        if not auth_ok and cache_db:
            # 数据库 token 校验 (动态增删实时生效)
            auth_ok = await asyncio.to_thread(cache_db.is_token_valid, provided)

        # 限流 (含鉴权失败计数 → 封 IP)
        if rate_limiter:
            err = rate_limiter.check(client_ip, path, auth_ok)
            if err:
                return json_error(429, "RATE_LIMITED", err)

        if not auth_ok:
            return json_error(401, "AUTH_FAILED", "无效或缺失 token")
        return await handler(request)

    app.middlewares.append(auth_middleware)

    async def health(request: web.Request):
        tdx_connected = await asyncio.to_thread(tdx.health_check_fast, 1.0)
        return web.json_response({
            "status": "ok",
            "tdx_connected": tdx_connected,
            "server_time": datetime.now().isoformat(timespec="seconds"),
        })

    async def execute_plan(request: web.Request):
        try:
            data = await request.json()
            plan = TradePlan.from_dict(data)
        except (ValueError, KeyError, TypeError) as e:
            return json_error(400, "PLAN_VALIDATION_ERROR", f"非法计划: {e}")
        if not plan.orders:
            return json_error(400, "PLAN_VALIDATION_ERROR", "计划为空")
        for o in plan.orders:
            if o.volume <= 0 or not o.stock_code:
                return json_error(400, "PLAN_VALIDATION_ERROR",
                                  f"非法订单: {o.stock_code} 数量 {o.volume}")
            if o.side not in ("buy", "sell"):
                return json_error(400, "PLAN_VALIDATION_ERROR", f"非法方向: {o.side}")
        try:
            report = await _call_tdx(executor.execute_plan, plan)
            if report["status"] == "duplicate":
                return json_error(409, "DUPLICATE_PLAN", report.get("message", "重复计划"))
            report["channel_used"] = "http"
            # 精确记录每笔订单到 SQLite
            for o in report.get("orders", []):
                _record_trade(cache_db, plan.plan_id, "order_execute",
                              o.get("stock_code", ""), o.get("side", ""),
                              o.get("volume"), None, o.get("order_id", ""),
                              o.get("status", ""), o.get("message", ""), detail=o)
            return web.json_response(report)
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def query_account(request: web.Request):
        data = await request.json()
        account_id = await _call_tdx(executor.resolve_account_id,
                                     data.get("account", ""), data.get("account_type", "stock"))
        try:
            def _do():
                asset = parser.parse_asset(tdx.query_stock_asset(account_id))
                positions = [parser.parse_position(p)
                             for p in tdx.query_stock_positions(account_id)]
                return asset, positions
            asset, positions = await _call_tdx(_do)
            return web.json_response({"account_id": account_id, "asset": asset,
                                      "positions": positions, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def query_orders(request: web.Request):
        data = await request.json()
        account_id = await _call_tdx(executor.resolve_account_id,
                                     data.get("account", ""), data.get("account_type", "stock"))
        try:
            def _do():
                return [parser.parse_order(o)
                        for o in tdx.query_stock_orders(
                            account_id, data.get("stock_code", ""),
                            data.get("cancelable_only", False))]
            orders = await _call_tdx(_do)
            return web.json_response({"orders": orders, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def cancel_order(request: web.Request):
        data = await request.json()
        account_id = await _call_tdx(executor.resolve_account_id,
                                     data.get("account", ""), data.get("account_type", "stock"))
        try:
            res = await _call_tdx(tdx.cancel_order_stock, account_id,
                                  data.get("stock_code", ""), data.get("order_id", ""))
            ok = str(res.get("Value", "0")) == "1"
            executor.log_trade({"event": "cancel_order", "stock_code": data.get("stock_code"),
                                "order_id": data.get("order_id"), "ok": ok,
                                "msg": res.get("Msg", "")})
            return web.json_response({"success": ok, "message": res.get("Msg", ""),
                                      "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def configure_sltp(request: web.Request):
        data = await request.json()
        items = data.get("items", [])
        registered = sltp.register_items(items, data.get("account_id", 0))
        return web.json_response({"success": True, "items_configured": registered,
                                  "channel_used": "http"})

    async def sltp_state(request: web.Request):
        return web.json_response({"items": sltp.state_items(), "channel_used": "http"})

    # ---- 推送接口 (信号/消息/板块/源码) ----

    async def push_block(request: web.Request):
        data = await request.json()
        try:
            res = await _call_tdx(tdx.send_user_block,
                                  data.get("block_code", ""), data.get("stocks", []),
                                  bool(data.get("show", False)))
            return web.json_response({"success": True, "result": res, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def push_message(request: web.Request):
        data = await request.json()
        try:
            res = await _call_tdx(tdx.send_message, data.get("msg", ""))
            return web.json_response({"success": True, "result": res, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def push_warnings(request: web.Request):
        data = await request.json()
        try:
            def _do():
                return tdx.send_warn(
                    stock_list=data.get("stock_list", []),
                    price_list=data.get("price_list"),
                    close_list=data.get("close_list"),
                    volum_list=data.get("volum_list"),
                    bs_flag_list=data.get("bs_flag_list"),
                    warn_type_list=data.get("warn_type_list"),
                    reason_list=data.get("reason_list"),
                    count=data.get("count", 1))
            res = await _call_tdx(_do)
            return web.json_response({"success": True, "result": res, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    async def push_source(request: web.Request):
        data = await request.json()
        try:
            res = await _call_tdx(tdx.send_source, data.get("py_code", ""),
                                  int(data.get("handle_type", 0)))
            return web.json_response({"success": True, "result": res, "channel_used": "http"})
        except TdxError as e:
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    app.router.add_get("/api/v1/health", health)
    app.router.add_post("/api/v1/plans/execute", execute_plan)
    app.router.add_post("/api/v1/account/query", query_account)
    app.router.add_post("/api/v1/orders/query", query_orders)
    app.router.add_post("/api/v1/orders/cancel", cancel_order)
    app.router.add_post("/api/v1/sltp/configure", configure_sltp)
    app.router.add_get("/api/v1/sltp/state", sltp_state)
    app.router.add_post("/api/v1/push/block", push_block)
    app.router.add_post("/api/v1/push/message", push_message)
    app.router.add_post("/api/v1/push/warnings", push_warnings)
    app.router.add_post("/api/v1/push/source", push_source)

    # ---- 通用透传路由 (覆盖全部 tqcenter 方法) ----

    async def tdx_call(request: web.Request):
        """通用 JSON-RPC 透传: POST /api/v1/tdx/call
        body: {"method": "get_stock_info", "params": {"stock_code": "600519.SH"}}
        """
        try:
            data = await request.json()
        except Exception:
            return json_error(400, "INVALID_JSON", "请求体必须是合法 JSON")
        method = str(data.get("method", "")).strip()
        params = data.get("params", {}) or {}
        if not method:
            return json_error(400, "MISSING_METHOD", "缺少 method 字段")
        # 白名单: 只允许已定义的方法, 防止任意调用
        allowed = [m for m in dir(tdx) if not m.startswith("_") and callable(getattr(tdx, m))]
        if method not in allowed:
            return json_error(400, "UNKNOWN_METHOD", f"不支持的方法: {method}")
        try:
            import time as _time
            _t0 = _time.monotonic()
            # 内存缓存: 高并发场景拦截高频查询, 保护通达信
            cache_ttl = {"get_market_snapshot": 3, "get_market_data": 300,
                         "get_stock_info": 86400, "get_more_info": 86400,
                         "get_financial_data": 86400, "get_stock_list": 3600,
                         "get_sector_list": 3600}.get(method, 0)
            cache_key = f"{method}:{json.dumps(params, ensure_ascii=False, sort_keys=True)}" if cache_ttl else None

            if memory_cache and cache_key:
                def _fetch():
                    return _run_tdx_sync(tdx, method, params)
                result = memory_cache.get_or_fetch(cache_key, cache_ttl, _fetch)
            else:
                result = await _call_tdx(tdx.call, method, params)
            _dur = round((_time.monotonic() - _t0) * 1000, 1)
            # 自动缓存常见查询到 SQLite (若启用缓存)
            if cache_db and method in ("get_market_data", "get_stock_info",
                                       "get_market_snapshot", "get_financial_data"):
                symbol = _extract_symbol(method, params)
                if symbol:
                    _cache_result(cache_db, method, symbol, params, result)
            # 精确记录调用
            _record_call(cache_db, method, params, result=result, duration_ms=_dur,
                         category="query", status="ok")
            return web.json_response({"success": True, "method": method,
                                      "result": result, "channel_used": "http"})
        except TdxError as e:
            _record_call(cache_db, method, params, error=str(e),
                         duration_ms=round((_time.monotonic() - _t0) * 1000, 1),
                         category="query", status="error")
            return json_error(502, "TDX_UNAVAILABLE", str(e))

    # ---- 缓存查询路由 ----

    async def cache_query(request: web.Request):
        """查询 SQLite 缓存. GET /api/v1/cache/kline?symbol=600519.SH&period=1d&limit=100"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "缓存未启用")
        query = request.query
        symbol = query.get("symbol", "")
        period = query.get("period", "1d")
        limit = int(query.get("limit", 500))
        rows = cache_db.get_kline(symbol, period, limit)
        return web.json_response({"success": True, "symbol": symbol, "period": period,
                                  "count": len(rows), "data": rows})

    async def cache_logs(request: web.Request):
        """查询桥调用日志. GET /api/v1/cache/logs?method=get_market_data&limit=100"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "缓存未启用")
        query = request.query
        method = query.get("method", "")
        limit = int(query.get("limit", 100))
        rows = cache_db.get_logs(method, limit)
        return web.json_response({"success": True, "count": len(rows), "data": rows})

    async def cache_trades(request: web.Request):
        """查询交易日志. GET /api/v1/cache/trades?symbol=600519.SH&limit=100"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "缓存未启用")
        query = request.query
        symbol = query.get("symbol", "")
        limit = int(query.get("limit", 100))
        rows = cache_db.get_trade_logs(symbol, limit)
        return web.json_response({"success": True, "count": len(rows), "data": rows})

    async def cache_get(request: web.Request):
        """查询缓存数据. GET /api/v1/cache/get?type=snapshot|stock_info|financial|sector&symbol=600519.SH"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "缓存未启用")
        query = request.query
        ctype = query.get("type", "")
        symbol = query.get("symbol", "")
        block = query.get("block", "")
        data = None
        if ctype == "snapshot":
            data = cache_db.get_snapshot(symbol)
        elif ctype == "stock_info":
            data = cache_db.get_stock_info(symbol)
        elif ctype == "financial":
            data = cache_db.get_financial(symbol)
        elif ctype == "sector":
            data = cache_db.get_sector_stocks(block or symbol)
        return web.json_response({"success": data is not None, "type": ctype,
                                  "symbol": symbol, "data": data})

    app.router.add_post("/api/v1/tdx/call", tdx_call)
    app.router.add_get("/api/v1/cache/kline", cache_query)
    app.router.add_get("/api/v1/cache/logs", cache_logs)
    app.router.add_get("/api/v1/cache/trades", cache_trades)
    app.router.add_get("/api/v1/cache/get", cache_get)

    # ---- 桥状态 / 网页控制台 ----

    async def stats(request: web.Request):
        """桥状态总览: 本机IP/共享路径/连接状态/账户/缓存统计."""
        import socket
        # 获取本机 IPv4 地址
        local_ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in local_ips and not ip.startswith("127."):
                    local_ips.append(ip)
        except Exception:
            pass

        tdx_connected = await asyncio.to_thread(tdx.health_check_fast, 1.0)
        stats_data = {
            "hostname": socket.gethostname(),
            "local_ips": local_ips,
            "bridge_url": f"http://{local_ips[0] if local_ips else 'localhost'}:{port}",
            "tdx_connected": tdx_connected,
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "shared_dir": os.environ.get("SHARED_DIR", ""),
            "token_configured": bool(token),
            "port": port,
        }
        # 账户概览 (带缓存, 避免网页高频轮询压垮通达信)
        import time as _time
        _now = _time.monotonic()
        if _now - _stats_cache.get("ts", 0) < 10:
            stats_data["account"] = _stats_cache.get("account")
        else:
            try:
                account_id = await _call_tdx(executor.resolve_account_id, "", "stock")
                asset = await _call_tdx(tdx.query_stock_asset, account_id)
                positions = await _call_tdx(tdx.query_stock_positions, account_id)
                stats_data["account"] = {
                    "currency": asset.get("Currency", ""),
                    "balance": float(asset.get("Balance", 0) or 0),
                    "cash": float(asset.get("Cash", 0) or 0),
                    "asset": float(asset.get("Asset", 0) or 0),
                    "market_value": float(asset.get("MarketValue", 0) or 0),
                    "position_count": len(positions or []),
                }
                _stats_cache["ts"] = _now
                _stats_cache["account"] = stats_data["account"]
            except Exception:
                stats_data["account"] = None
        # 缓存统计
        if cache_db:
            try:
                stats_data["cache"] = cache_db.get_stats()
                stats_data["cache"]["disk_bytes"] = cache_db.get_cache_disk()
                # 内存缓存命中率
                if memory_cache:
                    stats_data["cache"]["mem_hit_rate"] = memory_cache.hit_rate()
                    stats_data["cache"]["mem_hits"] = memory_cache.stats()["hits"]
                    stats_data["cache"]["mem_entries"] = memory_cache.stats()["cache_entries"]
            except Exception:
                stats_data["cache"] = None
        # 限流状态
        if rate_limiter:
            try:
                stats_data["security"] = rate_limiter.stats()
            except Exception:
                stats_data["security"] = None
        return web.json_response({"success": True, "data": stats_data})

    async def reset_token(request: web.Request):
        """重置 BRIDGE_AUTH_TOKEN (生成新 hex, 更新环境变量)."""
        import secrets
        new_token = secrets.token_hex(32)
        os.environ["BRIDGE_AUTH_TOKEN"] = new_token
        return web.json_response({"success": True, "new_token": new_token,
                                  "message": "新 token 已生成, 请同步到 Linux .env"})

    # ---- Token 管理 API (动态增删查, SQLite 存储) ----

    async def list_tokens(request: web.Request):
        """列出所有 token. GET /api/v1/auth/tokens"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "token 管理需启用缓存")
        try:
            tokens = await asyncio.to_thread(cache_db.list_tokens)
            return web.json_response({"success": True, "tokens": tokens})
        except Exception as e:
            return json_error(500, "INTERNAL", str(e))

    async def add_token(request: web.Request):
        """新增 token. POST /api/v1/auth/tokens {name?, count?}"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "token 管理需启用缓存")
        import secrets
        try:
            data = await request.json() or {}
        except Exception:
            data = {}
        name = str(data.get("name", "") or "")[:50]
        count = max(1, min(int(data.get("count", 1)), 100))  # 批量生成 1-100 个
        tokens = []
        for i in range(count):
            new_token = secrets.token_hex(32)
            ok = await asyncio.to_thread(cache_db.add_token, new_token,
                                         f"{name or 'client'}_{i+1}")
            if ok:
                tokens.append({"token": new_token, "name": f"{name or 'client'}_{i+1}"})
        return web.json_response({"success": True, "count": len(tokens), "tokens": tokens,
                                  "message": f"已生成 {len(tokens)} 个 token, 请立即保存"})

    async def delete_token_route(request: web.Request):
        """删除 token. DELETE /api/v1/auth/tokens/{token_hash}"""
        if not cache_db:
            return json_error(501, "CACHE_DISABLED", "token 管理需启用缓存")
        token_hash = request.match_info.get("token_hash", "")
        if not token_hash:
            return json_error(400, "MISSING_HASH", "缺少 token_hash")
        ok = await asyncio.to_thread(cache_db.delete_token, token_hash)
        if not ok:
            return json_error(404, "NOT_FOUND", "token 不存在")
        return web.json_response({"success": True, "message": "token 已删除"})

    async def ui_page(request: web.Request):
        """网页控制台 HTML."""
        from aiohttp import web as _web
        # 兼容 PyInstaller 打包: 资源在 sys._MEIPASS 下
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        html_path = os.path.join(base, "static", "ui.html")
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            return _web.Response(text=html, content_type="text/html", charset="utf-8")
        except OSError:
            return _web.Response(text="<h1>ui.html 未找到</h1>", content_type="text/html")

    app.router.add_get("/api/v1/stats", stats)
    app.router.add_post("/api/v1/auth/reset-token", reset_token)
    app.router.add_get("/api/v1/auth/tokens", list_tokens)
    app.router.add_post("/api/v1/auth/tokens", add_token)
    app.router.add_delete("/api/v1/auth/tokens/{token_hash}", delete_token_route)
    app.router.add_get("/ui", ui_page)

    return app
