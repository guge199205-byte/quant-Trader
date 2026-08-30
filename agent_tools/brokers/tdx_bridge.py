"""通达信（TDX）桥 Broker：对接 quantmind 的 TDX 交易通道。

复用资产（~/projects/quantmind）：
- backend/services/trade/services/tdx_account_sync_task.py — 账户同步
- backend/services/trade/services/tdx_signal_push_service.py — 信号推送
- backend/services/trade/routers/tdx_l2.py — L2 行情
- backend/services/trade/simulation/services/market_rules.py — 市场规则
- 8550 桥（tdx-39 bridge token）：日K/周K 行情

配置（.env）：
  TDX_BRIDGE_URL=  桥地址
  TDX_BRIDGE_TOKEN=桥 token

安全：实盘下单必须过风控（见 docs/ARCHITECTURE_UPGRADE.md §4）。
当前为骨架：quote/kline 已接桥协议，place_order 待移植。
"""

import os
from typing import Any, Dict, List, Optional

from agent_tools.brokers.base import Broker, BrokerError


class TdxBridgeBroker(Broker):
    """通达信桥。"""

    name = "tdx"
    markets = "cn"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.bridge_url = (self.config.get("bridge_url")
                           or os.getenv("TDX_BRIDGE_URL", "")).rstrip("/")
        self.token = self.config.get("token") or os.getenv("TDX_BRIDGE_TOKEN", "")
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

    def _place_order(self, symbol: str, side: str, volume: int,
                     price: Optional[float] = None) -> Dict[str, Any]:
        """经桥下单（/api/v1/plans/execute，通达信客户端执行）。"""
        import time

        import requests

        if not self.account:
            raise BrokerError("TDX 账户未配置：请设置 TDX_ACCOUNT（.env），未配置前禁止实盘下单")
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

    def get_positions(self, signature: str, today_date: str) -> Dict[str, float]:
        raise BrokerError("TDX 实盘持仓查询待移植（quantmind: tdx_account_sync_task.py）")

    def get_cash(self, signature: str, today_date: str) -> float:
        raise BrokerError("TDX 实盘现金查询待移植")

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

    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "cn") -> List[Dict[str, Any]]:
        """经 8550 桥拉 K 线（日K/周K 支持，分钟线不支持——桥限制）。"""
        import requests

        try:
            resp = requests.get(
                f"{self.bridge_url}/kline",
                params={
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "interval": interval,
                    "token": self.token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            bars = data.get("data") or data.get("klines") or []
            if not isinstance(bars, list):
                raise BrokerError(f"TDX 桥返回格式异常: {str(data)[:200]}")
            return bars
        except requests.RequestException as exc:
            raise BrokerError(f"TDX 桥请求失败: {exc}") from exc


def register() -> None:
    from agent_tools.brokers.base import registry

    registry.register(TdxBridgeBroker)


register()
