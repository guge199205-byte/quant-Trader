from enum import Enum


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class PriceType(Enum):
    MANUAL = 0    # PRICE_MY 自填价(限价)
    MARKET = 1    # PRICE_SJ 市价
    LIMIT_UP = 2  # PRICE_ZTJ 涨停价/笼子上限
    LIMIT_DOWN = 3  # PRICE_DTJ 跌停价/笼子下限


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    NEEDS_CONFIRM = "needs_confirm"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    PARTIAL_CANCELLED = "partial_cancelled"
    CANCEL_FAILED = "cancel_failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ChannelType(Enum):
    HTTP = "http"
    FILE_SYNC = "file_sync"


# TDX 委托状态 -> 内部订单状态
TDX_STATUS_MAP = {
    0: OrderStatus.REJECTED,
    1: OrderStatus.SUBMITTED,
    2: OrderStatus.PARTIAL_FILL,
    3: OrderStatus.FILLED,
    4: OrderStatus.PARTIAL_CANCELLED,
    5: OrderStatus.CANCELLED,
}

# order_type 常量
STOCK_BUY = 0
STOCK_SELL = 1
