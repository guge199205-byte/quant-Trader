"""Quant-Trader API 服务（FastAPI，默认端口 8090）。

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

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from backend.config import (
    get_data_root,
    get_enabled_markets,
    load_backend_config,
)
from backend.services import agent_data
from prompts.analysis_modes import MODES, load_selection, save_selection

app = FastAPI(title="Quant-Trader API", version="0.1.0")

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
    """FIFO 重建已平仓逐笔明细（LAST 25 TRADES 表用），最新平仓在前。
    cn 实盘：并入当日通达信桥成交回报（logs/live_trade_*.jsonl 带 fill 的行）。"""
    cfg = config()
    records = agent_data.load_position_records(cfg, agent, market, limit=5000)
    closed, _, _ = agent_data.rebuild_closed_trades(cfg, market, records)
    closed.sort(key=lambda t: t["exit_date"], reverse=True)
    if market == "cn":
        closed = _merge_live_fills(agent, closed)
    return {"success": True, "data": closed[:limit]}


def _merge_live_fills(agent: str, closed: list) -> list:
    """当日实盘成交（带 fill 回报的行）并入「已完成」明细。

    成本基准（优先级）：fill 行自带 cost_price（执行路径记账时写入）→
    账本当前持仓成本（同 lot 加仓摊薄后仍准确）→ 0（完全清仓且无记录的，
    pnl 置 None 不假装有数）。名义金额 = 数量×成交价；盈亏 = (成交价-成本)×数量。
    """
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    ledger_costs: dict = {}
    try:
        ledger = json.loads((logs_dir / "live_ledger.json").read_text(encoding="utf-8"))
        for aname, rec in (ledger.get("agents") or {}).items():
            for code, pos in (rec.get("positions") or {}).items():
                cp = float((pos or {}).get("cost_price") or 0)
                if cp > 0:
                    ledger_costs.setdefault(aname, {})[code] = cp
    except (OSError, json.JSONDecodeError):
        pass
    items = list(closed)
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
            fill = rec.get("fill")
            if rec.get("agent") != agent or not fill:
                continue
            fv = int(fill.get("filled_volume") or 0)
            if fv <= 0:
                continue
            ts = rec.get("ts") or ""
            exit_price = float(fill.get("filled_price") or rec.get("price") or 0)
            entry = (float(fill.get("cost_price") or 0)
                     or ledger_costs.get(agent, {}).get(rec.get("code"), 0.0))
            notional = round(fv * exit_price, 2)
            pnl = round((exit_price - entry) * fv, 2) if entry > 0 else None
            items.append({
                "symbol": rec.get("code"),
                "exit_date": ts[:10],
                "qty": fv,
                "entry_price": entry,
                "exit_price": exit_price,
                "notional": notional,
                "fee": 0.0,
                "pnl": pnl,
                "live": True,
            })
    items.sort(key=lambda t: str(t.get("exit_date", "")), reverse=True)
    return items


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


@app.get("/api/live/news")
def live_news(tickers: str = "", hours: int = 24, limit: int = 10, keyword: str = ""):
    """盘中新闻代理 → quantmind /api/v1/news/articles（Huntly/RSS 聚合 + enrichment）。

    A 股按代码筛：tickers 逗号分隔（600519.SH,000858.SZ）。
    港股无 HK 标签文章库，改用 keyword 关键词全文搜（腾讯/恒生/港股），匹配同花顺/华尔街见闻
    等来源标题里提及港股市值/公司的文章。两者可同传（quantmind 取交集）。
    """
    base = os.getenv("NEWS_API_BASE", "http://172.17.0.1:8000")
    import requests  # noqa: PLC0415 局部导入（与文件既有惯例一致）

    from datetime import datetime, timedelta, timezone

    hours = max(1, min(168, hours))
    limit = max(1, min(50, limit))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"since": since, "page_size": limit, "sort": "time_desc"}
    if tickers:
        params["tickers"] = tickers
    if keyword:
        params["keyword"] = keyword
    try:
        resp = requests.get(f"{base}/api/v1/news/articles", params=params, timeout=10)
        resp.raise_for_status()
        # 统一信封（与 /api/live/ledger 等一致）：前端 unwrap 依赖 {success, data}
        return {"success": True, "data": resp.json()}
    except Exception as e:  # noqa: BLE001
        return {"success": True, "data": {"articles": [], "error": f"{type(e).__name__}: {e}"}}


# ---------- 富途港股直连（OpenD 网关；BayMax 自有实现，不经外部平台） ----------


def _futu_env(env: str) -> str:
    return env.upper() if env.upper() in {"REAL", "SIMULATE"} else "SIMULATE"


@app.get("/api/futu/account")
async def futu_account(env: str = "SIMULATE"):
    """富途账户（资产/持仓）。env=REAL 实盘 / SIMULATE 模拟（默认）。"""
    from backend.services import futu_live

    try:
        out = await futu_live.query_account(_futu_env(env))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途查询失败: {e}"}
    return {"success": True, "data": out}


@app.get("/api/futu/account-both")
async def futu_account_both():
    """一次握手查 REAL+SIMULATE 两套账户（省一次 RSA 握手，降实盘 tab 延迟）。"""
    from backend.services import futu_live

    try:
        out = await futu_live.query_account_both()
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途查询失败: {e}"}
    return {"success": True, "data": out}


@app.get("/api/futu/orders")
async def futu_orders(env: str = "SIMULATE"):
    """当日订单历史（order_list_query）→ Live 成交 tab。"""
    from backend.services import futu_live

    try:
        out = await futu_live.query_orders(_futu_env(env))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途查询失败: {e}"}
    return {"success": True, "data": out}


@app.get("/api/futu/closed")
async def futu_closed(env: str = "SIMULATE"):
    """已平仓行（qty==0 且 realized_pl!=0）→ Live「已完成」tab 港股。
    解包 {closed: [...]} 为裸列表（前端按数组消费）。"""
    from backend.services import futu_live

    try:
        out = await futu_live.query_closed(_futu_env(env))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途查询失败: {e}"}
    data = out.get("closed") if isinstance(out, dict) else out
    return {"success": True, "data": data or []}


@app.get("/api/futu/snapshot")
async def futu_snapshot(codes: str = ""):
    """实时快照（现价/昨收/当日涨跌）→ 港股盘中分析循环取价。codes 逗号分隔。"""
    from backend.services import futu_live

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"success": False, "error": "codes 参数为空"}
    try:
        out = await futu_live.query_snapshot(code_list)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途快照失败: {e}"}
    return {"success": True, "data": out}


# ---------- 富途下单/撤单（HK/US 实盘/模拟；OpenD 交易连接） ----------

@app.post("/api/futu/place")
async def futu_place(payload: dict = Body(...)):
    """富途下单（env=SIMULATE/REAL，market=HK/US）。order: {code, price, quantity, order_type, trd_side}"""
    from backend.services import futu_live

    env = str(payload.get("env") or "SIMULATE")
    market = str(payload.get("market") or "HK")
    order = payload.get("order") or {}
    if not order.get("code") or not order.get("quantity"):
        return {"success": False, "error": "order.code / order.quantity 必填"}
    try:
        out = await futu_live.place_order(order, env, market)
        return {"success": bool(out.get("success")), "data": out}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途下单失败: {e}"}


@app.post("/api/futu/cancel")
async def futu_cancel(payload: dict = Body(...)):
    """富途撤单（env/market 同上）。"""
    from backend.services import futu_live

    env = str(payload.get("env") or "SIMULATE")
    market = str(payload.get("market") or "HK")
    order_id = str(payload.get("order_id") or "")
    if not order_id:
        return {"success": False, "error": "order_id 必填"}
    try:
        out = await futu_live.cancel_order(order_id, env, market)
        return {"success": bool(out.get("success")), "data": out}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"富途撤单失败: {e}"}


# ---------- 市场→交易所映射（总控可配：哪个市场用哪个券商） ----------

_BROKER_MARKET_FILE = Path(__file__).resolve().parents[1] / "config" / "broker_market.json"
_BROKER_MARKET_DEFAULT = {"cn": "tdx", "hk": "tiger", "us": "ibkr"}
_BROKER_CHOICES = {"cn": ["tdx"], "hk": ["tiger", "ibkr", "futu"], "us": ["ibkr", "tiger", "futu"]}


def load_broker_market() -> dict:
    try:
        data = json.loads(_BROKER_MARKET_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: (v if v in _BROKER_CHOICES.get(k, []) else _BROKER_MARKET_DEFAULT[k])
                    for k, v in data.items() if k in _BROKER_MARKET_DEFAULT}
    except (OSError, json.JSONDecodeError):
        pass
    return dict(_BROKER_MARKET_DEFAULT)


@app.get("/api/broker-market")
def get_broker_market():
    """市场→交易所映射（前端总控选择器读取）。"""
    return {"success": True, "data": {"mapping": load_broker_market(),
                                      "choices": _BROKER_CHOICES}}


@app.put("/api/broker-market")
def put_broker_market(payload: dict = Body(...)):
    """保存市场→交易所映射。values: {market: broker}"""
    values = payload.get("values") or {}
    current = load_broker_market()
    for mkt, brk in values.items():
        if mkt in _BROKER_CHOICES and brk in _BROKER_CHOICES[mkt]:
            current[mkt] = brk
    _BROKER_MARKET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BROKER_MARKET_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return {"success": True, "data": {"mapping": current}}


# ---------- 老虎证券（港股实盘通道，tigeropen SDK；凭据 config/brokers.json tiger） ----------

def _tiger_broker(env: str = "SIMULATE"):
    """老虎 broker 实例（凭据从 config/brokers.json tiger 段读取）。
    env=REAL 用实盘账户（real_account），默认模拟盘（account）。"""
    from agent_tools.brokers.tiger_bridge import TigerBridgeBroker
    from backend.services.tdx_live import _read_brokers

    cfg = dict(_read_brokers().get("tiger") or {})
    if env.upper() == "REAL" and cfg.get("real_account"):
        cfg["account"] = cfg["real_account"]
    return TigerBridgeBroker(cfg)


@app.get("/api/tiger/account")
async def tiger_account(env: str = "SIMULATE", market: str = "hk"):
    """老虎账户资产+持仓（默认模拟盘；env=REAL 实盘；market=hk/us）→ Live 实盘面板。"""
    try:
        broker = _tiger_broker(env)
        cash = broker.get_cash(None, "")
        positions = broker.get_positions(None, "", market=market)
        return {"success": True, "data": {
            "cash": cash,
            "positions": positions,
            "position_count": len(positions),
            "broker": "tiger",
            "env": "REAL" if env.upper() == "REAL" else "SIMULATE",
            "account": broker.account,
            "market": market,
        }}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"老虎查询失败: {e}"}


@app.get("/api/tiger/orders")
async def tiger_orders(limit: int = Query(50, ge=1, le=500), env: str = "SIMULATE",
                         market: str = "hk"):
    """老虎当日委托（含成交/在途；market=hk/us）→ Live 成交 tab。"""
    try:
        broker = _tiger_broker(env)
        return {"success": True, "data": broker.get_orders(market=market, limit=limit)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"老虎委托查询失败: {e}"}


@app.get("/api/tiger/transactions")
async def tiger_transactions(start: str = Query("2026-08-01"),
                             end: str = Query("2099-12-31"),
                             limit: int = Query(200, ge=1, le=1000),
                             env: str = "SIMULATE", market: str = "hk"):
    """老虎历史成交（get_filled_orders，日期范围；market=hk/us）→ 「已完成」历史数据源。"""
    try:
        broker = _tiger_broker(env)
        return {"success": True,
                "data": broker.get_filled_orders(start, end, market=market, limit=limit)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"老虎历史成交查询失败: {e}"}


# ---------- 盈透证券（IBKR Gateway，ib_insync；凭据 config/brokers.json ib） ----------

def _ibkr_broker():
    from agent_tools.brokers.ibkr_bridge import IbkrBridgeBroker
    from backend.services.tdx_live import _read_brokers

    return IbkrBridgeBroker(_read_brokers().get("ib") or {})


@app.get("/api/ibkr/account")
async def ibkr_account():
    """IBKR 账户现金+持仓 → Live 美股实盘面板。"""
    try:
        broker = _ibkr_broker()
        cash = broker.get_cash(None, "")
        positions = broker.get_positions(None, "")
        return {"success": True, "data": {
            "cash": cash,
            "positions": positions,
            "position_count": len(positions),
            "broker": "ibkr",
        }}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"IBKR 查询失败: {e}"}


@app.get("/api/ibkr/orders")
async def ibkr_orders(limit: int = Query(50, ge=1, le=500)):
    """IBKR 在途委托 → Live 美股成交 tab。"""
    try:
        broker = _ibkr_broker()
        return {"success": True, "data": broker.get_orders(limit=limit)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"IBKR 委托查询失败: {e}"}


# ---------- 通达信桥执行服务（BayMax 自有，复刻 quantmind 桥服务层） ----------
# 响应为裸 JSON（不经 {success,data} 信封）——前端 TradingSettings getJson 直接取
# res.data，形状与 quantmind 原版对齐。滚动买卖/止损止盈/推送选股仍走 /api/quantmind
# 代理（那些是 quantmind 推理引擎的控制器，引擎在 quantmind 侧运行）。


@app.get("/api/tdx/config")
def tdx_config_get():
    """桥配置状态（不返回 token 明文）+ 桥健康检查。"""
    from backend.services import tdx_live

    return tdx_live.get_tdx_config()


@app.post("/api/tdx/config")
def tdx_config_post(body: dict = Body(...)):
    """保存桥连接覆盖（config/tdx_bridge.json，只写非空字段，token 只写不回显）。"""
    from backend.services import tdx_live

    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="请求体需为 JSON 对象")
    return tdx_live.save_bridge_config(body.get("bridge_url"), body.get("bridge_token"))


@app.get("/api/tdx/overview")
def tdx_overview_get():
    """桥总览：stats + 账户资产/持仓 + 当日委托（桥不可达时 available=false）。"""
    from backend.services import tdx_live

    return tdx_live.bridge_overview()


@app.get("/api/real-trading/status")
def real_trading_status_get():
    """BayMax 实盘执行状态（桥健康 + 最近一次 llm_trade 调仓记录）。"""
    from backend.services import tdx_live

    return tdx_live.real_trading_status()


@app.get("/api/broker-config/{broker}")
def broker_config_get(broker: str):
    """读取券商接入配置（敏感字段脱敏为 *_configured）。"""
    from backend.services import tdx_live

    try:
        return tdx_live.get_broker_config(broker)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.put("/api/broker-config/{broker}")
def broker_config_put(broker: str, body: dict = Body(...)):
    """更新券商接入配置（未提供的敏感字段保持原值，空值清除）。"""
    from backend.services import tdx_live

    values = (body or {}).get("values")
    if not isinstance(values, dict):
        raise HTTPException(status_code=422, detail="请求体需为 {values: {...}}")
    try:
        return tdx_live.update_broker_config(broker, values)
    except ValueError as e:
        # 未知券商(404) / 无效字段(422) 共用 ValueError，按内容区分
        code = 404 if str(e).startswith("未知券商") else 422
        raise HTTPException(status_code=code, detail=str(e)) from e


@app.post("/api/broker-config/{broker}/test")
async def broker_config_test(broker: str, body: dict | None = Body(None)):
    """测试券商连通性（futu 走 BayMax 自有 OpenD 直连）。"""
    from backend.services import tdx_live

    try:
        return await tdx_live.test_broker_connection(broker)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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


# ---------- 实盘同步数据（quantmind PG，同机通达信实盘账户） ----------
# quantmind 的 trade 服务实时采集通达信桥账户/持仓（real_account_snapshots，每 30s）、
# 日终账本（real_account_ledger_daily_snapshots）与 L2 因子（tdx_l2_snapshot，每 60s）。
# Quant-Trader 只读同步展示与分析用；连接失败降级返回错误，不影响主链路。
# PG 连接配置在 .env：QM_PG_HOST/PORT/DB/USER/PASSWORD（默认 172.17.0.1:5432）。

_QM_PG = None


def _qm_pg_conn():
    global _QM_PG
    if _QM_PG is None:
        _QM_PG = {
            "host": os.getenv("QM_PG_HOST", "172.17.0.1"),
            "port": int(os.getenv("QM_PG_PORT", "5432")),
            "dbname": os.getenv("QM_PG_DB", "quantmind"),
            "user": os.getenv("QM_PG_USER", "quantmind"),
            "password": os.getenv("QM_PG_PASSWORD", ""),
            "connect_timeout": 3,
        }
    import psycopg2

    return psycopg2.connect(**_QM_PG)


@app.get("/api/live/real-account")
def live_real_account():
    """quantmind 实盘账户最新快照（总资产/现金/持仓），每 30s 由 TDX 桥同步。"""
    import psycopg2.extras

    try:
        conn = _qm_pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT snapshot_at, total_asset, cash, market_value,
                           today_pnl_raw, total_pnl_raw, payload_json
                    FROM real_account_snapshots
                    ORDER BY snapshot_at DESC LIMIT 1
                    """
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("real-account 读取失败: %s", e)
        return {"success": False, "error": f"quantmind PG 读取失败: {e}"}
    if not row:
        return {"success": True, "data": None}
    payload = row.get("payload_json") or {}
    positions = payload.get("positions") or []
    # 补股票中文名（CN_STOCK_NAMES 覆盖 quantdb instrument_detail 全市场）
    try:
        from tools.stock_names import CN_STOCK_NAMES

        names = dict(CN_STOCK_NAMES)
    except Exception:  # noqa: BLE001
        names = {}
    names = {**names, **_quantdb_stock_names()}
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not p.get("name"):
            p["name"] = names.get(sym, "")
    return {"success": True, "data": {
        "ts": row["snapshot_at"].isoformat() if row["snapshot_at"] else None,
        "total_asset": row["total_asset"],
        "cash": row["cash"],
        "market_value": row["market_value"],
        "today_pnl": row["today_pnl_raw"],
        "total_pnl": row["total_pnl_raw"],
        "positions": positions,
    }}


