"""盈透证券（IBKR, ib_insync）Broker：连接 TWS / IB Gateway。

配置（.env）：
  IBKR_HOST=127.0.0.1
  IBKR_PORT=7497        # 7497=IBGW 纸面账户, 7496=TWS 纸面, 4001/4002=实盘
  IBKR_CLIENT_ID=1

安全：下单前过风控审批门；默认纸面账户（port 7497）。
"""

import os
from typing import Any, Dict, List, Optional

from agent_tools.brokers.base import Broker, BrokerError


class IbkrBridgeBroker(Broker):
    name = "ibkr"
    markets = "both"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.host = self.config.get("host") or os.getenv("IBKR_HOST", "127.0.0.1")
        self.port = int(self.config.get("port") or os.getenv("IBKR_PORT", "7497"))
        self.client_id = int(self.config.get("client_id") or os.getenv("IBKR_CLIENT_ID", "1"))
        self._ib = None

    # ---------- 连接 ----------

    def _connect(self):
        if self._ib is None:
            try:
                from ib_insync import IB

                self._ib = IB()
                if not self._ib.connect(self.host, self.port, clientId=self.client_id,
                                        timeout=10, readonly=False):
                    raise BrokerError(f"IBKR 连接失败（{self.host}:{self.port}）")
            except BrokerError:
                raise
            except Exception as exc:
                raise BrokerError(f"IBKR 连接异常: {exc}") from exc
        return self._ib

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

    @staticmethod
    def _stock(symbol: str):
        from ib_insync import Stock

        # IBKR 代码：AAPL(美股) / 0700.HK(港股)
        if symbol.endswith(".HK"):
            return Stock(symbol.replace(".HK", ""), "SMART", "HKD")
        if symbol.endswith((".SH", ".SZ")):
            return Stock(symbol, "SEHK" if False else "SMART", "CNH")
        return Stock(symbol, "SMART", "USD")

    # ---------- 行情 ----------

    def get_quote(self, symbol: str, date: str, market: str = "us") -> Optional[Dict[str, Any]]:
        try:
            ib = self._connect()
            ticker = ib.reqMktData(self._stock(symbol), "", False, False)
            ib.sleep(1.5)
            price = ticker.marketPrice()
            ib.cancelMktData(ticker.contract)
            if not price or price == float("inf"):
                return None
            return {"symbol": symbol, "date": date, "buy price": float(price)}
        except Exception as exc:
            raise BrokerError(f"IBKR 行情查询失败: {exc}") from exc

    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "us") -> List[Dict[str, Any]]:
        try:
            from ib_insync import util

            ib = self._connect()
            duration = "1 Y"
            bars = ib.reqHistoricalData(
                self._stock(symbol), endDateTime=end, durationStr=duration,
                barSizeSetting="1 day" if interval == "daily" else "1 hour",
                whatToShow="TRADES", useRTH=True,
            )
            return [
                {"date": str(b.date)[:10], "open": float(b.open), "close": float(b.close),
                 "high": float(b.high), "low": float(b.low), "volume": float(b.volume)}
                for b in bars if str(b.date)[:10] >= start
            ]
        except Exception as exc:
            raise BrokerError(f"IBKR K 线查询失败: {exc}") from exc

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
        try:
            from ib_insync import LimitOrder, MarketOrder

            ib = self._connect()
            order = LimitOrder(action, int(amount), float(price)) if price else MarketOrder(action, int(amount))
            trade = ib.placeOrder(self._stock(symbol), order)
            ib.sleep(2)
            status = trade.orderStatus.status
            if status in ("Cancelled", "Inactive"):
                raise BrokerError(f"IBKR 订单被拒: {status} {trade.orderStatus.whyHeld or ''}")
            return {"order_id": str(trade.order.orderId), "status": status,
                    "message": f"IBKR 已受理: {symbol} {action} {amount}"}
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"IBKR 下单失败: {exc}") from exc

    def get_positions(self, signature: str, today_date: str) -> Dict[str, float]:
        try:
            ib = self._connect()
            return {p.contract.symbol: float(p.position) for p in ib.positions() if p.position}
        except Exception as exc:
            raise BrokerError(f"IBKR 持仓查询失败: {exc}") from exc

    def get_cash(self, signature: str, today_date: str) -> float:
        try:
            ib = self._connect()
            total = 0.0
            for v in ib.accountSummary():
                if v.tag == "TotalCashValue":
                    total += float(v.value or 0)
            return total
        except Exception as exc:
            raise BrokerError(f"IBKR 资金查询失败: {exc}") from exc


def register() -> None:
    from agent_tools.brokers.base import registry

    registry.register(IbkrBridgeBroker)


register()
