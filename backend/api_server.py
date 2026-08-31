"""BayMax-Trader API 服务（FastAPI，默认端口 8090）。

核心能力：
- /api/data/* 实时代理：优先读项目根 data/（实时交易数据）
- 结构化端点：agents / positions / trades / performance / logs / status / config
- 前端通过 config.yaml 的 api_base 一行切换即可实时化
"""

import functools
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from backend.config import (
    get_data_root,
    get_enabled_markets,
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


# ---------- 轻量 TTL 缓存（热接口加固） ----------
# 背景：/api/overview 无缓存全量重算（3 市场 × agent × 净值/汇总），Live 页轮询
# 下曾两次把 api 线程池打满死锁。这里给热接口加线程安全 TTL 缓存限频：
# 同签名在 TTL 内直接返回缓存值，不重算；数据根目录变更时用 ttl_invalidate 作废。

_TTL_OVERVIEW_S = 30   # 总控聚合（最重，30s 内多次请求只算一次）
_TTL_PRICES_S = 10     # Live 价格条（轮询频率最高，10s 限频）
_TTL_CATALOG_S = 60    # 数据平台目录扫描（文件系统遍历，60s）

_ttl_lock = threading.Lock()
_ttl_cache: dict = {}


def ttl_cache(seconds: float):
    """线程安全 TTL 缓存装饰器：key = (module, fn_name, *args, **kwargs)。"""

    def deco(fn):
        key_base = (fn.__module__, fn.__name__)

        # wraps 让 FastAPI 看到原始函数签名（否则 *args/**kwargs 会被当成查询参数）
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_base + args + tuple(sorted(kwargs.items()))
            now = time.monotonic()
            with _ttl_lock:
                hit = _ttl_cache.get(key)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            value = fn(*args, **kwargs)
            with _ttl_lock:
                _ttl_cache[key] = (now, value)
            return value

        return wrapper

    return deco


def ttl_invalidate(prefix: tuple):
    """使 key 以 prefix 开头的缓存项失效（如数据根目录被修改后）。"""
    with _ttl_lock:
        for key in [k for k in _ttl_cache if k[: len(prefix)] == prefix]:
            del _ttl_cache[key]

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
    """返回项目根 data/ 下的实时文件。"""
    cfg = config()
    candidate = get_data_root(cfg) / path
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
    # 补 price/notional（价格文件重算 + 滑点模型，与 FIFO 平仓口径一致）
    return {"success": True, "data": agent_data.enrich_trades_with_prices(cfg, market, trades)}


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
    closed, _, _ = agent_data.rebuild_closed_trades(cfg, market, records)
    closed.sort(key=lambda t: t["exit_date"], reverse=True)
    return {"success": True, "data": closed[:limit]}


@app.get("/api/agents/{agent}/holdings")
def get_holdings(agent: str, market: str = Query("us")):
    """当前持仓明细：数量/成本/最新价/市值/浮动盈亏/占比（含现金）。"""
    cfg = config()
    records = agent_data.load_position_records(cfg, agent, market, limit=5000)
    quotes = agent_data.load_latest_prices(cfg, market)
    return {"success": True, "data": agent_data.compute_holdings(cfg, market, records, quotes)}


@app.get("/api/agents/{agent}/logs")
def get_logs(agent: str, market: str = Query("us"), date: Optional[str] = None):
    cfg = config()
    lines = agent_data.load_agent_logs(cfg, agent, market, date)
    return {"success": True, "data": lines}


# ---------- 实盘（通达信桥，逻辑同模拟盘那套） ----------

def _live_broker():
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    return TdxBridgeBroker()


@app.get("/api/live/account")
def live_account():
    """实盘账户：可用资金/总资产/持仓明细（桥实时查询）。
    持仓补实时价（桥 quote）与浮动盈亏——今天买卖的票要实时跟踪。"""
    broker = _live_broker()
    try:
        acct = broker._account_query()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"桥查询失败: {e}") from e
    positions = acct.get("positions") or []
    # 股票名称映射：CN_STOCK_NAMES（上证50）→ quantdb instrument_detail（全市场，duckdb）→ 空
    try:
        from tools.stock_names import CN_STOCK_NAMES

        names = dict(CN_STOCK_NAMES)
    except Exception:  # noqa: BLE001
        names = {}
    names = {**names, **_quantdb_stock_names()}
    # 买入时间：从交易日志取该 code 最近成功买入的 ts
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    buy_ts: dict = {}
    for f in sorted(logs_dir.glob("live_trade_*.jsonl")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("mode") == "execute" and rec.get("result") and rec.get("code"):
                buy_ts.setdefault(rec["code"], rec.get("ts", ""))
    for p in positions:
        code = p.get("stock_code") or ""
        cost = float(p.get("cost_price") or 0)
        price = float(p.get("last_price") or 0)
        try:
            if not price:
                quote = broker.get_quote(code, "")
                price = float((quote or {}).get("close") or 0)
        except Exception:  # noqa: BLE001
            price = 0
        p["last_price"] = price
        p["name"] = names.get(code, "")
        p["buy_time"] = (buy_ts.get(code) or "")[:16]
        p["position_value"] = round(price * float(p.get("total_volume") or 0), 2)
        if cost and price:
            p["pnl_pct"] = round((price - cost) / cost * 100, 2)
            p["pnl"] = round((price - cost) * float(p.get("total_volume") or 0), 2)
        else:
            p["pnl_pct"], p["pnl"] = None, None
    return {"success": True, "data": acct}


@app.get("/api/live/orders")
def live_orders():
    """当日委托（桥 orders/query）。"""
    try:
        orders = _live_broker().get_orders()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"桥查询失败: {e}") from e
    return {"success": True, "data": orders}


