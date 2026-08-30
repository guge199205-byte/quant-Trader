"""BayMax-Trader API 服务（FastAPI，默认端口 8090）。

核心能力：
- /api/data/* 实时代理：优先读项目根 data/（实时交易数据），回退到 nof0/data/ 静态快照
- 结构化端点：agents / positions / trades / performance / logs / status / config
- 前端通过 config.yaml 的 api_base 一行切换即可实时化
"""

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import (
    get_data_root,
    get_enabled_markets,
    get_ui_dir,
    load_backend_config,
)
from backend.services import agent_data

app = FastAPI(title="BayMax-Trader API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 鉴权（X-API-Token，.env 的 API_TOKEN；未配置则不启用） ----------

import os as _os

from dotenv import load_dotenv as _load_dotenv

_load_dotenv(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env"))
_API_TOKEN = _os.getenv("API_TOKEN", "").strip('"')

# 前端静态资源（nof0 主题）：assets / 页面 / data 快照
_UI_DIR = get_ui_dir(load_backend_config())
if (_UI_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_UI_DIR / "assets"), name="assets")


@app.middleware("http")
async def auth_middleware(request, call_next):
    # CORS 预检直接放行（CORSMiddleware 处理）
    if request.method == "OPTIONS":
        return await call_next(request)
    if _API_TOKEN:
        # /api/data/* 与静态托管视为资源类，豁免鉴权（data-loader 原生 fetch 不带头）
        path = request.url.path
        if (
            path.startswith("/api/data")
            or path == "/"
            or path.startswith("/assets/")
            or path.startswith("/data/")
            or path.endswith((".html", ".ico"))
        ):
            return await call_next(request)
        token = request.headers.get("x-api-token", "")
        if token != _API_TOKEN:
            from fastapi.responses import JSONResponse as _JR

            # 手动补 CORS 头（本中间件在 CORSMiddleware 外层，401 不经其处理）
            resp = _JR(status_code=401, content={"success": False, "error": "unauthorized"})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "X-API-Token, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            return resp
    return await call_next(request)

_config: Optional[dict] = None


def config() -> dict:
    global _config
    if _config is None:
        _config = load_backend_config()
    return _config


@app.on_event("shutdown")
def _shutdown():
    global _config
    _config = None


# ---------- 基础 ----------

@app.get("/api/config")
def get_config():
    cfg = config()
    # 不暴露密钥类字段
    safe = json.loads(json.dumps(cfg))
    return {"success": True, "data": safe}


@app.get("/api/status")
def get_status():
    cfg = config()
    root = get_data_root(cfg)
    runtime_file = root.parent / "runtime_env.json"
    runtime = {}
    if runtime_file.exists():
        try:
            runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            runtime = {}
    return {
        "success": True,
        "data": {
            "service": "baymax-api",
            "version": "0.1.0",
            "markets": get_enabled_markets(cfg),
            "broker_default": cfg.get("broker", {}).get("default", "sandbox"),
            "runtime_env": runtime,
        },
    }


@app.get("/api/markets")
def get_markets():
    cfg = config()
    markets = [
        {"id": mid, **m}
        for mid, m in cfg.get("markets", {}).items()
        if m.get("enabled", True)
    ]
    return {"success": True, "data": markets}


# ---------- 实时代理（前端实时化的核心） ----------

@app.get("/api/data/{path:path}")
def proxy_data(path: str):
    """优先返回项目根 data/ 下的实时文件；不存在时回退到前端静态快照。"""
    cfg = config()
    candidates = [
        get_data_root(cfg) / path,
        get_ui_dir(cfg) / "data" / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            media = _media_type(candidate)
            return FileResponse(candidate, media_type=media)
    raise HTTPException(status_code=404, detail=f"数据文件不存在: {path}")


@app.get("/api/data/{path:path}/")
async def proxy_data_dir(path: str):
    return RedirectResponse(url=f"/api/data/{path}")


# ---------- 结构化端点 ----------

@app.get("/api/agents")
def list_agents(market: str = Query("us")):
    cfg = config()
    return {"success": True, "data": agent_data.list_agents(cfg, market)}


@app.get("/api/agents/{agent}/positions")
def get_positions(agent: str, market: str = Query("us"), limit: int = Query(200, ge=1, le=5000)):
    cfg = config()
    records = agent_data.load_position_records(cfg, agent, market, limit)
    if not records:
        raise HTTPException(status_code=404, detail=f"Agent 无持仓记录: {agent}")
    return {"success": True, "data": records}


@app.get("/api/agents/{agent}/trades")
def get_trades(agent: str, market: str = Query("us"), limit: int = Query(200, ge=1, le=5000)):
    cfg = config()
    from backend.services import trade_store

    trades = trade_store.query_trades(cfg, market, agent, limit)
    if not trades:
        # 缓存未命中时回退 JSONL 直读（保证一致性）
        trades = agent_data.load_trades(cfg, agent, market, limit)
    return {"success": True, "data": trades}


@app.get("/api/agents/{agent}/performance")
def get_performance(agent: str, market: str = Query("us")):
    cfg = config()
    series = agent_data.compute_equity_series(cfg, agent, market)
    if not series:
        raise HTTPException(status_code=404, detail=f"Agent 无净值数据: {agent}")
    equity_values = [p["equity"] for p in series]
    peak = -float("inf")
    max_drawdown = 0.0
    for v in equity_values:
        peak = max(peak, v)
        if peak > 0:
            max_drawdown = min(max_drawdown, (v - peak) / peak)
    first, last = equity_values[0], equity_values[-1]
    total_return = (last - first) / first if first else 0.0
    records = agent_data.load_position_records(cfg, agent, market)
    extended = agent_data.compute_extended_summary(series, records, cfg, market)
    return {
        "success": True,
        "data": {
            "agent": agent,
            "points": series,
            "summary": {
                "start_equity": first,
                "end_equity": last,
                "total_return": round(total_return, 6),
                "max_drawdown": round(abs(max_drawdown), 6),
                "records": len(series),
                **extended,
            },
        },
    }


@app.get("/api/agents/{agent}/trade-detail")
def get_trade_detail(agent: str, market: str = Query("us"), limit: int = Query(25, ge=1, le=200)):
    """FIFO 重建已平仓逐笔明细（LAST 25 TRADES 表用），最新平仓在前。"""
    cfg = config()
    records = agent_data.load_position_records(cfg, agent, market, limit=5000)
    closed, _ = agent_data.rebuild_closed_trades(cfg, market, records)
    closed.sort(key=lambda t: t["exit_date"], reverse=True)
    return {"success": True, "data": closed[:limit]}


@app.get("/api/agents/{agent}/logs")
def get_logs(agent: str, market: str = Query("us"), date: Optional[str] = None):
    cfg = config()
    lines = agent_data.load_agent_logs(cfg, agent, market, date)
    return {"success": True, "data": lines}


# ---------- 指标 ----------

@app.get("/api/metrics")
def get_metrics():
    """聚合指标：服务健康 / agent / 交易 / 记忆 / 风控状态。"""
    import time

    cfg = config()
    root = get_data_root(cfg)
    now = time.time()
    metrics = {"services": {}, "markets": {}}

    # 服务探活：优先宿主侧结果（logs/service_status.json；dsh 只绑宿主回环，
    # 容器内不可达）；文件过期则容器内回退（mcp 走 compose DNS）
    probes = {
        "api": 8091, "mcp_us": 8100, "mcp_cn": 8200, "mcp_hk": 8300, "dsh": 3081,
    }
    _dns_hosts = {"mcp_us": "mcp-us", "mcp_cn": "mcp-cn", "mcp_hk": "mcp-hk"}
    status_file = Path("logs/service_status.json")
    host_probe = {}
    try:
        if status_file.is_file() and time.time() - status_file.stat().st_mtime < 360:
            host_probe = json.loads(status_file.read_text(encoding="utf-8"))
    except OSError:
        pass
    for name, port in probes.items():
        if name in host_probe:
            metrics["services"][name] = host_probe[name]
            continue
        try:
            import socket

            s = socket.create_connection((_dns_hosts.get(name, "127.0.0.1"), port), timeout=2)
            s.close()
            metrics["services"][name] = "up"
        except OSError:
            metrics["services"][name] = "down"

    # 每市场 agent/交易/记忆
    for market in ["us", "cn", "hk"]:
        agents = agent_data.list_agents(cfg, market)
        data_dir = root / {
            "us": "agent_data", "cn": "agent_data_astock", "hk": "agent_data_hk",
        }[market]
        trades = 0
        for a in agents:
            trades += a.get("total_records", 0)
        memory_file = data_dir / "market_memory.md"
        memory_lines = 0
        if memory_file.exists():
            try:
                memory_lines = len(memory_file.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
        metrics["markets"][market] = {
            "agents": len(agents),
            "position_records": trades,
            "memory_lines": memory_lines,
        }

    # 最近交易时间（任一市场最新 position mtime）
    latest_trade_ts = 0
    for market in ["us", "cn", "hk"]:
        d = root / {"us": "agent_data", "cn": "agent_data_astock", "hk": "agent_data_hk"}[market]
        if d.exists():
            for pf in d.glob("*/position/position.jsonl"):
                try:
                    latest_trade_ts = max(latest_trade_ts, pf.stat().st_mtime)
                except OSError:
                    pass
    metrics["latest_trade_age_sec"] = int(now - latest_trade_ts) if latest_trade_ts else None
    metrics["generated_at"] = int(now)
    return {"success": True, "data": metrics}


# ---------- 股票中文名 ----------

@app.get("/api/stock-names")
def get_stock_names(market: str = Query("us")):
    from tools.stock_names import CN_STOCK_NAMES, HK_STOCK_NAMES, US_STOCK_NAMES

    table = {"cn": CN_STOCK_NAMES, "hk": HK_STOCK_NAMES, "us": US_STOCK_NAMES}.get(market, {})
    return {"success": True, "data": table}


# ---------- 最新价格（Live 滚动价格条） ----------

@app.get("/api/prices")
def get_latest_prices(market: str = Query("us")):
    cfg = config()
    return {"success": True, "data": agent_data.load_latest_prices(cfg, market)}


# ---------- 总控聚合 ----------

@app.get("/api/overview")
def get_overview():
    """总控台聚合：三市场 × agent（净值/收益/风控/记忆/运行状态）。"""
    cfg = config()
    markets = {}
    for market in ["us", "cn", "hk"]:
        agents = agent_data.list_agents(cfg, market)
        rows = []
        for a in agents:
            series = agent_data.compute_equity_series(cfg, a["name"], market)
            summary = None
            if series:
                vals = [p["equity"] for p in series]
                peak = -float("inf")
                dd = 0.0
                for v in vals:
                    peak = max(peak, v)
                    if peak > 0:
                        dd = min(dd, (v - peak) / peak)
                first, last = vals[0], vals[-1]
                records = agent_data.load_position_records(cfg, a["name"], market)
                summary = {
                    "start_equity": first,
                    "end_equity": last,
                    "total_return": round((last - first) / first, 6) if first else 0.0,
                    "max_drawdown": round(abs(dd), 6),
                }
                summary.update(agent_data.compute_extended_summary(series, records, cfg, market))
            rows.append({
                "name": a["name"],
                "latest_date": a["latest_date"],
                "records": a["total_records"],
                "cash": a["cash"],
                "summary": summary,
            })
        markets[market] = rows
    return {"success": True, "data": {"markets": markets}}


# ---------- 静态托管（8090 直接出页面：index / 子页面 / data 快照） ----------

@app.get("/")
def index():
    ui_dir = get_ui_dir(config())
    index_file = ui_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"success": True, "message": "BayMax-Trader API 运行中，前端请访问 8080"}


@app.get("/{name}.html")
def ui_page(name: str):
    """nof0 子页面（portfolio / models / monitor 等）。"""
    ui_dir = get_ui_dir(config())
    page_file = ui_dir / f"{name}.html"
    if page_file.exists():
        return FileResponse(page_file)
    raise HTTPException(status_code=404, detail=f"页面不存在: {name}.html")


@app.get("/data/{path:path}")
def ui_data(path: str):
    """前端 data/ 静态快照（config.yaml、agent_data 等）。"""
    ui_dir = get_ui_dir(config())
    data_file = ui_dir / "data" / path
    if data_file.is_file():
        return FileResponse(data_file, media_type=_media_type(data_file))
    raise HTTPException(status_code=404, detail=f"静态数据不存在: {path}")


@app.get("/favicon.ico")
def favicon():
    ui_dir = get_ui_dir(config())
    icon_file = ui_dir / "favicon.ico"
    if icon_file.exists():
        return FileResponse(icon_file, media_type="image/x-icon")
    raise HTTPException(status_code=404)


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".json": "application/json",
        ".jsonl": "application/json",
        ".csv": "text/csv",
        ".html": "text/html",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".css": "text/css",
        ".js": "application/javascript",
    }.get(suffix, "application/octet-stream")


def main():
    import uvicorn

    cfg = load_backend_config()
    server = cfg.get("server", {})
    uvicorn.run(app, host=server.get("host", "0.0.0.0"), port=int(server.get("port", 8090)), log_level="info")


if __name__ == "__main__":
    main()
