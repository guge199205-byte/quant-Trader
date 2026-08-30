"""TDX 桥数据源：通达信 8550 桥行情（日K/周K）。

行情逻辑复用 agent_tools.brokers.tdx_bridge.TdxBridgeBroker.get_klines，
保证 broker 与 datasource 行为一致。
"""

import os
from typing import Any, Dict, List, Optional

from agent_tools.datasources.base import DataSource, DataSourceError


class TdxDataSource(DataSource):
    """通达信桥数据源（行情只读，下单走 broker）。"""

    name = "tdx"
    markets = "cn"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._bridge = None

    def _get_bridge(self):
        if self._bridge is None:
            from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

            try:
                self._bridge = TdxBridgeBroker(self.config)
            except Exception as exc:
                raise DataSourceError(str(exc)) from exc
        return self._bridge

    def get_quote(self, symbol: str, date: str, market: str = "cn") -> Optional[Dict[str, Any]]:
        return self._get_bridge().get_quote(symbol, date, market=market)

    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "cn") -> List[Dict[str, Any]]:
        return self._get_bridge().get_klines(symbol, start, end, interval, market=market)

    def is_trading_day(self, date: str, market: str = "cn") -> bool:
        try:
            bars = self.get_klines("000001.SH", date, date, interval="daily", market="cn")
            return bool(bars)
        except Exception:
            return False

    def get_trading_days(self, market: str = "cn") -> List[str]:
        raise DataSourceError("TDX 桥不支持交易日历全量查询，请使用 local 数据源")


def register() -> None:
    from agent_tools.datasources.base import registry

    registry.register(TdxDataSource)


register()
