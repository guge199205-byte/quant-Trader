"""通达信（TDX）桥 Broker：Quant-Trader 直连 8550 桥（Windows 交易机）实盘下单。

桥协议（brokers/tdx-bridge/src/api/routes.py，实测）：
  POST /api/v1/plans/execute   下单（TradePlan{plan_id,account,account_type,orders[]}）
  POST /api/v1/account/query   资产+持仓（account 可空，桥 resolve 默认账户）
  POST /api/v1/orders/query    当日委托查询（无历史接口）
  POST /api/v1/orders/cancel   撤单
  POST /api/v1/tdx/call        JSON-RPC 透传（get_market_data/get_market_snapshot 等）
  GET  /api/v1/health          健康检查（免鉴权）
  认证：Authorization: Bearer <token>

配置（.env）：
  TDX_BRIDGE_URL=http://<tdx-bridge-ip>:8550
  TDX_BRIDGE_TOKEN=<64-hex>
  TDX_ACCOUNT=       # 可留空，桥 resolve_account_id 解析默认账户
  TDX_ACCOUNT_TYPE=stock

安全：实盘下单必须过风控（见 docs/ARCHITECTURE_UPGRADE.md §4）。
断线保险：桥 IP 变动（DHCP）导致连接失败时，幂等请求自动触发局网 /24
健康扫描（bridge_discovery）换址重试一次；下单/撤单不自动重试（防重复）。
桥状态码：0=REJECTED 1=SUBMITTED 2=PARTIAL_FILL 3=FILLED 4=PARTIAL_CANCELLED 5=CANCELLED
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_tools.brokers.base import Broker, BrokerError

# 设置页（/api/tdx/config POST）保存的运行时覆盖，优先于 .env；
# config/ 目录容器与宿主机共用挂载，cron 侧与本模块同源解析
_OVERRIDE_FILE = Path(__file__).resolve().parents[2] / "config" / "tdx_bridge.json"


def bridge_overrides() -> Dict[str, str]:
    """读取 config/tdx_bridge.json 的桥连接覆盖（bridge_url/bridge_token）。"""
    try:
        data = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
        return {k: str(v).strip() for k, v in (data or {}).items()
                if k in ("bridge_url", "bridge_token") and str(v or "").strip()}
    except (OSError, json.JSONDecodeError):
        return {}


class TdxBridgeBroker(Broker):
    """通达信桥。"""

    name = "tdx"
    markets = "cn"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        ov = bridge_overrides()
        self.bridge_url = (self.config.get("bridge_url") or ov.get("bridge_url")
                           or os.getenv("TDX_BRIDGE_URL", "")).rstrip("/")
        self.token = (self.config.get("token") or ov.get("bridge_token")
                      or os.getenv("TDX_BRIDGE_TOKEN", ""))
        self.account = self.config.get("account") or os.getenv("TDX_ACCOUNT", "")
        self.account_type = self.config.get("account_type") or os.getenv("TDX_ACCOUNT_TYPE", "tdx")
        self._disc_tried = False  # 断线自动发现每实例至多一次（防同进程重复扫网）
        if not self.bridge_url:
            raise BrokerError("TDX 桥未配置：请设置 TDX_BRIDGE_URL（.env）")

    # ---------- 交易（移植自 quantmind broker_client.py） ----------

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post(self, path: str, payload: Dict[str, Any], timeout: float,
              idempotent: bool = True) -> Dict[str, Any]:
        """统一 POST + 断线自动发现重试（多重保险，见 bridge_discovery）。

        幂等读首次传输层失败（连接/超时）→ 触发桥 IP 自动发现（局网 /24 扫
        8550 健康端点）→ 换址重试一次；非幂等（下单/撤单）绝不自动重试——
        超时≠失败，重复下单不可接受。应用层错误（HTTP 4xx/5xx）不触发发现
        （URL 本身是通的，服务端问题另查）。"""
        import requests

        try:
            resp = requests.post(f"{self.bridge_url}{path}", json=payload,
                                 headers=self._headers(), timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            raise
        except requests.RequestException as exc:
            if not idempotent:
                raise
            found = self._discover_or_none()
            if found and found != self.bridge_url:
                self.bridge_url = found
                try:
                    resp = requests.post(f"{self.bridge_url}{path}", json=payload,
                                         headers=self._headers(), timeout=timeout)
                    resp.raise_for_status()
                    return resp.json()
                except requests.RequestException:
                    pass  # 换址仍失败 → 抛原始异常（贴近故障起点）
            raise

    def _discover_or_none(self) -> Optional[str]:
        """桥失联时自动发现：env/覆盖快探 → 局网扫描（有跨进程冷却），
        命中即全链路收敛（写 config/tdx_bridge.json）。失败返回 None。"""
        if self._disc_tried:
            return None
        self._disc_tried = True
        try:
            from agent_tools.brokers.bridge_discovery import resolve_bridge

            env_url = (os.getenv("TDX_BRIDGE_URL") or "").rstrip("/")
            return resolve_bridge(env_url=env_url, current_url=self.bridge_url) or None
        except Exception:  # noqa: BLE001 发现失败不掩盖原始连接错误
            return None

    def tdx_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """通用透传：POST /api/v1/tdx/call（桥白名单方法，返回 result 解包）。

        用于盘中五档等数据：get_market_snapshot 返回 Buyp/Buyv/Sellp/Sellv 各 5 档。
        """
        data = self._post("/api/v1/tdx/call",
                          {"method": method, "params": params or {}}, 8)
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}

    def _place_order(self, symbol: str, side: str, volume: int,
                     price: Optional[float] = None) -> Dict[str, Any]:
        """经桥下单（/api/v1/plans/execute，通达信客户端执行）。"""
        import time

        import requests

        # account 可空：桥 resolve_account_id 会解析默认账户（实测）
        # 审批门：approval_required=true 时拒绝（backend.yaml risk 段）
        try:
            from agent_tools.risk import RiskPolicy

            if RiskPolicy.from_backend_config().approval_required:
                raise BrokerError("风控审批门开启（approval_required=true），实盘下单被拒绝")
        except BrokerError:
            raise
        except Exception:
            pass

        plan_id = f"baymax_{int(time.time())}_{os.getpid()}"
        payload = {
            "plan_id": plan_id,
            "account": self.account,
            "account_type": self.account_type,
            "source": "baymax-trader",
            "orders": [{
                "stock_code": symbol,
                "side": side,
                "volume": int(volume),
                "order_type": "limit" if price else "market",
                "price_type": 0 if price else 1,
                "price": float(price) if price else None,
            }],
        }
        try:
            data = self._post("/api/v1/plans/execute", payload, 15, idempotent=False)
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥下单失败: {exc}") from exc
        orders = data.get("orders") or []
        first = orders[0] if orders else {}
        status = first.get("status", data.get("status", "unknown"))
        if status in ("rejected", "error"):
            raise BrokerError(first.get("message") or data.get("message") or "TDX 下单被拒")
        return {
            "order_id": first.get("order_id", ""),
            "status": status,
            "message": first.get("message", "TDX 已受理"),
            "plan_id": plan_id,
        }

    def _account_query(self) -> Dict[str, Any]:
        """POST /api/v1/account/query → {account_id, asset, positions, channel_used}"""
        import requests

        try:
            return self._post("/api/v1/account/query",
                              {"account": self.account,
                               "account_type": self.account_type}, 15)
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥账户查询失败: {exc}") from exc

    def get_positions(self, signature: str, today_date: str) -> Dict[str, float]:
        """实盘持仓 {symbol: total_volume}（桥 account/query 返回 Code/Cbj/TotalVol/CanUseVol）"""
        data = self._account_query()
        positions = {}
        for p in data.get("positions") or []:
            code = p.get("stock_code", "")
            if code:
                positions[code] = float(p.get("total_volume") or 0)
        return positions

    def get_cash(self, signature: str, today_date: str) -> float:
        """实盘可用资金（桥 asset.cash）"""
        data = self._account_query()
        return float((data.get("asset") or {}).get("cash") or 0)

    def get_orders(self, stock_code: str = "", cancelable_only: bool = False) -> List[Dict[str, Any]]:
        """当日委托查询（桥只支持当日，无历史接口）"""
        import requests

        try:
            data = self._post("/api/v1/orders/query",
                              {"account": self.account,
                               "account_type": self.account_type,
                               "stock_code": stock_code,
                               "cancelable_only": cancelable_only}, 15)
            return (data or {}).get("orders") or []
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥委托查询失败: {exc}") from exc

    def cancel_order(self, stock_code: str, order_id: str) -> Dict[str, Any]:
        """撤单（当日可撤委托）——非幂等：超时≠成功，不自动重试。"""
        import requests

        try:
            return self._post("/api/v1/orders/cancel",
                              {"account": self.account,
                               "account_type": self.account_type,
                               "stock_code": stock_code,
                               "order_id": order_id}, 15, idempotent=False)
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥撤单失败: {exc}") from exc

    def buy(self, signature: str, today_date: str, symbol: str, amount: int,
            price: Optional[float] = None) -> Dict[str, Any]:
        return self._place_order(symbol, "buy", amount, price)

    def sell(self, signature: str, today_date: str, symbol: str, amount: int,
             price: Optional[float] = None) -> Dict[str, Any]:
        return self._place_order(symbol, "sell", amount, price)

    # ---------- 行情（桥协议可直接用） ----------

    def get_quote(self, symbol: str, date: str, market: str = "cn") -> Optional[Dict[str, Any]]:
        klines = self.get_klines(symbol, date, date, interval="daily", market=market)
        return klines[-1] if klines else None

    def get_klines(self, symbol: str, start: str = "", end: str = "",
                   interval: str = "daily", market: str = "cn") -> List[Dict[str, Any]]:
        """经 8550 桥拉 K 线（POST /api/v1/tdx/call get_market_data）。
        日K(1d)/周K(1w) 支持，分钟线不支持（桥限制）。
        返回 [{"date","open","high","low","close","volume","amount"}]，按日期升序。
        """
        import requests

        period = {"daily": "1d", "weekly": "1w"}.get(interval, interval)
        # 实测（桥调用日志 18328）：参数名是 stock_list（列表），不是 stock_code
        params: Dict[str, Any] = {
            "stock_list": [symbol],
            "period": period,
            "dividend_type": "front",  # 前复权，与本地价格数据口径一致
            "count": 250,
        }
        try:
            data = self._post("/api/v1/tdx/call",
                              {"method": "get_market_data", "params": params}, 20)
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥请求失败: {exc}") from exc
        if not data.get("success", True):
            err = data.get("error") or {}
            raise BrokerError(f"TDX 桥返回错误: {err.get('message', str(data)[:200])}")
        # tdx/call 返回 {success, result: {ErrorId, Value: {symbol: {...}}}}
        result = data.get("result") or {}
        if str(result.get("ErrorId", "0")) != "0":
            raise BrokerError(f"TDX 行情错误 ErrorId={result.get('ErrorId')}: {str(result)[:200]}")
        value = result.get("Value") or {}
        kline = value.get(symbol) if isinstance(value, dict) and symbol in value else result
        closes = kline.get("Close") or []
        dates = kline.get("Date") or [""] * len(closes)
        bars = []
        for i in range(len(closes)):
            bars.append({
                "date": str(dates[i]) if i < len(dates) else "",
                "open": self._at(kline.get("Open"), i),
                "high": self._at(kline.get("High"), i),
                "low": self._at(kline.get("Low"), i),
                "close": closes[i],
                "volume": self._at(kline.get("Volume"), i),
                "amount": self._at(kline.get("Amount"), i),
            })
        return bars

    @staticmethod
    def _at(lst, i):
        try:
            return lst[i] if lst and i < len(lst) else None
        except Exception:
            return None


def register() -> None:
    from agent_tools.brokers.base import registry

    registry.register(TdxBridgeBroker)


register()