@app.get("/api/live/real-ledger")
def live_real_ledger():
    """quantmind 日终账本（每日总资产/日收益），供净值曲线与日收益展示。"""
    import psycopg2.extras

    try:
        conn = _qm_pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT snapshot_date, total_asset, cash, market_value,
                           daily_return_pct, total_return_pct, position_count, source
                    FROM real_account_ledger_daily_snapshots
                    ORDER BY snapshot_date
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("real-ledger 读取失败: %s", e)
        return {"success": False, "error": f"quantmind PG 读取失败: {e}"}
    return {"success": True, "data": [
        {
            "date": r["snapshot_date"].isoformat() if r["snapshot_date"] else None,
            "total_asset": r["total_asset"],
            "cash": r["cash"],
            "market_value": r["market_value"],
            "daily_return_pct": r["daily_return_pct"],
            "total_return_pct": r["total_return_pct"],
            "position_count": r["position_count"],
            "source": r["source"],
        }
        for r in rows
    ]}


@app.get("/api/live/l2-factors")
def live_l2_factors(
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(60, ge=1, le=500),
):
    """L2 因子快照（quantmind tdx_l2_snapshot）：最近 limit 条，可选 symbol 过滤。
    每 60s 由 TDX L2 采集任务更新（13 因子在 factors jsonb）。"""
    import psycopg2.extras

    try:
        conn = _qm_pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # JOIN stocks 取股票名称：stocks 主键 symbol 为后缀格式
                # （600097.SH），与 tdx_l2_snapshot 的 stock_code 一致；snapshot
                # 的 symbol 是 SH600097 前缀格式，不能用于关联
                join_sql = """
                    SELECT s.ts, s.symbol, s.stock_code, s.now_price, s.factors,
                           st.name
                    FROM tdx_l2_snapshot s
                    LEFT JOIN stocks st ON st.symbol = s.stock_code
                """
                if symbol:
                    cur.execute(join_sql + " WHERE s.symbol = %s ORDER BY s.ts DESC LIMIT %s", (symbol, limit))
                else:
                    cur.execute(join_sql + " ORDER BY s.ts DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("l2-factors 读取失败: %s", e)
        return {"success": False, "error": f"quantmind PG 读取失败: {e}"}
    return {"success": True, "data": [
        {
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "symbol": r["symbol"],
            "stock_code": r["stock_code"],
            "name": r["name"],
            "now_price": r["now_price"],
            "factors": r["factors"] or {},
        }
        for r in rows
    ]}


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
            if rec.get("mode") in ("execute", "execute_intraday", "sell",
                                   "fill_confirm") and (
                "result" in rec or "error" in rec or "fill" in rec
                or "pending" in rec or rec.get("mode") == "fill_confirm"
            ):
                # fill_confirm（reconcile 兜底确认的成交）没有嵌套 result/fill，
                # 平铺 volume/price——补一层 fill 让前端 hasFill 判定放行
                # （2026-09-03：强平守护 13:00 的成交确认因此没进「成交」页）
                if rec.get("mode") == "fill_confirm" and not rec.get("fill"):
                    rec["fill"] = {"order_id": rec.get("order_id"),
                                   "filled_price": rec.get("price"),
                                   "filled_volume": rec.get("volume")}
                records.append(rec)
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)
    # 补成交价：桥当日委托 filled_price（按 order_id，回退按 code）
    price_map: dict = {}
    side_map: dict = {}
    try:
        broker = _live_broker()
        for o in broker.get_orders():
            fp = o.get("filled_price")
            if fp is None or o.get("status") != "filled":
                continue
            price_map[o.get("order_id")] = float(fp)
            price_map.setdefault(o.get("stock_code"), float(fp))
            side_map[o.get("order_id")] = str(o.get("side") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    for rec in records:
        # 桥 filled_price 是真实成交价；有订单匹配按 order_id 覆盖，否则只有
        # 日志本身没价时才按 code 兜底（08-31 买入行有自己的成交价，不能被
        # 今日同 code 卖出价污染）
        rid = ((rec.get("result") or {}).get("order_id")
               or (rec.get("fill") or {}).get("order_id"))
        if rid and rid in price_map:
            rec["price"] = price_map[rid]
        elif not rec.get("price") and price_map.get(rec.get("code")):
            rec["price"] = price_map[rec.get("code")]
        elif not rec.get("price"):
            rec["price"] = None
        # 旧日志行没有 side：按桥当日委托回报补全买卖方向
        if not rec.get("side") and rid and side_map.get(rid):
            rec["side"] = side_map[rid]
    return {"success": True, "data": records[:limit]}


# ---------- 实盘已平仓流（右侧「已完成」feed；全仓卖出的成交事件） ----------

@app.get("/api/live/closed")
def live_closed(limit: int = Query(60, ge=1, le=300)):
    """实盘已平仓事件：卖出后该 agent 该股归零 → 一笔完整平仓。

    账本只存当前快照，用 fill 成交事件倒走重建：倒走穿越一笔卖出时该
    agent 该股数量已为 0，说明这笔卖出就是清仓（卖掉了全部持仓）。
    成本取事件日志自带的 cost_price（记账时快照），无成本则 pnl 置 None。
    2026-09-03 之前「已完成」feed 一直读模拟盘平仓文件（迁移实盘后不再更新）。
    """
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    events: list = []
    for f in sorted(logs_dir.glob("live_trade_*.jsonl")):
        if any(m in f.name for m in ("_us_", "_hk_")):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("error"):
                continue
            mode = rec.get("mode")
            if mode == "fill_confirm":
                fv = int(rec.get("volume") or 0)
                fp = rec.get("price")
            else:
                fill = rec.get("fill") or {}
                fv = int(fill.get("filled_volume") or 0)
                fp = fill.get("filled_price")
            if fv <= 0 or not rec.get("side"):
                continue
            try:
                price = float(fp or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                continue
            events.append({
                "ts": rec.get("ts") or "",
                "agent": rec.get("agent"),
                "code": rec.get("code"),
                "side": str(rec["side"]).lower(),
                "vol": fv,
                "price": price,
                "cost": rec.get("cost_price"),
            })
    events.sort(key=lambda r: r["ts"], reverse=True)

    # 当前账本 → (agent, code) 数量/成本起点（倒走基准）
    qty: dict = {}
    cost_now: dict = {}
    try:
        raw = json.loads((logs_dir / "live_ledger.json").read_text(encoding="utf-8"))
        for agent, rec in (raw.get("agents") or {}).items():
            qty.setdefault(agent, {})
            cost_now.setdefault(agent, {})
            for code, p in (rec.get("positions") or {}).items():
                qty[agent][code] = int(p.get("volume") or 0)
                cost_now[agent][code] = p.get("cost_price")
    except (OSError, ValueError):
        pass

    closed: list = []
    seen: set = set()
    for e in events:
        agent, code = e["agent"], e["code"]
        if not agent or not code:
            continue
        aqty = qty.setdefault(agent, {})
        acost = cost_now.setdefault(agent, {})
        q0 = int(aqty.get(code) or 0)
        if e["side"] == "sell" and q0 == 0 and e["price"] > 0:
            key = (e["ts"], agent, code)
            if key not in seen:
                seen.add(key)
                entry = e["cost"] if e["cost"] is not None else acost.get(code)
                closed.append({
                    "ts": e["ts"], "agent": agent, "symbol": code,
                    "exit_date": str(e["ts"])[:10], "qty": e["vol"],
                    "entry_price": float(entry or 0),
                    "exit_price": e["price"],
                    "notional": round(e["price"] * e["vol"], 2),
                    "fee": 0.0,
                    "pnl": (round((e["price"] - float(entry)) * e["vol"], 2)
                            if entry not in (None, 0) else None),
                    "hold_days": None,
                })
        # 倒走复原：卖出的逆向是加回，买入的逆向是扣减
        if e["side"] == "sell":
            aqty[code] = q0 + e["vol"]
        else:
            aqty[code] = max(0, q0 - e["vol"])
    closed.sort(key=lambda r: r["ts"], reverse=True)
    return {"success": True, "data": closed[:limit]}


# ---------- 当日实时指数（顶部行情条） ----------

# A 股主流指数（通达信代码）：上证指数用 000001.SH（999999 该源不支持）
INDEX_DEFS: tuple = (
    ("000001.SH", "上证指数"),
    ("000016.SH", "上证50"),
    ("000300.SH", "沪深300"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
)

# 全球指数（腾讯 qt.gtimg.cn 实时快照）：US/HK 顶部行情条。
# 通达信桥/TdxAiData 均不提供全球指数，腾讯公开接口免费可用。
TENCENT_INDICES: dict = {
    "us": [("usDJI", "道琼斯"), ("usIXIC", "纳斯达克"), ("usINX", "标普500")],
    "hk": [("hkHSI", "恒生指数"), ("hkHSCEI", "国企指数"), ("hkHSTECH", "恒生科技")],
}


def _tencent_indices(market: str) -> list:
    """腾讯实时快照 → indices 行：v_usDJI="200~道琼斯~.DJI~最新~昨收~开~…~时间~涨跌~涨跌幅%…"。

    字段：f[1] 名称、f[2] 代码、f[3] 最新价、f[4] 昨收、f[30] 时间、f[32] 涨跌幅(%)。
    """
    import requests

    codes = ",".join(c for c, _ in TENCENT_INDICES[market])
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=8)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip('";')
        f = payload.split("~")
        if len(f) < 35:
            continue
        try:
            last = float(f[3])
            chg = float(f[32])
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        name = next((n for c, n in TENCENT_INDICES[market] if c in line), f[1])
        rows.append({"code": f[2], "name": name, "last": round(last, 2),
                     "change_pct": round(chg, 2)})
    return rows


def _bench_last_two(path: Path):
    """基准文件（AlphaVantage 格式）最后两根日线 → 单元素 indices 列表或 None。

    桥/TdxAiData 不可用时的兜底：至少让行情条还有当天基准可看。
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        series = doc.get("Time Series (Daily)") or {}
        if len(series) < 2:
            return None
        days = sorted(series.items())
        c1, c2 = float(days[-2][1].get("4. close") or 0), float(days[-1][1].get("4. close") or 0)
        if c1 <= 0 or c2 <= 0:
            return None
        name = (doc.get("Meta Data") or {}).get("2. Symbol", "INDEX")
        return [{"code": name, "name": name, "last": round(c2, 2),
                 "change_pct": round((c2 - c1) / c1 * 100, 2)}]
    except (OSError, ValueError, TypeError):
        return None


@app.get("/api/live/indices")
@ttl_cache(10.0)  # 6 次桥调用聚合；30s 轮询下 10s 限频够用
def live_indices(market: str = "cn"):
    """当日实时指数（Live 顶部行情条）。

    CN：通达信桥日K最后两根——盘中最后一根即当日实时最新价（与
    TdxAiData 快照 Now 一致），涨跌幅 = (今收 - 昨收)/昨收；桥失败
    回退 SSE50 基准文件。US：等权 NDX100 基准文件（无实时源）。
    HK：暂无数据源返回空。
    """
    root = Path(__file__).resolve().parents[1] / "data"
    if market == "cn":
        rows: list = []
        try:
            broker = _live_broker()
            for code, name in INDEX_DEFS:
                try:
                    klines = broker.get_klines(code)
                    if not klines or len(klines) < 2:
                        continue
                    c2 = float(klines[-1]["close"])
                    c1 = float(klines[-2]["close"])
                    if c1 <= 0 or c2 <= 0:
                        continue
                    rows.append({"code": code, "name": name, "last": round(c2, 2),
                                 "change_pct": round((c2 - c1) / c1 * 100, 2)})
                except Exception:  # noqa: BLE001 单只失败跳过，不拖垮整条
                    continue
        except Exception:  # noqa: BLE001 桥不可用 → 基准兜底
            rows = []
        if rows:
            return {"success": True, "data": {"indices": rows}}
        fallback = _bench_last_two(root / "A_stock" / "index_daily_sse_50.json")
        if fallback:
            return {"success": True, "data": {"indices": fallback}}
        return {"success": True, "data": {"indices": []}}
    if market == "us":
        rows = _tencent_indices("us")
        if rows:
            return {"success": True, "data": {"indices": rows}}
        fb = _bench_last_two(root / "benchmark_nasdaq100.json")
        if fb:
            return {"success": True, "data": {"indices": fb}}
        return {"success": True, "data": {"indices": []}}
    if market == "hk":
        rows = _tencent_indices("hk")
        if rows:
            return {"success": True, "data": {"indices": rows}}
        return {"success": True, "data": {"indices": []}}
    return {"success": True, "data": {"indices": []}}


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


# ---------- 比赛配置（Arena「比赛配置」tab ↔ 分析引擎 的读写口） ----------

@app.get("/api/comp-config")
def get_comp_config(market: str = "cn"):
    """比赛配置：目录（4 种配置的中文名/要求）+ 每模型当前多选（按市场分区）。

    market=cn/hk/us；不同市场各自维护模型多选，注入的系统提示词也按市场交易规则调整。
    """
    mk = (market or "cn").lower()
    return {"success": True, "data": {"market": mk, "catalog": MODES, "selection": load_selection(mk)}}


@app.put("/api/comp-config")
async def put_comp_config(request: Request, market: str = "cn"):
    """保存每模型多选（按市场分区）：body = {"selection": {模型名: [配置id, ...]}}。"""
    mk = (market or "cn").lower()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    sel = body.get("selection")
    if not isinstance(sel, dict):
        raise HTTPException(400, "selection 必须是对象 {模型名: [配置id]}")

    valid = {m["id"] for m in MODES}
    cleaned: dict = {}
    for model, ids in sel.items():
        if not isinstance(ids, list):
            raise HTTPException(400, f"{model} 的配置必须是数组")
        cleaned[model] = [i for i in ids if i in valid]
    save_selection(mk, cleaned)
    return {"success": True, "data": {"market": mk, "selection": cleaned}}


# ---------- 本地历史K线（quantus/quanthk，duckdb 直读；agent 工具数据源） ----------

@app.get("/api/local/klines")
def local_klines(symbol: str, market: str = "us", days: int = Query(60, ge=5, le=500)):
    """本地日线（quantus=美股/quanthk=港股，quantmind 数据资产日更）。
    symbol 归一化：AAPL / US.AAPL → AAPL；00700 / HK.00700 → 0700.HK。"""
    from scripts.local_klines import get_daily

    try:
        bars = get_daily(symbol, market, days=days)
        return {"success": True, "data": {"symbol": symbol, "market": market,
                                          "count": len(bars), "bars": bars[-120:]}}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"本地K线查询失败: {e}"}


def main():
    import uvicorn

    cfg = load_backend_config()
    server = cfg.get("server", {})
    uvicorn.run(app, host=server.get("host", "0.0.0.0"), port=int(server.get("port", 8090)), log_level="info")


if __name__ == "__main__":
    main()
