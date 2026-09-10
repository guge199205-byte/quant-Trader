"""QMT（迅投大 QMT）桥 Broker —— 阶段一：只读。

通道拓扑（与 8550 通达信桥**完全独立**，互不拖累）：
    BayMax(Linux) ──Redis RPC──> Windows 192.168.31.13 · 大 QMT 内置策略
Windows 侧 = quantmind 的 qmt-bridge-kit（QMT 策略编辑器里加载 BIGQMT_REDIS_DRYRUN.py，
它自己 import 其余模块）；本模块是它的 Linux 侧客户端，走 `xtquant-big-convert`
的 xtquant 兼容层（Linux 不需要真机 xtquant，也不需要 userdata_mini）。

职责边界：只做「账户 / 持仓 / 委托 / 成交」；**行情不在这里**（继续走 TDX 桥与
quantdb）。2026-09-10 实录：行情通道全通而账户通道整日掉线，两条链路必须可分——
把行情也搬过来等于自毁冗余。

阶段一（当前）：查询可用；buy/sell/cancel_order 一律抛 BrokerError，等只读链路
验收（对账、观察若干交易日）通过后再接。Windows 侧另有 rpc_allow_order_methods
总闸，即使这边接线了，那边不开也下不出去。

配置（按优先级：构造参数 → config/qmt_bridge.json → 环境变量）：
  account_id / redis_host / redis_port(6380) / redis_db(0) / redis_password
  account_type(STOCK) / timeout(10) / strategy_name(baymax)
  环境变量 QMT_EXEC_* 优先，兼容 big-convert 原生 BIGQMT_*。
  ★ config/qmt_bridge.json 含密码，不入库（.gitignore）。
"""
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_tools.brokers.base import Broker, BrokerError

# 与 config/tdx_bridge.json 同惯例：运维/安装写入的运行时配置，优先于环境变量
_OVERRIDE_FILE = Path(__file__).resolve().parents[2] / "config" / "qmt_bridge.json"

DEFAULT_PORT = 6380
DEFAULT_DB = 0
DEFAULT_TIMEOUT = 10.0
DEFAULT_ACCOUNT_TYPE = "STOCK"
DEFAULT_STRATEGY_NAME = "baymax"

# QMT 委托状态码 → 本系统（TDX 口径）小写状态串。
# 口径必须与 scripts/live_fills.py 的终态集合 ("cancelled","withdrawn","rejected",
# "expired","filled") 对齐：51(报撤中)/52(部成待撤) **不是终态**，误判会漏记成交
# 或提前清掉在途单；53(部撤) 是终态（已撤，剩量不再成交，部成由 reconcile 补记）。
STATUS_MAP: Dict[int, str] = {
    48: "submitted",   # 未申报
    49: "submitted",   # 等待申报
    50: "submitted",   # 已申报
    51: "submitted",   # 报撤中（未确认，仍可能成交）
    52: "partial_fill",  # 部成待撤
    53: "cancelled",   # 部撤（终态）
    54: "cancelled",   # 已撤
    55: "partial_fill",  # 部分成交
    56: "filled",      # 全部成交
    57: "rejected",    # 废单
    255: "submitted",  # 未知（不误判为终态）
}

READONLY_MSG = ("QMT 通道处于只读阶段（阶段一）：下单/撤单未接线——"
                "先跑 scripts/qmt_probe.py 验收只读链路，再接执行")


