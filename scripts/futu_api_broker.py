#!/usr/bin/env python3
"""富途执行代理（经 BayMax /api/futu/* HTTP，宿主脚本用）。

实现 tiger/ibkr 桥的通用接口（get_positions/get_cash/get_quote/get_klines/
buy/sell），让 HK/US 执行链可按「市场→交易所映射」选富途执行。
- env: SIMULATE（默认，安全）/ REAL（brokers.json futu.trade_env 可覆盖）
- 代码: HK.00700（港股）/ US.AAPL（美股）
- 行情: snapshot 端点（现价）；K线暂不经 HTTP（取价失败跳过，闸门保护）
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8092"  # nginx 反代（自动注入 X-API-Token）


def _api(path: str, params: dict | None = None, payload: dict | None = None,
         timeout: int = 30) -> dict:
    import requests

    if payload is not None:
        resp = requests.post(f"{API}{path}", json=payload, timeout=timeout)
    else:
        resp = requests.get(f"{API}{path}", params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _env() -> str:
    """brokers.json futu.trade_env（SIMULATE/REAL），默认 SIMULATE。"""
    try:
        data = json.loads((ROOT / "config" / "brokers.json").read_text(encoding="utf-8"))
        env = ((data or {}).get("futu") or {}).get("trade_env") or "SIMULATE"
        return str(env).upper() if str(env).upper() in ("SIMULATE", "REAL") else "SIMULATE"
    except (OSError, json.JSONDecodeError):
        return "SIMULATE"


def futu_code(code: str, market: str = "hk") -> str:
    """00700 / HK.00700 → HK.00700；AAPL → US.AAPL。"""
    if "." in str(code):
        return str(code)
    if market == "us":
        return f"US.{code}"
    return f"HK.{str(code).zfill(5)}"


class FutuApiBroker:
    """富途执行代理：接口对齐 tiger/ibkr 桥（tiger_bridge/ibkr_bridge 通用形态）。"""

    name = "futu"

    def __init__(self, market: str = "hk"):
        self.market = "us" if str(market).lower() == "us" else "hk"
        self.env = _env()

    # ---------- 账户 ----------

    def get_positions(self, signature, today_date, market: str = "hk") -> dict:
        data = _api("/api/futu/account", {"env": self.env}) or {}
        raw = (data.get("data") or {}).get("account") or data.get("data") or {}
        out = {}
        for code, p in (raw.get("positions") or {}).items():
            vol = float(p.get("volume") or 0)
            if vol <= 0:
                continue
            out[code] = {
                "symbol": code, "volume": vol,
                "cost_price": float(p.get("cost") or 0),
                "market_value": float(p.get("market_value") or 0),
                "currency": p.get("currency") or "",
            }
        return out

    def get_cash(self, signature, today_date) -> float:
        data = _api("/api/futu/account", {"env": self.env}) or {}
        raw = (data.get("data") or {}).get("account") or data.get("data") or {}
        return float(raw.get("cash") or 0)

    # ---------- 行情 ----------

    def get_quote(self, symbol: str, date: str, market: str = "hk") -> dict | None:
        code = futu_code(symbol, market)
        try:
            data = _api("/api/futu/snapshot", {"codes": code}, timeout=15) or {}
            snap = ((data.get("data") or {}).get("snapshot") or {}).get(code) or {}
            price = float(snap.get("last_price") or 0)
            if price > 0:
                return {"symbol": symbol, "date": date, "buy price": price}
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_klines(self, symbol: str, *args, **kwargs) -> list:
        return []  # 富途K线暂不经 HTTP（取价失败由闸门跳过）

    # ---------- 交易 ----------

    def buy(self, signature, today_date, symbol: str, amount: int,
            price: float | None = None, market: str = "hk") -> dict:
        return self._place(symbol, amount, price, "BUY", market)

    def sell(self, signature, today_date, symbol: str, amount: int,
             price: float | None = None, market: str = "hk") -> dict:
        return self._place(symbol, amount, price, "SELL", market)

    def _place(self, symbol: str, amount: int, price: float | None,
               side: str, market: str) -> dict:
        data = _api("/api/futu/place", payload={
            "env": self.env,
            "market": "US" if market == "us" else "HK",
            "order": {
                "code": futu_code(symbol, market),
                "price": float(price or 0),
                "quantity": int(amount),
                "order_type": "NORMAL" if price else "MARKET",
                "trd_side": side,
            },
        }, timeout=40) or {}
        if not data.get("success"):
            raise RuntimeError(data.get("error") or "富途下单失败")
        inner = data.get("data") or {}
        return {"order_id": str(inner.get("order_id") or ""),
                "status": str(inner.get("status") or "submitted"),
                "message": inner.get("message") or "富途已受理"}


if __name__ == "__main__":
    # 自检：SIM 账户资金/持仓
    b = FutuApiBroker("hk")
    print(f"env={b.env} cash=${b.get_cash(None, ''):,.2f} 持仓 {len(b.get_positions(None, ''))} 只")
