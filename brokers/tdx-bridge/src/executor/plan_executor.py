import json
import logging
import os
from datetime import datetime

from ..core.trade_plan import TradePlan
from ..core.types import OrderStatus, PriceType, Side
from ..tdx import parser
from ..tdx.client import TdxClient, TdxError

log = logging.getLogger(__name__)


class PlanExecutor:
    """执行 TradePlan: 卖先买后, 资金/持仓校验, 幂等去重, 交易记录同步到共享目录."""

    def __init__(self, tdx: TdxClient, shared_report_dir: str,
                 trade_log_file: str = "trade_log.jsonl"):
        self.tdx = tdx
        self.shared_report_dir = shared_report_dir
        self.trade_log_file = os.path.join(shared_report_dir, trade_log_file)
        self._executed_plans = set()   # 内存去重 (重启后可从日志恢复)
        self._account_id = -1          # 未显式指定则用 0

    def resolve_account_id(self, account: str, account_type: str) -> int:
        """用户指定账号则取句柄, 否则默认 0."""
        if account:
            aid = self.tdx.stock_account(account=account, account_type=account_type)
            if aid >= 0:
                return aid
            log.warning(f"stock_account 返回句柄 {aid}, 回退默认 0")
        return 0

    # ---- 交易记录同步 ----

    def log_trade(self, record: dict) -> None:
        """把交易记录追加写到共享目录, 供 Linux 侧读取."""
        record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        record.setdefault("bridge", "bridge-windows")
        try:
            os.makedirs(self.shared_report_dir, exist_ok=True)
            with open(self.trade_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            log.error(f"写交易记录失败: {e}")

    # ---- 主流程 ----

    def execute_plan(self, plan: TradePlan) -> dict:
        if plan.plan_id in self._executed_plans:
            return {"plan_id": plan.plan_id, "status": "duplicate",
                    "message": "plan 已执行过"}
        self._executed_plans.add(plan.plan_id)

        self.log_trade({"event": "plan_received", "plan": plan.to_dict()})
        account_id = self.resolve_account_id(plan.account, plan.account_type)

        orders_out = []
        statuses = []
        # 卖先买后
        sells = [o for o in plan.orders if o.side == Side.SELL.value]
        buys = [o for o in plan.orders if o.side == Side.BUY.value]
        for o in sells + buys:
            result = self._execute_one(account_id, o, plan)
            orders_out.append(result)
            if result["status"] == "filled":
                statuses.append("filled")
            elif result["status"] in ("submitted", "needs_confirm"):
                statuses.append("submitted")
            else:
                statuses.append("rejected")

        overall = "executed" if not any(s in ("rejected", "error") for s in statuses) else "partial"
        if not orders_out:
            overall = "rejected"

        report = {
            "plan_id": plan.plan_id,
            "status": overall,
            "orders": orders_out,
            "executed_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.log_trade({"event": "plan_done", "plan_id": plan.plan_id, "status": overall})
        return report

    # ---- 单笔下单 ----

    def _execute_one(self, account_id: int, order, plan: TradePlan) -> dict:
        side = Side(order.side)
        stock = order.stock_code

        # 幂等: 查今日委托是否已存在同代码同方向
        try:
            existing = self.tdx.query_stock_orders(account_id, stock_code=stock)
            for eo in existing:
                if eo.get("BSFlag") == (1 if side == Side.SELL else 0) and eo.get("Status") in ("3", 3):
                    log.info(f"{stock} 已有成交委托, 跳过重复下单")
                    return self._order_out(order, "duplicate", None,
                                           "当日已有同方向成交, 跳过")
        except TdxError as e:
            log.warning(f"查委托失败(继续): {e}")

        # 卖前校验可用持仓
        if side == Side.SELL:
            try:
                positions = self.tdx.query_stock_positions(account_id)
                avail = next((int(p.get("CanUseVol") or 0) for p in positions
                              if p.get("Code") == stock), 0)
                if avail < order.volume:
                    return self._order_out(order, "rejected", None,
                                           f"可用持仓不足: {avail} < {order.volume}")
            except TdxError as e:
                log.warning(f"查持仓失败: {e}")

        price_type = PriceType(order.price_type) if order.price_type is not None else (
            PriceType.MANUAL if order.order_type == "limit" else PriceType.MARKET)
        price = order.price or 0.0

        try:
            res = self.tdx.order_stock(account_id, stock, side, order.volume,
                                       price_type, price)
            value = str(res.get("Value", "0"))
            wtbh = str(res.get("Wtbh", "") or "")
            msg = res.get("Msg", "")

            if value == "2":
                state = OrderStatus.SUBMITTED
                status = "submitted"
                self.log_trade({"event": "order_placed", "stock": stock, "side": side.value,
                                "volume": order.volume, "price": price, "order_id": wtbh})
            elif value == "1":
                state = OrderStatus.NEEDS_CONFIRM
                status = "needs_confirm"
                self.log_trade({"event": "order_needs_confirm", "stock": stock,
                                "side": side.value, "volume": order.volume,
                                "order_id": wtbh, "msg": msg})
            else:
                state = OrderStatus.REJECTED
                status = "rejected"
                self.log_trade({"event": "order_rejected", "stock": stock,
                                "side": side.value, "volume": order.volume,
                                "msg": msg, "value": value})
            return self._order_out(order, status, wtbh, msg, state)
        except TdxError as e:
            self.log_trade({"event": "order_error", "stock": stock, "side": side.value,
                            "volume": order.volume, "error": str(e)})
            return self._order_out(order, "error", None, str(e))

    def _order_out(self, order, status: str, order_id, message, state=None):
        out = {
            "stock_code": order.stock_code,
            "side": order.side,
            "volume": order.volume,
            "status": status,
            "order_id": order_id or "",
            "message": message or "",
        }
        if any([order.stop_loss_pct, order.take_profit_pct, order.trailing_stop_pct]):
            out["sltp"] = {
                "stop_loss_pct": order.stop_loss_pct,
                "take_profit_pct": order.take_profit_pct,
                "trailing_stop_pct": order.trailing_stop_pct,
            }
        return out
