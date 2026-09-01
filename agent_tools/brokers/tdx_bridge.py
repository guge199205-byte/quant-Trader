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
        if not self.bridge_url:
            raise BrokerError("TDX 桥未配置：请设置 TDX_BRIDGE_URL（.env）")

    # ---------- 交易（移植自 quantmind broker_client.py） ----------

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def tdx_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """通用透传：POST /api/v1/tdx/call（桥白名单方法，返回 result 解包）。

        用于盘中五档等数据：get_market_snapshot 返回 Buyp/Buyv/Sellp/Sellv 各 5 档。
        """
        import requests

        resp = requests.post(f"{self.bridge_url}/api/v1/tdx/call",
                             json={"method": method, "params": params or {}},
                             headers=self._headers(), timeout=8)
        resp.raise_for_status()
        data = resp.json()
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
            resp = requests.post(f"{self.bridge_url}/api/v1/plans/execute",
                                 json=payload, headers=self._headers(), timeout=15)
            resp.raise_for_status()
            data = resp.json()
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
            resp = requests.post(f"{self.bridge_url}/api/v1/account/query",
                                 json={"account": self.account,
                                       "account_type": self.account_type},
                                 headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()
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
            resp = requests.post(f"{self.bridge_url}/api/v1/orders/query",
                                 json={"account": self.account,
                                       "account_type": self.account_type,
                                       "stock_code": stock_code,
                                       "cancelable_only": cancelable_only},
                                 headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return (resp.json() or {}).get("orders") or []
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥委托查询失败: {exc}") from exc

    def cancel_order(self, stock_code: str, order_id: str) -> Dict[str, Any]:
        """撤单（当日可撤委托）"""
        import requests

        try:
            resp = requests.post(f"{self.bridge_url}/api/v1/orders/cancel",
                                 json={"account": self.account,
                                       "account_type": self.account_type,
                                       "stock_code": stock_code,
                                       "order_id": order_id},
                                 headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()
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
            resp = requests.post(f"{self.bridge_url}/api/v1/tdx/call",
                                 json={"method": "get_market_data", "params": params},
                                 headers=self._headers(), timeout=20)
            resp.raise_for_status()
            data = resp.json()
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