@app.get("/api/live/ledger")
def live_ledger():
    """实盘分账账本：每 agent ¥10 万虚拟子账户（scripts/live_ledger.py）。
    返回每 agent 的额度使用与名下持仓（数量/成本/名称），供持仓 tab 按模型展示。"""
    ledger_file = Path(__file__).resolve().parents[1] / "logs" / "live_ledger.json"
    try:
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {"version": 1, "agents": {}}
    try:
        from tools.stock_names import CN_STOCK_NAMES

        names = dict(CN_STOCK_NAMES)
    except Exception:  # noqa: BLE001
        names = {}
    names = {**names, **_quantdb_stock_names()}
    quota = 100_000.0
    out: dict = {}
    for agent, rec in (ledger.get("agents") or {}).items():
        positions = []
        used = 0.0
        for code, p in sorted((rec.get("positions") or {}).items()):
            volume = int(p.get("volume") or 0)
            cost = float(p.get("cost_price") or 0)
            used += volume * cost
            positions.append({
                "code": code,
                "name": names.get(code, code),
                "volume": volume,
                "cost_price": round(cost, 4),
                "position_value": round(volume * cost, 2),
                "buy_ts": (p.get("buy_ts") or "")[:16],
            })
        out[agent] = {
            "quota": quota,
            "used": round(used, 2),
            "remaining": round(max(quota - used, 0.0), 2),
            "positions": positions,
        }
    return {"success": True, "data": {"agents": out}}


@app.get("/api/live/equity")
def live_equity():
    """实盘净值点（logs/live_equity.jsonl，北京 日期+小时+口径 一条）。
    总账户（asset=桥实时总资产）与每 agent 分账虚拟净值（虚拟现金+持仓×实时价）。
    供 ARENA 总账户净值图：曲线随盘中记录逐小时累积。"""
    f = Path(__file__).resolve().parents[1] / "logs" / "live_equity.jsonl"
    total, agents = [], {}
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return {"success": True, "data": {"total": total, "agents": agents}}
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        v = r.get("value")
        if v is None:
            continue
        pt = {"date": r.get("date"), "ts": r.get("ts"), "value": float(v)}
        agent = r.get("agent")
        if agent:
            agents.setdefault(agent, []).append(pt)
        else:
            total.append(pt)
    # 文件按追加顺序，按 ts 排成时间序（防止回填/追加顺序错乱画乱线）
    total.sort(key=lambda p: str(p.get("ts", "")))
    for pts in agents.values():
        pts.sort(key=lambda p: str(p.get("ts", "")))
    return {"success": True, "data": {"total": total, "agents": agents}}


