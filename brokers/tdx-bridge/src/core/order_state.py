from typing import Dict, Set

from .types import OrderStatus


# 状态机: 允许的迁移
ALLOWED_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.EXPIRED},
    OrderStatus.SUBMITTED: {OrderStatus.PARTIAL_FILL, OrderStatus.FILLED,
                            OrderStatus.CANCELLING, OrderStatus.NEEDS_CONFIRM, OrderStatus.REJECTED},
    OrderStatus.NEEDS_CONFIRM: {OrderStatus.SUBMITTED, OrderStatus.FILLED,
                                OrderStatus.REJECTED, OrderStatus.CANCELLING},
    OrderStatus.PARTIAL_FILL: {OrderStatus.FILLED, OrderStatus.CANCELLING},
    OrderStatus.CANCELLING: {OrderStatus.CANCELLED, OrderStatus.PARTIAL_CANCELLED,
                             OrderStatus.CANCEL_FAILED},
    OrderStatus.CANCEL_FAILED: {OrderStatus.CANCELLING},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.PARTIAL_CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}

TERMINAL_STATES = {OrderStatus.FILLED, OrderStatus.CANCELLED,
                   OrderStatus.PARTIAL_CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


class OrderStateMachine:
    def __init__(self) -> None:
        self._states: Dict[str, OrderStatus] = {}

    def current(self, order_id: str) -> OrderStatus:
        return self._states.get(order_id, OrderStatus.PENDING)

    def transition(self, order_id: str, new_state: OrderStatus) -> bool:
        old = self.current(order_id)
        if old == new_state:
            return True
        if new_state not in ALLOWED_TRANSITIONS.get(old, set()):
            raise ValueError(f"非法状态迁移: {old.value} -> {new_state.value} (order_id={order_id})")
        self._states[order_id] = new_state
        return True

    def set(self, order_id: str, state: OrderStatus) -> None:
        """直接设置(仅用于从持久化恢复)."""
        self._states[order_id] = state

    def is_terminal(self, order_id: str) -> bool:
        return self.current(order_id) in TERMINAL_STATES

    def snapshot(self) -> Dict[str, str]:
        return {k: v.value for k, v in self._states.items()}