def qmt_overrides() -> Dict[str, Any]:
    """读取 config/qmt_bridge.json（缺文件/坏 JSON 返回空，不阻断构造）。"""
    try:
        data = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _attr(obj: Any, *names: str, default: Any = "") -> Any:
    """取对象首个存在且非 None 的属性（QMT 字段名随版本略有出入）。"""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v: Any, default: int = -1) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class QmtBridgeBroker(Broker):
    """大 QMT 桥。阶段一只读；接口形状对齐 TdxBridgeBroker（上层脚本无感）。"""

    name = "qmt"
    markets = "cn"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = dict(config or {})
        ov = qmt_overrides()

        def pick(key: str, envs: tuple, default: Any = "") -> Any:
            for src in (cfg.get(key), ov.get(key)):
                if src not in (None, ""):
                    return src
            for e in envs:
                if os.getenv(e):
                    return os.getenv(e)
            return default

        self.account_id = str(pick("account_id",
                                   ("QMT_EXEC_ACCOUNT_ID", "BIGQMT_ACCOUNT_ID"), ""))
        self.account_type = str(pick("account_type",
                                     ("QMT_EXEC_ACCOUNT_TYPE", "BIGQMT_ACCOUNT_TYPE"),
                                     DEFAULT_ACCOUNT_TYPE))
        self.strategy_name = str(pick("strategy_name",
                                      ("QMT_EXEC_STRATEGY_NAME",), DEFAULT_STRATEGY_NAME))
        self.timeout = float(pick("timeout", ("QMT_EXEC_TIMEOUT",), DEFAULT_TIMEOUT))
        self.redis: Dict[str, Any] = {
            "host": str(pick("redis_host", ("QMT_EXEC_REDIS_HOST", "BIGQMT_REDIS_HOST"), "")),
            "port": int(pick("redis_port", ("QMT_EXEC_REDIS_PORT",), DEFAULT_PORT)),
            "db": int(pick("redis_db", ("QMT_EXEC_REDIS_DB",), DEFAULT_DB)),
            "password": str(pick("redis_password",
                                 ("QMT_EXEC_REDIS_PASSWORD", "BIGQMT_REDIS_PASSWORD"), "")),
        }
        self._trader: Any = None
        self._account: Any = None
        self._lock = threading.Lock()

        if not self.account_id:
            raise BrokerError("QMT 桥未配置资金账号：config/qmt_bridge.json 的 "
                              "account_id（或 QMT_EXEC_ACCOUNT_ID）")
        if not self.redis["host"]:
            raise BrokerError("QMT 桥未配置 Redis 地址：config/qmt_bridge.json 的 "
                              "redis_host（或 QMT_EXEC_REDIS_HOST）")

    # ---------- 连接 ----------

    def _redact(self, text: Any) -> str:
        """底层库报错常把 Redis URL（含密码）原样带出来 → 出日志前先脱敏。"""
        s = str(text)
        pw = str(self.redis.get("password") or "")
        return s.replace(pw, "***") if pw else s

    def _ensure(self):
        """惰性连接（首次调用时 import + configure），双检锁防并发重复初始化。"""
        if self._trader is not None and self._account is not None:
            return self._trader, self._account
        with self._lock:
            if self._trader is not None and self._account is not None:
                return self._trader, self._account
            try:
                from bigqmt_signal_trader.xtquant_compat import (  # noqa: PLC0415
                    StockAccount,
                    configure,
                    xt_trader,
                )
            except ImportError as exc:
                raise BrokerError(
                    '未安装 QMT 客户端库（pip install "xtquant-big-convert[redis]==0.3.31"）'
                ) from exc
            try:
                configure(account_id=self.account_id,
                          redis_config=dict(self.redis),
                          timeout_seconds=self.timeout)
            except Exception as exc:  # noqa: BLE001 底层库异常类型不稳定
                raise BrokerError(
                    f"QMT 客户端配置失败：{self._redact(exc)}"
                    "（检查资金账号与桥 Redis 地址/密码）") from exc
            self._trader = xt_trader
            self._account = StockAccount(self.account_id, self.account_type)
            return self._trader, self._account

    # ---------- 查询 ----------

    def ping(self) -> Dict[str, Any]:
        """RPC 存活探测（只读，不触发任何交易方法）。"""
        trader, _ = self._ensure()
        try:
            result = trader.client.call("ping", {})
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"QMT 桥 ping 失败：{self._redact(exc)}") from exc
        return {"ok": True, "result": result, "account_id": self.account_id}

    def _account_query(self) -> Dict[str, Any]:
        """资产+持仓，返回形状与 TdxBridgeBroker._account_query 完全一致。

        上层 10+ 个脚本读的键固定为 asset.asset / asset.cash /
        positions[].stock_code|total_volume|available_volume|cost_price——
        available_volume 是 T+1 卖出闸门的唯一依据，必须如实映射 can_use_volume。
        """
        trader, account = self._ensure()
        try:
            asset = trader.query_stock_asset(account)
            positions = trader.query_stock_positions(account) or []
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"QMT 账户查询失败：{self._redact(exc)}") from exc
        if asset is None:
            raise BrokerError("QMT 账户查询返回空（交易端可能未登录）")

        total_asset = _to_float(_attr(asset, "total_asset"))
        cash = _to_float(_attr(asset, "cash"))
        market_value = _to_float(_attr(asset, "market_value"))
        pos_out: List[Dict[str, Any]] = []
        for p in positions:
            code = str(_attr(p, "stock_code", "instrument_id", default="") or "")
            volume = _to_float(_attr(p, "volume", "total_volume"))
            if not code or volume <= 0:
                continue          # 清仓残留行：不算持仓
            mv = _to_float(_attr(p, "market_value"))
            pos_out.append({
                "stock_code": code,
                "stock_name": str(_attr(p, "stock_name", "instrument_name", default="") or ""),
                "cost_price": _to_float(_attr(p, "avg_price", "open_price")),
                "total_volume": volume,
                "available_volume": _to_float(_attr(p, "can_use_volume")),
                "market_value": mv,
                # 现价兜底：消费方把 0/缺省视为"拿不到"，会回落到行情接口
                "last_price": round(mv / volume, 4) if volume > 0 and mv > 0 else 0,
            })
        return {
            "account_id": self.account_id,
            "channel_used": "qmt",
            "asset": {
                "currency": "CNY",
                "asset": total_asset,
                "balance": total_asset,
                "cash": cash,
                "market_value": market_value,
                "frozen_cash": _to_float(_attr(asset, "frozen_cash")),
            },
            "positions": pos_out,
        }

    def get_positions(self, signature: str, today_date: str) -> Dict[str, float]:
        """{symbol: total_volume}（对齐 TdxBridgeBroker）。"""
        data = self._account_query()
        return {p["stock_code"]: float(p["total_volume"])
                for p in data.get("positions") or [] if p.get("stock_code")}

    def get_cash(self, signature: str, today_date: str) -> float:
        """可用资金（对齐 TdxBridgeBroker：asset.cash）。"""
        return float((self._account_query().get("asset") or {}).get("cash") or 0)

    def get_orders(self, stock_code: str = "",
                   cancelable_only: bool = False) -> List[Dict[str, Any]]:
        """当日委托（QMT 无历史接口），字段名对齐 TDX 桥口径。"""
        trader, account = self._ensure()
        try:
            orders = trader.query_stock_orders(account, cancelable_only=cancelable_only) or []
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"QMT 委托查询失败：{self._redact(exc)}") from exc
        out: List[Dict[str, Any]] = []
        for o in orders:
            code = str(_attr(o, "stock_code", default="") or "")
            if stock_code and code != stock_code:
                continue
            status_code = _to_int(_attr(o, "order_status", "status"), -1)
            side_code = _to_int(_attr(o, "order_type"), -1)
            out.append({
                "order_id": str(_attr(o, "order_id", default="") or ""),
                "stock_code": code,
                "side": {23: "buy", 24: "sell"}.get(side_code, ""),
                "status": STATUS_MAP.get(status_code, "submitted"),
                "status_code": status_code,
                "order_price": _to_float(_attr(o, "price")),
                "filled_price": _to_float(_attr(o, "traded_price")),
                "filled_volume": _to_float(_attr(o, "traded_volume")),
                "total_volume": _to_float(_attr(o, "order_volume")),
                "time": str(_attr(o, "order_time", "time", default="") or ""),
                "status_msg": str(_attr(o, "status_msg", default="") or ""),
            })
        return out

    def get_trades(self) -> List[Dict[str, Any]]:
        """当日成交明细（对账用；TDX 桥无此接口，QMT 有）。"""
        trader, account = self._ensure()
        try:
            trades = trader.query_stock_trades(account) or []
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"QMT 成交查询失败：{self._redact(exc)}") from exc
        return [{
            "trade_id": str(_attr(t, "traded_id", "trade_id", default="") or ""),
            "order_id": str(_attr(t, "order_id", default="") or ""),
            "stock_code": str(_attr(t, "stock_code", default="") or ""),
            "side": {23: "buy", 24: "sell"}.get(_to_int(_attr(t, "order_type"), -1), ""),
            "filled_volume": _to_float(_attr(t, "traded_volume")),
            "filled_price": _to_float(_attr(t, "traded_price")),
            "time": str(_attr(t, "traded_time", "time", default="") or ""),
        } for t in trades]

    # ---------- 交易（阶段一：未接线） ----------

    def buy(self, signature: str, today_date: str, symbol: str, amount: int,
            price: Optional[float] = None) -> Dict[str, Any]:
        raise BrokerError(READONLY_MSG)

    def sell(self, signature: str, today_date: str, symbol: str, amount: int,
             price: Optional[float] = None) -> Dict[str, Any]:
        raise BrokerError(READONLY_MSG)

    def cancel_order(self, stock_code: str, order_id: str) -> Dict[str, Any]:
        raise BrokerError(READONLY_MSG)

    # ---------- 行情（不在本通道职责内） ----------

    def get_quote(self, symbol: str, date: str, market: str = "cn") -> Optional[Dict[str, Any]]:
        raise BrokerError("QMT 通道不提供行情：行情走 TDX 桥 / quantdb（两条链路分开是刻意设计）")

    def get_klines(self, symbol: str, start: str = "", end: str = "",
                   interval: str = "daily", market: str = "cn") -> List[Dict[str, Any]]:
        raise BrokerError("QMT 通道不提供行情：行情走 TDX 桥 / quantdb（两条链路分开是刻意设计）")


def register() -> None:
    from agent_tools.brokers.base import registry

    registry.register(QmtBridgeBroker)


register()
