#!/usr/bin/env python3
"""统一行情取数入口（2026-09-15）：混合策略，桥断不停摆。

背景：行情数据 Linux 侧 TdxAiData 通道可独立获取（与桥逐字段实测一致，
2026-09-15 对拍 600519/001312：close/volume/amount 全同，仅日期格式差），
账户/交易仍必须走桥。本模块把「取行情」统一收口：

  - prefer="aidata"（决策/批量路径：整点轮、调仓轮、复盘、延期重放）
      → AiData 优先（Linux 通道，不依赖 Win），桥兜底
  - prefer="bridge"（高频实时路径：分钟哨兵）
      → 桥优先（最实时），失败自动落 AiData——桥断行情判断照常

输出统一格式：[{date: "YYYY-MM-DD", open, high, low, close, volume, amount}]
（日期归一：桥的 "20260914" → "2026-09-14"；数值统一 float）

纪律：任何一路失败只记录，最终没数据返回 []——调用方按原有空值路径处理。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

from push_notify import _log  # noqa: E402  失败留痕（logs/push_notify.log）

_FIELDS = ("open", "high", "low", "close", "volume", "amount")
# 测试隔离开关（tests/conftest.py autouse 设置）：禁用 AiData 源，防 mock broker
# 的用例真撞 AiData 网络+限流退避（2026-09-15 实录：全量测试被拖到 timeout）
_AIDATA_OFF_ENV = "LIVE_QUOTES_DISABLE_AIDATA"
# 与 tdx_aidata 的 `_GATE_VARS`/`_OFF_VALUES` 是**同一套方言**：两个变量名都认，
# 值 strip+lower 后比 1/true/yes。这里不 import 那个模块（它有 tqsdk 级依赖，
# 本模块是热路径），靠 tests/test_aidata_gate.py 的矩阵钉住两边逐字一致——
# 分歧过一次，形态是静默的：探针报「已关闭」而这里照旧调用并失败。
_AIDATA_OFF_VARS = ("TDX_AIDATA_DISABLE", _AIDATA_OFF_ENV)
_AIDATA_OFF_VALUES = ("1", "true", "yes")


def _aidata_allowed() -> bool:
    for var in _AIDATA_OFF_VARS:
        if (os.environ.get(var) or "").strip().lower() in _AIDATA_OFF_VALUES:
            return False
    return True


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def norm_date(d: Any) -> str:
    """桥 "20260914" → "2026-09-14"；已是横线格式原样返回。"""
    s = str(d or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _norm_bars(bars: Optional[List[dict]]) -> List[dict]:
    out = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        bar = {"date": norm_date(b.get("date"))}
        bar.update({f: _f(b.get(f)) for f in _FIELDS})
        if bar["close"] > 0:            # 无收盘价的行（未生成 bar）丢弃
            out.append(bar)
    return out


def _aidata_klines(code: str, interval: str, count: int) -> List[dict]:
    if not _aidata_allowed():
        raise RuntimeError("AiData 已禁用（测试隔离开关）")
    from agent_tools.datasources import tdx_aidata as ta

    if not ta.available():
        raise RuntimeError("AiData 不可用")
    return ta.get_klines(code, interval=interval, count=count)


def _bridge_klines(broker: Any, code: str, interval: str, count: int) -> List[dict]:
    if broker is None:
        raise RuntimeError("无 broker（桥不可用）")
    bars = broker.get_klines(code, interval=interval)
    return bars[-count:] if count and bars else bars


def klines(code: str, *, interval: str = "daily", count: int = 5,
           prefer: str = "aidata", broker: Any = None) -> List[dict]:
    """单码日K，统一格式。prefer: "aidata"(决策/批量) | "bridge"(高频实时)。"""
    order = ("aidata", "bridge") if prefer == "aidata" else ("bridge", "aidata")
    for src in order:
        try:
            raw = (_aidata_klines(code, interval, count) if src == "aidata"
                   else _bridge_klines(broker, code, interval, count))
            bars = _norm_bars(raw)
            if bars:
                return bars
        except Exception as exc:  # noqa: BLE001
            _log(f"live_quotes[{src}] {code} 取K线失败: {str(exc)[:120]}")
    return []


def klines_batch(codes: List[str], *, interval: str = "daily", count: int = 5,
                 broker: Any = None, prefer: str = "aidata") -> Dict[str, List[dict]]:
    """多码批量：AiData 优先（一次拉全，最省限流），缺的码逐一桥兜底。

    prefer="bridge" 时逐码走 klines（哨兵场景不用批量）。
    """
    out: Dict[str, List[dict]] = {}
    pending = list(codes)
    if prefer == "aidata" and pending and _aidata_allowed():
        try:
            from agent_tools.datasources import tdx_aidata as ta

            if ta.available():
                data = ta.get_klines_batch(pending, interval=interval, count=count)
                for code, bars in (data or {}).items():
                    nb = _norm_bars(bars)
                    if nb:
                        out[code] = nb
                pending = [c for c in pending if c not in out]
        except Exception as exc:  # noqa: BLE001
            _log(f"live_quotes[batch] AiData 批量失败: {str(exc)[:120]}")
    for code in pending:
        bars = klines(code, interval=interval, count=count,
                      prefer=prefer, broker=broker)
        if bars:
            out[code] = bars
    return out


def last_close(code: str, *, prefer: str = "aidata", broker: Any = None,
               interval: str = "daily") -> float:
    """最新一根收盘价（=盘中为实时价）；取不到 0.0。"""
    bars = klines(code, interval=interval, count=2, prefer=prefer, broker=broker)
    return bars[-1]["close"] if bars else 0.0


# AiData 快照的现价字段随版本名不同，按序探测
_PRICE_KEYS = ("close", "Close", "last_price", "LastPrice", "NewPrice",
               "new_price", "Now", "Price", "Last")


def quote(code: str, *, prefer: str = "aidata", broker: Any = None) -> dict:
    """实时快照，统一返回 {"close": 现价, "src": 来源}；取不到 {}。

    调用方只依赖 close 字段（与桥 get_quote 的消费口径一致）。
    """
    order = ("aidata", "bridge") if prefer == "aidata" else ("bridge", "aidata")
    for src in order:
        try:
            if src == "aidata":
                if not _aidata_allowed():
                    raise RuntimeError("AiData 已禁用（测试隔离开关）")
                from agent_tools.datasources import tdx_aidata as ta

                if not ta.available():
                    raise RuntimeError("AiData 不可用")
                raw = ta.get_quote(code) or {}
            else:
                if broker is None:
                    raise RuntimeError("无 broker（桥不可用）")
                raw = broker.get_quote(code, "") or {}
            for key in _PRICE_KEYS:
                px = _f(raw.get(key))
                if px > 0:
                    return {"close": px, "src": src}
        except Exception as exc:  # noqa: BLE001
            _log(f"live_quotes[{src}] {code} 取快照失败: {str(exc)[:120]}")
    return {}