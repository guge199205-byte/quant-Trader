"""通达信 TdxAiData 官方数据接口（实时行情源）。

背景（2026-08-28 通达信测试版新增）：官方提供 TdxAiData.dll/so + tqServer.py，
不依赖本地客户端，Windows/Linux/Mac 通用，行情实时，支持分钟线/分笔/订阅。
文档：help.tdx.com.cn/quant/docs（TdxAiData接口调用章）。

依赖文件（TdxAiData 安装目录，须已就位）：
  libTdxAiData.so  /  TdxAiData.ini（含 token + 服务器地址 aihs.tdx.com.cn:7709）  /  tqServer.py
  TDX_AIDATA_DIR 环境变量指向该目录（默认 /opt/tdx-aidata）

实测约束（2026-08-31）：
  - 测试版接口有突发限流：服务端间歇性返回 "Token Insufficient"（错误码 13），
    实测放行 2-3 次请求后冷却数分钟（与商城积分余额无关，用户确认积分充足）
    → 本模块指数退避重试（10s/20s/40s/60s），并把请求压到最少（批量）
  - count 参数模式不可用（必失败且会带坏同进程后续连接）→
    一律用 start_time/end_time 区间模式，count 由 _start_for_count 换算
  - stock_list 支持多只股票批量，一次调用拿全部 → 用 get_klines_batch
  - TdxAiData 只做数据（K线/分时/分笔/订阅），不提供交易功能；下单仍走 8550 桥
  - 服务器不可达 → 调用抛 RuntimeError，调用方应回退桥行情
"""
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

AIDATA_DIR = os.getenv("TDX_AIDATA_DIR", "/opt/tdx-aidata")

_RETRY_GAPS = [10, 20, 40, 60]  # 秒，Token Insufficient 指数退避

_tqs = None
_load_error: Optional[str] = None


def _load() -> Any:
    """懒加载 tqServer.tqs（首次调用时 import）。"""
    global _tqs, _load_error
    if _tqs is not None or _load_error is not None:
        return _tqs
    try:
        import sys

        if AIDATA_DIR not in sys.path:
            sys.path.insert(0, AIDATA_DIR)
        from tqServer import tqs  # type: ignore

        _tqs = tqs
    except Exception as exc:  # noqa: BLE001
        _load_error = f"TdxAiData 不可用（{AIDATA_DIR}）: {exc}"
    return _tqs


def available() -> bool:
    return _load() is not None


def _require() -> Any:
    t = _load()
    if t is None:
        raise RuntimeError(_load_error or "TdxAiData 未加载")
    return t


def _call_with_retry(fn, *args, **kwargs) -> Any:
    """tqs 调用带指数退避重试：测试版接口突发限流（空结果/异常），
    等待 10s/20s/40s/60s 依次重试；全部失败后空结果返回 None，异常则抛出。"""
    last = None
    for n, gap in enumerate(_RETRY_GAPS + [0]):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if gap:
                time.sleep(gap)
            continue
        if result:
            return result
        if gap:
            time.sleep(gap)
    if last is not None:
        raise RuntimeError(f"TdxAiData 调用异常: {last}") from last
    return None


# ---------- K线（实时，含分钟线） ----------

_PERIOD_MAP = {
    "daily": "1d", "weekly": "1w", "monthly": "1mon",
    "1m": "1m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m",
    "1h": "1h", "45d": "45d", "1q": "1q", "1y": "1y",
}
_BAR_MINUTES = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
_DAY_MARGIN = {"1d": 2, "1w": 15, "1mon": 45, "45d": 90, "1q": 130, "1y": 520}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _start_for_count(period: str, count: int, end: str) -> str:
    """count → 区间模式 start_time（tqs 的 count 参数该 token 不可用）。
    分钟周期按 bar 时长×2 回推；日历周期按根数×日数裕量回推（含周末/节假日）。"""
    minutes = _BAR_MINUTES.get(period)
    try:
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        end_dt = datetime.now()
    if minutes:
        start_dt = end_dt - timedelta(minutes=minutes * count * 2 + 30)
    else:
        start_dt = end_dt - timedelta(days=_DAY_MARGIN.get(period, 2) * count)
    return start_dt.strftime("%Y-%m-%d %H:%M:%S")


def _val(data: dict, field: str, symbol: str, i: int) -> Any:
    """从 {field: DataFrame|{symbol: [...]}} 中取第 i 个值。"""
    f = data.get(field)
    if f is None:
        return None
    try:
        if hasattr(f, "iloc"):  # pandas DataFrame
            return f[symbol].iloc[i]
        if isinstance(f, dict):
            return f.get(symbol, [])[i]
        return f[i]
    except Exception:  # noqa: BLE001
        return None


def _dates(data: dict) -> List[str]:
    """K线日期序列（DataFrame index；无 pandas 兜底模式无日期，空串占位）。"""
    sample = next(iter(data.values())) if data else None
    if sample is None:
        return []
    if hasattr(sample, "index"):
        return [str(d.date()) if hasattr(d, "date") else str(d) for d in sample.index]
    closes = data.get("Close") or {}
    lst = list(closes.values())[0] if isinstance(closes, dict) and closes else None
    return [""] * len(lst) if lst else []


