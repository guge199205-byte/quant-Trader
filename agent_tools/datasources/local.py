"""本地数据源：data/merged.jsonl（日级 + 小时级兼容）。

包装 tools/price_tools.py 的既有函数，保持行为一致。
"""

from typing import Any, Dict, List, Optional

from tools.price_tools import (
    get_all_trading_days,
    get_open_prices,
    is_trading_day,
)

from agent_tools.datasources.base import DataSource


class LocalDataSource(DataSource):
    """本地文件数据源。"""

    name = "local"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def get_quote(self, symbol: str, date: str, market: str = "us") -> Optional[Dict[str, Any]]:
        prices = get_open_prices(date, [symbol], market=market)
        price = prices.get(f"{symbol}_price")
        if price is None:
            return None
        return {"symbol": symbol, "date": date, "buy price": price}

    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "us") -> List[Dict[str, Any]]:
        from tools.price_tools import get_yesterday_open_and_close_price

        try:
            start_prices, end_prices = get_yesterday_open_and_close_price(
                end, [symbol], market=market
            )
        except Exception as exc:
            raise DataSourceError(f"本地 K 线读取失败: {exc}") from exc
        bars = []
        if symbol in start_prices and start_prices[symbol] is not None:
            bars.append({"date": start, "type": "open", "price": start_prices[symbol]})
        if symbol in end_prices and end_prices[symbol] is not None:
            bars.append({"date": end, "type": "close", "price": end_prices[symbol]})
        return bars

    def is_trading_day(self, date: str, market: str = "us") -> bool:
        return is_trading_day(date, market=market)

    def get_trading_days(self, market: str = "us") -> List[str]:
        return get_all_trading_days(market=market)


def register() -> None:
    from agent_tools.datasources.base import registry

    registry.register(LocalDataSource)


register()
