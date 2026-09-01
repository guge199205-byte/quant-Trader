"""老虎证券（tigeropen SDK）Broker。

配置（.env，与 tigeropen SDK 一致）：
  TIGEROPEN_TIGER_ID=老虎 Tiger ID
  TIGEROPEN_PRIVATE_KEY=私钥文件路径
  TIGEROPEN_ACCOUNT=账户号

安全：下单前过风控审批门；账户未配置时拒绝。
"""

import os
from typing import Any, Dict, List, Optional

from agent_tools.brokers.base import Broker, BrokerError


class TigerBridgeBroker(Broker):
    name = "tiger"
    markets = "both"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.tiger_id = self.config.get("tiger_id") or os.getenv("TIGEROPEN_TIGER_ID", "")
        # UI 存 rsa_private_key（tdx_live BROKER_FIELDS），旧代码读 private_key——两者都认
        self.private_key = (self.config.get("rsa_private_key")
                            or self.config.get("private_key")
                            or os.getenv("TIGEROPEN_PRIVATE_KEY", ""))
        self.account = self.config.get("account") or os.getenv("TIGEROPEN_ACCOUNT", "")

    # ---------- 客户端 ----------

    def _get_clients(self):
        if not self.tiger_id or not self.private_key:
            raise BrokerError("老虎证券未配置：TIGEROPEN_TIGER_ID / TIGEROPEN_PRIVATE_KEY（.env）")
        try:
            from tigeropen.common.configs import ClientConfig
            from tigeropen.common.util.sign_util import sign
            from tigeropen.quote.quote_client import QuoteClient
            from tigeropen.trade.trade_client import TradeClient

            config = ClientConfig(tiger_id=self.tiger_id, private_key=self.private_key)
            return QuoteClient(config), TradeClient(config)
        except Exception as exc:
            raise BrokerError(f"老虎 SDK 初始化失败: {exc}") from exc

    @staticmethod
    def _check_approval_gate() -> None:
        try:
            from agent_tools.risk import RiskPolicy

            if RiskPolicy.from_backend_config().approval_required:
                raise BrokerError("风控审批门开启（approval_required=true），实盘下单被拒绝")
        except BrokerError:
            raise
        except Exception:
            pass

    # ---------- 行情 ----------

    def get_quote(self, symbol: str, date: str, market: str = "hk") -> Optional[Dict[str, Any]]:
        try:
            quote_client, _ = self._get_clients()
            from tigeropen.quote.request.quote_request import QuoteRequest

            req = QuoteRequest(symbols=[symbol])
            quotes = quote_client.get_quote(req)
            if not quotes:
                return None
            q = quotes[0]
            return {"symbol": symbol, "date": date, "buy price": float(q.latest or 0)}
        except Exception as exc:
            raise BrokerError(f"老虎行情查询失败: {exc}") from exc

    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "hk") -> List[Dict[str, Any]]:
        try:
            quote_client, _ = self._get_clients()
            from tigeropen.quote.request.history_quote_request import HistoryQuoteRequest
            from tigeropen.common.consts import BarPeriod

            period = BarPeriod.DAY if interval == "daily" else BarPeriod.HOUR
            req = HistoryQuoteRequest(symbols=[symbol], begin_time=start, end_time=end, period=period)
            bars = quote_client.get_history_bars(req)
            return [
                {"date": str(b.time)[:10], "open": float(b.open), "close": float(b.close),
                 "high": float(b.high), "low": float(b.low), "volume": float(b.volume)}
                for b in (bars or [])
            ]
        except Exception as exc:
            raise BrokerError(f"老虎 K 线查询失败: {exc}") from exc

    # ---------- 交易 ----------

    def buy(self, signature: str, today_date: str, symbol: str, amount: int,
            price: Optional[float] = None) -> Dict[str, Any]:
        self._check_approval_gate()
        return self._place_order(symbol, amount, price, "BUY")

    def sell(self, signature: str, today_date: str, symbol: str, amount: int,
             price: Optional[float] = None) -> Dict[str, Any]:
        self._check_approval_gate()
        return self._place_order(symbol, amount, price, "SELL")

    def _place_order(self, symbol: str, amount: int, price: Optional[float],
                     action: str) -> Dict[str, Any]:
        if not self.account:
            raise BrokerError("老虎账户未配置：TIGEROPEN_ACCOUNT（.env）")
        try:
            _, trade_client = self._get_clients()
            from tigeropen.common.consts import Currency, Market, OrderType
            from tigeropen.trade.request.order_request import OrderRequest

            market = Market.US if symbol.isupper() and not symbol.endswith(".HK") else Market.HK
            req = OrderRequest(
                account=self.account, symbol=symbol, action=action,
                order_type=OrderType.MKT if not price else OrderType.LMT,
                total_quantity=int(amount), limit_price=float(price) if price else None,
                market=market, currency=Currency.USD if market == Market.US else Currency.HKD,
            )
            result = trade_client.create_order(req)
            return {"order_id": getattr(result, "id", ""), "status": "submitted",
                    "message": f"老虎已受理: {symbol} {action} {amount}"}
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"老虎下单失败: {exc}") from exc

    def get_positions(self, signature: str, today_date: str,
                      market: str = "hk") -> Dict[str, Any]:
        """持仓查询（HK/US 实盘）：{symbol: {volume, cost_price, market_value, currency}}。"""
        if not self.account:
            raise BrokerError("老虎账户未配置：TIGEROPEN_ACCOUNT（.env）")
        try:
            _, trade_client = self._get_clients()
            from tigeropen.common.consts import Market

            mkt = Market.HK if market == "hk" else Market.US
            positions = trade_client.get_positions(account=self.account, market=mkt) or []
            out: Dict[str, Any] = {}
            for p in positions:
                d = p.to_dict() if hasattr(p, "to_dict") else {}
                sym = str(d.get("symbol") or "")
                qty = float(d.get("quantity") or d.get("total_quantity") or 0)
                if not sym or qty <= 0:
                    continue
                out[sym] = {
                    "symbol": sym,
                    "volume": qty,
                    "cost_price": float(d.get("average_cost") or 0),
                    "market_value": float(d.get("market_value") or 0),
                    "currency": str(d.get("currency") or ""),
                }
            return out
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"老虎持仓查询失败: {exc}") from exc

    def get_cash(self, signature: str, today_date: str) -> float:
        """资金查询（实盘账户可用现金合计）。"""
        if not self.account:
            raise BrokerError("老虎账户未配置：TIGEROPEN_ACCOUNT（.env）")
        try:
            _, trade_client = self._get_clients()
            assets = trade_client.get_assets(account=self.account) or []
            cash = 0.0
            for a in assets:
                d = a.to_dict() if hasattr(a, "to_dict") else {}
                cash += float(d.get("cash") or d.get("available_funds") or 0)
            return cash
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"老虎资金查询失败: {exc}") from exc

    def get_orders(self, market: str = "hk", limit: int = 50) -> List[Dict[str, Any]]:
        """当日委托（含已成交/在途），字段对齐桥 orders 形状供前端消费。"""
        if not self.account:
            raise BrokerError("老虎账户未配置：TIGEROPEN_ACCOUNT（.env）")
        try:
            _, trade_client = self._get_clients()
            from tigeropen.common.consts import Market

            mkt = Market.HK if market == "hk" else Market.US
            orders = trade_client.get_orders(account=self.account, market=mkt,
                                             limit=int(limit)) or []
            out = []
            for o in orders:
                d = o.to_dict() if hasattr(o, "to_dict") else {}
                out.append({
                    "order_id": str(d.get("id") or ""),
                    "stock_code": str(d.get("symbol") or ""),
                    "side": str(d.get("action") or "").lower(),
                    "status": str(d.get("status") or "").lower(),
                    "filled_volume": float(d.get("filled_quantity") or 0),
                    "filled_price": float(d.get("filled_avg_price") or 0),
                    "order_price": float(d.get("limit_price") or 0),
                    "time": str(d.get("create_time") or "")[:19],
                })
            return out
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"老虎委托查询失败: {exc}") from exc


def register() -> None:
    from agent_tools.brokers.base import registry

    registry.register(TigerBridgeBroker)


register()