def _bars_from(data: dict, symbol: str) -> List[Dict[str, Any]]:
    """tqs.get_market_data 结果 → [{"date","open","high","low","close","volume","amount"}]
    限流/缺数时接口会返回 NaN 残缺行，close 无效的 bar 直接丢弃（上游视为无行情回退）。"""
    import math

    dates = _dates(data)
    bars = []
    for i in range(len(dates)):
        close = _val(data, "Close", symbol, i)
        try:
            c = float(close)
            valid = math.isfinite(c) and c > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            continue  # NaN/None close → 残缺行
        bars.append({
            "date": dates[i],
            "open": _val(data, "Open", symbol, i),
            "high": _val(data, "High", symbol, i),
            "low": _val(data, "Low", symbol, i),
            "close": close,
            "volume": _val(data, "Volume", symbol, i),
            "amount": _val(data, "Amount", symbol, i),
        })
    return bars


def _query_market_data(symbols: List[str], period: str,
                       start: str, end: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "stock_list": symbols,
        "period": period,
        "start_time": start,
        "end_time": end,
        "dividend_type": "front",
    }
    data = _call_with_retry(_require().get_market_data, **params)
    return data or {}


def get_klines(symbol: str, interval: str = "daily",
               start: str = "", end: str = "", count: int = 0) -> List[Dict[str, Any]]:
    """单只股票K线，与桥 get_klines 同返回格式
    [{"date","open","high","low","close","volume","amount"}]，按日期升序。
    count>0 时自动换算为时间区间并截取最后 count 根。"""
    period = _PERIOD_MAP.get(interval, interval)
    end = end or _now_str()
    if count and not start:
        start = _start_for_count(period, count, end)
    data = _query_market_data([symbol], period, start, end)
    bars = _bars_from(data, symbol)
    if count and len(bars) > count:
        bars = bars[-count:]
    return bars


def get_klines_batch(symbols: List[str], interval: str = "daily",
                     start: str = "", end: str = "", count: int = 0) -> Dict[str, List[Dict[str, Any]]]:
    """多只股票K线，一次 tqs 调用（省 token 配额），返回 {symbol: bars}。"""
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}
    period = _PERIOD_MAP.get(interval, interval)
    end = end or _now_str()
    if count and not start:
        start = _start_for_count(period, count, end)
    data = _query_market_data(symbols, period, start, end)
    out = {}
    for sym in symbols:
        bars = _bars_from(data, sym)
        if count and len(bars) > count:
            bars = bars[-count:]
        out[sym] = bars
    return out


def get_quote(symbol: str) -> Dict[str, Any]:
    """实时快照（get_market_snapshot，最新价/涨跌幅）。"""
    return _call_with_retry(_require().get_market_snapshot, stock_code=symbol)


# ---------- 分时 / 分笔 ----------

def get_minute_data(symbol: str, date: str) -> Dict[str, Any]:
    """指定日期分时数据 {Time, Price, Average, Volume, TotalNum}。

    tqs 类未封装 get_minute_data（仅底层原生 _tdx() 有），直接透传；
    参数名是 codestr/date（位置传参，避免命名不匹配）。
    """
    return _call_with_retry(_require()._tdx().get_minute_data, symbol, date)


def get_tick_data(symbol: str, date: str, startxh: int = 0,
                  wantnum: int = 100) -> Dict[str, Any]:
    """分笔成交 {BSFlag, Price, Time, Volume, TotalNum}。"""
    return _call_with_retry(_require().get_tick_data,
                            stock_code=symbol, date=date, startxh=startxh, wantnum=wantnum)


# ---------- 实时订阅 ----------

def subscribe(symbols: List[str]) -> Any:
    """订阅实时行情（返回回调句柄/结果，具体形态以 tqServer 实现为准）。"""
    return _require().subscribe(stock_list=symbols)


def unsubscribe(symbols: List[str]) -> Any:
    return _require().unsubscribe(stock_list=symbols)


def _test() -> None:
    """自检：可用性 + 日K/5分钟线 + 批量 + 实时快照。"""
    if not available():
        print(f"✗ {_load_error}")
        return
    print("✓ TdxAiData 可用")
    bars = get_klines("600519.SH", interval="daily", count=5)
    print(f"  日K {len(bars)} 根, 最新: {bars[-1] if bars else None}")
    m5 = get_klines("600519.SH", interval="5m", count=3)
    print(f"  5m {len(m5)} 根, 最新: {m5[-1] if m5 else None}")
    batch = get_klines_batch(["600519.SH", "000858.SZ"], interval="daily", count=3)
    for sym, b in batch.items():
        print(f"  批量 {sym}: {len(b)} 根, 最新收盘 {b[-1]['close'] if b else None}")
    try:
        snap = get_quote("600519.SH")
        keys = list(snap.keys())[:8] if isinstance(snap, dict) else type(snap).__name__
        print(f"  快照 keys: {keys}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 快照失败: {e}")


if __name__ == "__main__":
    _test()
