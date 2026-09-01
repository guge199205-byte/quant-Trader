"""通达信桥执行服务（BayMax 自有；复刻 quantmind trade-core 的桥服务层）。

桥连接配置 / 总览聚合（stats+account+orders）/ 实盘执行状态 / 券商接入配置。
- 桥连接: config/tdx_bridge.json 运行时覆盖（设置页保存即生效；config/ 目录容器
  与宿主机共用挂载，cron 侧 TdxBridgeBroker 同源解析）→ .env 兜底
- 券商配置: config/brokers.json，敏感字段只写不回显（读取脱敏为 *_configured 布尔）
- 桥本体（Windows 通达信客户端侧 8550 HTTP 服务）只作外部执行器调用

响应形状与前端 TradingSettings.tsx 的类型定义及 quantmind 原版对齐（裸 JSON，
不经 {success,data} 信封——前端 getJson 直接取 res.data）。
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
BROKERS_FILE = ROOT / "config" / "brokers.json"
LIVE_TRADE_GLOB = "live_trade_*.jsonl"
CN_TZ = ZoneInfo("Asia/Shanghai")
BRIDGE_TIMEOUT = 3.0

# 券商接入字段表（与前端 TradingSettings BROKER_META 对齐；True=敏感只写不回显）
BROKER_FIELDS = {
    "tiger": {"tiger_id": False, "rsa_private_key": True, "account": False},
    "futu": {"opend_host": False, "opend_port": False,
             "trade_pwd_md5": True, "trade_env": False},
    "ib": {"gateway_host": False, "gateway_port": False, "client_id": False},
}
BROKER_LABELS = {"tiger": "老虎证券", "futu": "富途证券", "ib": "盈透证券(IB)"}


def effective_bridge() -> tuple:
    """生效的桥连接 (url, token)：config/tdx_bridge.json 覆盖 → .env 兜底。"""
    from agent_tools.brokers.tdx_bridge import bridge_overrides

    ov = bridge_overrides()
    url = ov.get("bridge_url") or os.getenv("TDX_BRIDGE_URL", "").strip()
    token = ov.get("bridge_token") or os.getenv("TDX_BRIDGE_TOKEN", "").strip()
    return url.rstrip("/"), token


def _bridge_health(url: str) -> dict:
    """GET /api/v1/health（免鉴权，3s 超时）；失败返回 {"error": ...}。"""
    import requests

    try:
        r = requests.get(f"{url}/api/v1/health", timeout=BRIDGE_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def get_tdx_config() -> dict:
    """桥配置状态（不返回 token 明文；形状对齐 quantmind TdxConfigResponse）。"""
    url, token = effective_bridge()
    health = _bridge_health(url) if url and token else None
    return {
        "enabled": bool(url and token),
        "bridge_url": url,
        "bridge_token_configured": bool(token),
        "real_trading_enabled": bool(url and token),
        "broker_type": "tdx",
        "health": health,
    }


def save_bridge_config(bridge_url: str | None, bridge_token: str | None) -> dict:
    """保存桥连接覆盖（只写非空字段；token 只写不回显）。"""
    from agent_tools.brokers.tdx_bridge import bridge_overrides

    ov = bridge_overrides()
    if bridge_url is not None and bridge_url.strip():
        ov["bridge_url"] = bridge_url.strip().rstrip("/")
    if bridge_token is not None and bridge_token.strip():
        ov["bridge_token"] = bridge_token.strip()
    path = ROOT / "config" / "tdx_bridge.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"success": True,
            "message": "通达信桥配置已更新（config/tdx_bridge.json，执行链路即时生效）"}


def _bridge_post(url: str, path: str, headers: dict) -> dict:
    import requests

    r = requests.post(f"{url}{path}", json={}, headers=headers, timeout=BRIDGE_TIMEOUT)
    if r.status_code != 200:
        return {}
    payload = r.json()
    return payload if isinstance(payload, dict) else {}


def bridge_overview() -> dict:
    """桥总览：stats + 账户资产/持仓 + 当日委托（形状对齐 quantmind /tdx/overview）。

    桥不可达时 available=false + error，不阻断前端渲染（前端 8s 轮询）。
    """
    import requests

    url, token = effective_bridge()
    if not url or not token:
        return {"available": False, "error": "桥地址或 token 未配置"}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        stats_r = requests.get(f"{url}/api/v1/stats", headers=headers, timeout=BRIDGE_TIMEOUT)
        stats_data = {}
        if stats_r.status_code == 200:
            payload = stats_r.json()
            stats_data = payload.get("data") or payload if isinstance(payload, dict) else {}
        account_data = _bridge_post(url, "/api/v1/account/query", headers)
        orders_data = _bridge_post(url, "/api/v1/orders/query", headers)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}

    account = account_data.get("asset") or {}
    positions = account_data.get("positions") or []
    orders = orders_data.get("orders") or []
    cache = stats_data.get("cache") or {}
    security = stats_data.get("security") or {}
    return {
        "available": True,
        "bridge": {
            "hostname": stats_data.get("hostname"),
            "local_ips": stats_data.get("local_ips") or [],
            "bridge_url": stats_data.get("bridge_url") or url,
            "port": stats_data.get("port"),
            "tdx_connected": bool(stats_data.get("tdx_connected")),
            "server_time": stats_data.get("server_time"),
            "token_configured": bool(stats_data.get("token_configured")),
        },
        "account": {
            "currency": account.get("currency"),
            "balance": account.get("balance"),
            "cash": account.get("cash"),
            "asset": account.get("asset"),
            "market_value": account.get("market_value"),
            "position_count": len(positions),
        },
        "positions": positions,
        "orders": orders,
        "cache": {
            "stock_info": cache.get("stock_info", 0),
            "kline": cache.get("kline", 0),
            "market_snapshot": cache.get("market_snapshot", 0),
            "mem_hit_rate": cache.get("mem_hit_rate", 0.0),
            "mem_entries": cache.get("mem_entries", 0),
        },
        "security": {
            "banned_ips": security.get("banned_ips", 0),
            "active_ips": security.get("active_ips", 0),
        },
    }


def _last_live_trade() -> dict | None:
    """最近一次实盘调仓记录（logs/live_trade_*.jsonl 最后一行）。"""
    logs_dir = ROOT / "logs"
    files = sorted(logs_dir.glob(LIVE_TRADE_GLOB))
    for f in reversed(files):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            return {"ts": rec.get("ts"), "mode": rec.get("mode"),
                    "agent": rec.get("agent"), "code": rec.get("code")}
        if lines:  # 该文件有内容但全是坏行 → 继续找更早文件
            continue
    return None


def real_trading_status() -> dict:
    """BayMax 实盘执行状态：A股=通达信桥 + 每日 llm_trade 模型自主调仓。

    status: running=桥健康 / degraded=桥配置了但不可达 / stopped=未配置。
    （形状兼容前端 RealTradingStatus；额外字段 last_live_trade/bridge_url 供展示）
    """
    url, token = effective_bridge()
    if not url or not token:
        status = "stopped"
    else:
        try:
            import requests

            ok = requests.get(f"{url}/api/v1/health",
                              timeout=BRIDGE_TIMEOUT).status_code == 200
            status = "running" if ok else "degraded"
        except Exception:  # noqa: BLE001
            status = "degraded"
    return {
        "status": status,
        "mode": "REAL",  # 桥执行=实盘；前端按 REAL/SIMULATION 渲染「实盘」徽标
        "strategy": {"id": "live_llm_trade",
                     "name": "模型自主调仓（每日 09:35 候选池决策 → 桥执行）"},
        "execution_config": {"max_buy_drop": None, "stop_loss": None},
        "bridge_url": url,
        "last_live_trade": _last_live_trade(),
        "user_id": "baymax-trader",
        "server_time": datetime.now(CN_TZ).isoformat(),
    }


# ---------- 券商接入配置（config/brokers.json，敏感只写不回显） ----------

def _read_brokers() -> dict:
    try:
        data = json.loads(BROKERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_brokers(data: dict) -> None:
    BROKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BROKERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")


def get_broker_config(broker: str) -> dict:
    """读取券商配置（敏感字段脱敏为 {name}_configured 布尔）。"""
    broker = (broker or "").lower().strip()
    if broker not in BROKER_FIELDS:
        raise ValueError(f"未知券商: {broker}")
    stored = _read_brokers().get(broker) or {}
    fields: dict = {}
    for name, sensitive in BROKER_FIELDS[broker].items():
        value = str(stored.get(name, "") or "")
        if sensitive:
            fields[f"{name}_configured"] = bool(value)
        else:
            fields[name] = value
    return {"success": True, "broker": broker,
            "label": BROKER_LABELS[broker], "fields": fields}


def update_broker_config(broker: str, values: dict) -> dict:
    """更新券商配置。未提供的敏感字段保持原值；空值清除。"""
    broker = (broker or "").lower().strip()
    if broker not in BROKER_FIELDS:
        raise ValueError(f"未知券商: {broker}")
    unknown = set(values or {}) - set(BROKER_FIELDS[broker])
    if unknown:
        raise ValueError(f"无效字段: {', '.join(sorted(unknown))}")
    data = _read_brokers()
    stored = dict(data.get(broker) or {})
    for name, value in (values or {}).items():
        text = str(value or "").strip()
        if text:
            stored[name] = text
        else:
            stored.pop(name, None)
    data[broker] = stored
    _write_brokers(data)
    return get_broker_config(broker)


async def test_broker_connection(broker: str) -> dict:
    """测试券商连通性（futu 走 BayMax 自有 OpenD 直连；tiger/ib 尚未实现）。"""
    broker = (broker or "").lower().strip()
    if broker not in BROKER_FIELDS:
        raise ValueError(f"未知券商: {broker}")
    if broker == "futu":
        from backend.services import futu_live

        try:
            out = await futu_live.query_account_both()
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "message": f"FutuOpenD 未连接：{exc}；请确认 OpenD 已启动并登录"}
        sim = out.get("simulate") or {}
        real = out.get("real") or {}
        asset = float(sim.get("total_asset") or 0)
        return {"success": True,
                "message": (f"FutuOpenD 已连接（模拟 HK${asset:,.0f} / 实盘 "
                            f"${float(real.get('total_asset') or 0):,.2f}）")}
    if broker == "tiger":
        # 老虎证券：港股实盘/模拟盘通道（tigeropen SDK；账户号格式决定模拟/实盘）
        from agent_tools.brokers.tiger_bridge import TigerBridgeBroker

        try:
            b = TigerBridgeBroker(_read_brokers().get("tiger") or {})
            cash = b.get_cash(None, "")
            positions = b.get_positions(None, "", market="hk")
            try:
                from tigeropen.common.util.account_util import AccountUtil

                paper = AccountUtil.is_paper_account(b.account)
            except Exception:  # noqa: BLE001
                paper = False
            return {"success": True,
                    "message": (f"老虎证券已连接（{'模拟盘' if paper else '实盘'}，"
                                f"可用现金 ${cash:,.2f}，持仓 {len(positions)} 只）")}
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "message": f"老虎连接失败：{exc}；请确认 tiger_id/私钥/账户已填且 Tiger Open API 已开通"}
    if broker == "ib":
        # 盈透证券：IBKR Gateway（ib_insync；gateway_host/gateway_port/client_id）
        from agent_tools.brokers.ibkr_bridge import IbkrBridgeBroker

        try:
            b = IbkrBridgeBroker(_read_brokers().get("ib") or {})
            cash = b.get_cash(None, "")
            positions = b.get_positions(None, "")
            return {"success": True,
                    "message": (f"IBKR 已连接（可用现金 ${cash:,.2f}，"
                                f"持仓 {len(positions)} 只）")}
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "message": f"IBKR 连接失败：{exc}；请确认 IB Gateway 已启动并开放 API（端口 7497/7496）"}
    return {"success": False,
            "message": f"{BROKER_LABELS[broker]} 接入尚未在 BayMax 实现（当前仅保存配置）"}
