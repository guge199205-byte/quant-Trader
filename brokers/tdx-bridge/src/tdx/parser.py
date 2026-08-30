import logging
from typing import Optional

from ..core.types import OrderStatus, Side, TDX_STATUS_MAP, PriceType

log = logging.getLogger(__name__)


def parse_position(pos: dict) -> dict:
    """持仓记录 -> 内部结构."""
    return {
        "stock_code": pos.get("Code", ""),
        "cost_price": _f(pos.get("Cbj")),
        "total_volume": int(_f(pos.get("TotalVol")) or 0),
        "available_volume": int(_f(pos.get("CanUseVol")) or 0),
    }


def parse_order(order: dict) -> dict:
    """当日委托记录 -> 内部结构."""
    bs = int(_f(order.get("BSFlag")) or -1)
    side = Side.SELL if bs == 1 else (Side.BUY if bs == 0 else None)
    status = TDX_STATUS_MAP.get(int(_f(order.get("Status")) or 0), OrderStatus.SUBMITTED)
    return {
        "order_id": str(order.get("Wtbh", "")),
        "stock_code": order.get("Code", ""),
        "time": order.get("Time", ""),
        "side": side.value if side else "cancel",
        "status": status.value,
        "order_price": _f(order.get("WtPrice")),
        "filled_price": _f(order.get("CjPrice")),
        "filled_volume": int(_f(order.get("CjVol")) or 0),
        "total_volume": abs(int(_f(order.get("WtVol")) or 0)),
    }


def parse_asset(asset: dict) -> dict:
    return {
        "currency": asset.get("Currency", ""),
        "balance": _f(asset.get("Balance")),
        "cash": _f(asset.get("Cash")),
        "asset": _f(asset.get("Asset")),
        "market_value": _f(asset.get("MarketValue")),
    }


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
