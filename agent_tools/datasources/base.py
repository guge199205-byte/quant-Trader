"""数据源抽象层：行情接入统一接口。

与 broker 层对称设计，数据源可插拔：
- local: 本地 merged.jsonl（默认，无网络依赖）
- tdx: 通达信 8550 桥（日K/周K）
- futu/tushare 等后续按同一接口扩展

配置（backend.yaml -> datasource 段）：
  datasource:
    default: "local"
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DataSourceError(Exception):
    """数据源异常。"""


class DataSource(ABC):
    """行情数据源统一接口。"""

    name: str = "base"
    #: 支持的市场："us" | "cn" | "both"
    markets: str = "both"

    @abstractmethod
    def get_quote(self, symbol: str, date: str, market: str = "us") -> Optional[Dict[str, Any]]:
        """单标的当日行情快照（buy/high/low/sell price, volume）。"""

    @abstractmethod
    def get_klines(self, symbol: str, start: str, end: str, interval: str = "daily",
                   market: str = "us") -> List[Dict[str, Any]]:
        """K 线序列；start/end 支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。"""

    @abstractmethod
    def is_trading_day(self, date: str, market: str = "us") -> bool:
        """该日期是否为交易日（以数据源实际覆盖为准）。"""

    @abstractmethod
    def get_trading_days(self, market: str = "us") -> List[str]:
        """数据源覆盖的全部交易日（升序）。"""


class DataSourceRegistry:
    def __init__(self) -> None:
        self._sources: Dict[str, type[DataSource]] = {}

    def register(self, cls: type[DataSource]) -> type[DataSource]:
        self._sources[cls.name] = cls
        return cls

    def create(self, name: str, config: Optional[Dict[str, Any]] = None) -> DataSource:
        if name not in self._sources:
            raise DataSourceError(f"未知数据源: {name}，可用: {sorted(self._sources)}")
        return self._sources[name](config or {})

    def available(self) -> List[str]:
        return sorted(self._sources)


registry = DataSourceRegistry()