@app.get("/api/token-usage")
def token_usage():
    """每 agent 实盘 LLM 分析累计 token 消耗。
    统计 data/agent_data_astock/*/log/**/log.jsonl 中带 usage 字段的条目
    （只由 live_hourly_analysis 写入 → 天然排除模拟盘回放日志）。"""
    root = Path(__file__).resolve().parents[1] / "data" / "agent_data_astock"
    agents: dict = {}
    if root.is_dir():
        for logf in sorted(root.glob("*/log/**/log.jsonl")):
            agent = logf.parts[-4]  # .../agent_data_astock/{agent}/log/{date}/log.jsonl
            acc = agents.setdefault(agent, {"calls": 0, "prompt_tokens": 0,
                                            "completion_tokens": 0, "total_tokens": 0,
                                            "estimated": 0, "last_ts": None})
            try:
                text = logf.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                u = r.get("usage")
                if not isinstance(u, dict):
                    continue
                acc["calls"] += 1
                acc["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
                acc["completion_tokens"] += int(u.get("completion_tokens") or 0)
                acc["total_tokens"] += int(u.get("total_tokens") or 0)
                if u.get("usage_est"):
                    acc["estimated"] += 1
                ts = r.get("timestamp") or r.get("ts")
                if ts and (not acc["last_ts"] or ts > acc["last_ts"]):
                    acc["last_ts"] = ts
    return {"success": True, "data": {"agents": agents}}


@app.get("/api/live/trades")
def live_trades(limit: int = Query(200, ge=1, le=5000)):
    """实盘交易记录：logs/live_trade_*.jsonl 的买入/卖出行（最新在前）。"""
    if not isinstance(limit, int):  # 直接函数调用（绕过 FastAPI 参数解析）时兜底
        limit = 200
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    records = []
    for f in sorted(logs_dir.glob("live_trade_*.jsonl")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("mode") in ("execute", "sell") and ("result" in rec or "error" in rec):
                records.append(rec)
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)
    # 补成交价：桥当日委托 filled_price（按 order_id，回退按 code）
    price_map: dict = {}
    try:
        broker = _live_broker()
        for o in broker.get_orders():
            fp = o.get("filled_price")
            if fp is None or o.get("status") != "filled":
                continue
            price_map[o.get("order_id")] = float(fp)
            price_map.setdefault(o.get("stock_code"), float(fp))
    except Exception:  # noqa: BLE001
        pass
    for rec in records:
        # 桥 filled_price 是真实成交价；日志 price 只是下单参考价（现价×1.01），有匹配就覆盖
        rid = (rec.get("result") or {}).get("order_id")
        price = price_map.get(rid) or price_map.get(rec.get("code"))
        if price:
            rec["price"] = price
        elif not rec.get("price"):
            rec["price"] = None
    return {"success": True, "data": records[:limit]}


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

_QUANTDB_NAMES: dict | None = None


def _quantdb_stock_names() -> dict:
    """quantdb instrument_detail 全市场 Symbol→Name（duckdb,一次性缓存）。

    /data/quantdb 在宿主机上是空目录占位，须走 ~/projects/quantmind/data/quantdb。
    """
    global _QUANTDB_NAMES
    if _QUANTDB_NAMES is not None:
        return _QUANTDB_NAMES
    found: dict = {}
    try:
        import duckdb

        for _root in (Path("/data/quantdb"),
                      Path(os.getenv("QM_QUANTDB_DATA_DIR", "")),
                      Path.home() / "projects/quantmind/data/quantdb"):
            detail = _root / "2_base_sector/instrument_detail/instrument_detail.parquet"
            if not detail.is_file():
                continue
            _con = duckdb.connect()
            try:
                for sym, nm in _con.execute(
                        "SELECT Symbol, Name FROM read_parquet(?)", [str(detail)]).fetchall():
                    if sym and nm:
                        found.setdefault(sym, nm)
            finally:
                _con.close()
            break
    except Exception:  # noqa: BLE001
        pass
    _QUANTDB_NAMES = found
    return found


@app.get("/api/stock-names")
def get_stock_names(market: str = Query("us")):
    from tools.stock_names import CN_STOCK_NAMES, HK_STOCK_NAMES, US_STOCK_NAMES

    table = {"cn": CN_STOCK_NAMES, "hk": HK_STOCK_NAMES, "us": US_STOCK_NAMES}.get(market, {})
    if market == "cn":
        table = {**dict(table), **_quantdb_stock_names()}
    return {"success": True, "data": table}


# ---------- 最新价格（Live 滚动价格条） ----------

@app.get("/api/prices")
@ttl_cache(_TTL_PRICES_S)
def get_latest_prices(market: str = Query("us")):
    cfg = config()
    return {"success": True, "data": agent_data.load_latest_prices(cfg, market)}


# ---------- 总控聚合 ----------

@app.get("/api/overview")
@ttl_cache(_TTL_OVERVIEW_S)
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

    # 通达信桥实盘状态（ARENA 前端读 data.tdx.real_trading_enabled 显示"实盘 开/关"）
    import requests

    tdx_status = {"real_trading_enabled": False, "health": None, "enabled": False}
    try:
        broker = _live_broker()
        h = requests.get(f"{broker.bridge_url}/api/v1/health", timeout=8)
        health = h.json()
        tdx_status = {
            "real_trading_enabled": bool(health.get("status") == "ok"
                                         and health.get("tdx_connected")),
            "health": health,
            "enabled": bool(health.get("status") == "ok"),
        }
        if tdx_status["real_trading_enabled"]:
            acct = broker._account_query()
            asset = acct.get("asset") or {}
            tdx_status["account"] = {
                "cash": asset.get("cash"),
                "market_value": asset.get("market_value"),
                "total_asset": asset.get("asset"),
                "positions": len(acct.get("positions") or []),
            }
    except Exception:  # noqa: BLE001
        pass
    return {"success": True, "data": {"markets": markets, "tdx": tdx_status}}


# ---------- quantmind 交易平台代理（通达信桥 / 券商接入 / 实时交易） ----------

@app.api_route("/api/quantmind/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def quantmind_proxy(path: str, request: Request):
    """转发到 quantmind 交易平台（容器内经 docker0 网关访问宿主机 8000），token 自动登录/续期。"""
    from backend.services import quantmind_proxy as qm

    return await qm.proxy_to_quantmind(request, path)


# ---------- 数据平台（/api/data-platform：多市场 parquet 仓库浏览/预览） ----------

_DP_MARKETS = {"quantdb", "quantus", "quanthk", "quantfutures"}


@app.get("/api/data-platform/markets")
def dp_markets():
    from backend.services import data_platform as dp

    return {"success": True, "data": dp.MARKETS}


@app.get("/api/data-platform/{market}/catalog")
@ttl_cache(_TTL_CATALOG_S)
def dp_catalog(market: str):
    if market not in _DP_MARKETS:
        raise HTTPException(status_code=400, detail=f"未知市场: {market}")
    from backend.services import data_platform as dp

    return {"success": True, "data": dp.build_catalog(market)}


@app.get("/api/data-platform/{market}/preview")
def dp_preview(
    market: str,
    dataset: str = Query(...),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    if market not in _DP_MARKETS:
        raise HTTPException(status_code=400, detail=f"未知市场: {market}")
    from backend.services import data_platform as dp

    try:
        return {"success": True, "data": dp.preview_dataset(market, dataset, symbol, limit)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        from fastapi.responses import JSONResponse as _JR

        logger.error("data-platform preview failed (%s/%s): %s", market, dataset, exc, exc_info=True)
        return _JR(status_code=500, content={"success": False, "error": f"预览失败: {exc}"})


@app.get("/api/data-platform/{market}/root")
@ttl_cache(_TTL_CATALOG_S)
def dp_root(market: str):
    if market not in _DP_MARKETS:
        raise HTTPException(status_code=400, detail=f"未知市场: {market}")
    from backend.services import data_platform as dp

    return {"success": True, "data": {"market": market, "root": dp.data_root(market)}}


@app.post("/api/data-platform/{market}/root")
async def dp_set_root(market: str, request: Request):
    """设置数据根目录（只影响展示/预览读取位置，不复制不移动数据）。"""
    if market not in _DP_MARKETS:
        raise HTTPException(status_code=400, detail=f"未知市场: {market}")
    from backend.services import data_platform as dp

    body = await request.json()
    root = str(body.get("root", "")).strip()
    if not root:
        raise HTTPException(status_code=400, detail="root 不能为空")
    if not dp.Path(root).is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在: {root}")
    # 根目录变了：该市场的 catalog/root 缓存作废，下次请求重扫
    ttl_invalidate(("api_server", "dp_catalog", market))
    ttl_invalidate(("api_server", "dp_root", market))
    return {"success": True, "data": {"market": market, "root": dp.set_data_root(market, root)}}


@app.get("/api/data-platform/{market}/scan")
def dp_scan(market: str, root: Optional[str] = Query(None)):
    """文件夹预检：识别 root 下的数据集（文件数/大小），不改变当前根。"""
    if market not in _DP_MARKETS:
        raise HTTPException(status_code=400, detail=f"未知市场: {market}")
    from backend.services import data_platform as dp

    return {"success": True, "data": dp.scan_folder(market, root or "")}


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
