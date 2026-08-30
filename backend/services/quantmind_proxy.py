"""quantmind 交易平台代理 —— 通达信桥 / 券商接入 / 实时交易。

arena 前端通过本代理调用 quantmind 后端（127.0.0.1:8000，容器在宿主机跑），
免去 CORS 与前端直连；自动登录并缓存 access_token，401 时重登重试一次。

敏感字段（券商私钥/密码等）由 quantmind 侧只写不回显（*_configured 标记），
本代理仅透传，不落地任何凭据。
"""

import logging
import os
import threading
import time
from typing import Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# baymax-api 容器内访问宿主机 quantmind 容器：走 docker0 网关 172.17.0.1
QM_API_BASE = os.getenv("QM_API_BASE", "http://172.17.0.1:8000").rstrip("/")
QM_LOGIN_URL = f"{QM_API_BASE}/api/v1/auth/login"
QM_USERNAME = os.getenv("QM_ADMIN_USER", "admin")
QM_PASSWORD = os.getenv("QM_ADMIN_PASSWORD", "admin123")
QM_TENANT = "default"

# JWT 有效期未知，保守缓存 6 小时；401 时强制重登重试
_TOKEN_TTL_SEC = 6 * 3600

_token_lock = threading.Lock()
_token_cache: dict[str, Optional[str]] = {"value": None}
_token_cache["expires_at"] = 0.0

_SKIP_HEADERS = {"host", "content-length", "transfer-encoding", "authorization", "connection"}
_ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")

_client: Optional[httpx.Client] = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0), trust_env=False)
    return _client


async def _login() -> str:
    """登录拿 access_token，失败抛异常。"""
    client = await _get_client()
    resp = await client.post(
        QM_LOGIN_URL,
        json={"username": QM_USERNAME, "password": QM_PASSWORD, "tenant_id": QM_TENANT},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"quantmind 登录失败: HTTP {resp.status_code}")
    data = resp.json()
    token = data.get("access_token") or (data.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError(f"quantmind 登录响应无 access_token: {data}")
    return str(token)


async def get_token(force: bool = False) -> str:
    """线程安全地取 token；过期或 force 时重新登录。"""
    with _token_lock:
        if (
            force
            or not _token_cache.get("value")
            or time.time() >= float(_token_cache.get("expires_at", 0.0))
        ):
            _token_cache["value"] = await _login()
            _token_cache["expires_at"] = time.time() + _TOKEN_TTL_SEC
        return str(_token_cache["value"])


async def proxy_to_quantmind(request: Request, path: str) -> Response:
    """转发 /api/quantmind/{path} → quantmind /api/v1/{path}。

    透传 method / query / body，注入 Bearer token；401 时重登重试一次。
    """
    url = f"{QM_API_BASE}/api/v1/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS
    }

    for attempt in (1, 2):
        headers["Authorization"] = f"Bearer {await get_token(force=(attempt == 2))}"
        try:
            client = await _get_client()
            resp = await client.request(
                method=request.method.upper(),
                url=url,
                headers=headers,
                content=body or None,
            )
        except httpx.HTTPError as exc:
            logger.warning("quantmind 代理请求失败: %s %s -> %s", request.method, url, exc)
            return JSONResponse(
                status_code=502,
                content={"success": False, "error": f"quantmind 不可达: {exc}"},
            )
        if resp.status_code != 401 or attempt == 2:
            resp_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
                media_type=resp.headers.get("content-type"),
            )
        logger.info("quantmind token 失效，重登重试: %s", path)

    # 不可达路径（理论不到）
    return JSONResponse(status_code=502, content={"success": False, "error": "代理失败"})
