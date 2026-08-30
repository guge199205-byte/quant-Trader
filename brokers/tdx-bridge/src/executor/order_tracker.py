import json
import logging
import os
import tempfile
from datetime import datetime

from ..core.order_state import OrderStateMachine
from ..core.types import OrderStatus, TDX_STATUS_MAP
from ..tdx.client import TdxClient
from ..tdx import parser

log = logging.getLogger(__name__)


def atomic_write(path: str, data: dict) -> None:
    """原子写 JSON (临时文件 + rename)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class OrderTracker:
    """订单生命周期跟踪, 状态持久化到 active_orders.json, 定时与 TDX 对账."""

    def __init__(self, tdx: TdxClient, state_file: str, account_id: int = 0,
                 sync_interval: float = 15.0):
        self.tdx = tdx
        self.state_file = state_file
        self.account_id = account_id
        self.sync_interval = sync_interval
        self.sm = OrderStateMachine()
        self.orders = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
            for oid, rec in data.get("orders", {}).items():
                self.orders[oid] = rec
                self.sm.set(oid, OrderStatus(rec.get("state", "submitted")))
        except (OSError, ValueError) as e:
            log.error(f"加载 {self.state_file} 失败: {e}")

    def save(self) -> None:
        atomic_write(self.state_file, {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "orders": self.orders,
        })

    def register(self, order_id: str, stock_code: str, side: str, volume: int,
                 price: float, plan_id: str = "") -> None:
        self.orders[order_id] = {
            "order_id": order_id, "plan_id": plan_id, "stock_code": stock_code,
            "side": side, "state": "submitted", "order_price": price,
            "filled_volume": 0, "total_volume": volume,
            "submitted_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.sm.set(order_id, OrderStatus.SUBMITTED)
        self.save()

    def update_state(self, order_id: str, new_state: OrderStatus, **fields) -> None:
        rec = self.orders.get(order_id)
        if not rec:
            return
        try:
            self.sm.transition(order_id, new_state)
        except ValueError as e:
            log.warning(f"{order_id} {e}")
            self.sm.set(order_id, new_state)
        rec["state"] = new_state.value
        rec.update(fields)
        rec["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self.save()

    def reconcile(self) -> None:
        """向 TDX 查询当日委托, 更新已登记订单状态."""
        try:
            orders = self.tdx.query_stock_orders(self.account_id)
        except Exception as e:
            log.warning(f"对账失败: {e}")
            return
        for o in orders:
            oid = str(o.get("Wtbh", ""))
            if oid not in self.orders:
                continue
            status = TDX_STATUS_MAP.get(int(parser._f(o.get("Status")) or 0),
                                        OrderStatus.SUBMITTED)
            self.update_state(oid, status,
                              filled_price=parser._f(o.get("CjPrice")),
                              filled_volume=int(parser._f(o.get("CjVol")) or 0))

    def snapshot(self) -> dict:
        return {"orders": self.orders, "states": self.sm.snapshot()}
