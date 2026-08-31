import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastmcp import FastMCP

# Add parent directory to Python path to import tools module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

mcp = FastMCP("LocalPrices")

# Ensure project root is on sys.path for absolute imports like `tools.*`
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tools.general_tools import get_config_value


def _open_val(day: Dict[str, Any]) -> Any:
    """兼容两种 merged 格式：小时级（1. buy price）与日线（1. open）。"""
    v = day.get("1. buy price")
    if v is None:
        v = day.get("1. open")
    return v


def _close_val(day: Dict[str, Any]) -> Any:
    """兼容两种 merged 格式：小时级（4. sell price）与日线（4. close）。"""
    v = day.get("4. sell price")
    if v is None:
        v = day.get("4. close")
    return v


def _quantdb_dir() -> Optional[Path]:
    """quantdb parquet 根目录：容器内 /data/quantdb（只读直挂）；本机回退环境变量/quantmind 仓库。
    必须含 1_kline_data 子目录才算有效（/data/quantdb 在本机可能是空目录占位）。"""
    for cand in (Path("/data/quantdb"),
                 Path(os.getenv("QM_QUANTDB_DATA_DIR", "")),
                 Path.home() / "projects/quantmind/data/quantdb"):
        if cand.is_dir() and (cand / "1_kline_data").is_dir():
            return cand
    return None


def _quantdb_daily(symbol: str, date: str) -> Optional[Dict[str, Any]]:
    """quantdb（quantmind parquet）duckdb 查询单日 OHLCV（后复权 daily_backward）。
    symbol 形如 600183.SH；date 形如 2026-08-28。查不到返回 None。"""
    root = _quantdb_dir()
    if root is None or not (symbol.endswith(".SH") or symbol.endswith(".SZ")):
        return None
    dt = date.replace("-", "")
    try:
        import duckdb

        con = duckdb.connect()
        rows = con.execute(
            """
            SELECT dt, open, high, low, close, volume FROM read_parquet(
              ?, union_by_name=true)
            WHERE symbol = ? AND dt = ?
            """,
            [str(root / "1_kline_data/daily_backward/dt=*/data.parquet"),
             symbol, int(dt)],
        ).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    _, open_, high, low, close, volume = rows[0]
    return {
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def _workspace_data_path(filename: str, symbol: Optional[str] = None) -> Path:
    """Get data file path based on symbol (auto-detect market type).

    Args:
        filename: Data filename (e.g., 'merged.jsonl')
        symbol: Stock symbol for auto-detecting market type.
                If symbol ends with .SH or .SZ, use A-stock data path.

    Returns:
        Path to the data file
    """
    base_dir = Path(__file__).resolve().parents[1]

    # Auto-detect market type from symbol
    if symbol and (symbol.endswith(".SH") or symbol.endswith(".SZ")):
        # Chinese A-shares
        return base_dir / "data" / "A_stock" / filename
    else:
        # US stocks (default)
        return base_dir / "data" / filename


def _validate_date_daily(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date must be in YYYY-MM-DD format") from exc

def _validate_date_hourly(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("date must be in YYYY-MM-DD HH:MM:SS format") from exc

@mcp.tool()
def get_price_local(symbol: str, date: str) -> Dict[str, Any]:
    """Read OHLCV data for specified stock and date. Get historical information for specified stock.
    
    Automatically detects date format and calls appropriate function:
    - Daily data: YYYY-MM-DD format (e.g., '2025-10-30')
    - Hourly data: YYYY-MM-DD HH:MM:SS format (e.g., '2025-10-30 14:30:00')

    Args:
        symbol: Stock symbol, e.g. 'IBM' or '600243.SHH'.
        date: Date in 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' format. Based on your current time format.

    Returns:
        Dictionary containing symbol, date and ohlcv data.
    """
    # Detect date format
    result = None
    if ' ' in date or 'T' in date:
        # Contains time component, use hourly
        result =  get_price_local_hourly(symbol, date)
    else:
        # Date only, use daily
        result = get_price_local_daily(symbol, date)
    
    # log_file = get_config_value("LOG_FILE")
    # signature = get_config_value("SIGNATURE")
    
    # log_entry = {
    #     "signature": signature,
    #     "new_messages": [{"role": "tool:get_price_local", "content": result}]
    # }
    # with open(log_file, "a", encoding="utf-8") as f:
    #     f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    return result



def get_price_local_daily(symbol: str, date: str) -> Dict[str, Any]:
    """Read OHLCV data for specified stock and date. Get historical information for specified stock.

    Args:
        symbol: Stock symbol, e.g. 'IBM' or '600243.SHH'.
        date: Date in 'YYYY-MM-DD' format.

    Returns:
        Dictionary containing symbol, date and ohlcv data.
    """
    filename = "merged.jsonl"
    try:
        _validate_date_daily(date)
    except ValueError as e:
        return {"error": str(e), "symbol": symbol, "date": date}

    # A股优先 quantdb（duckdb 查询分析，后复权），查不到再回退本地 merged.jsonl
    if symbol.endswith(".SH") or symbol.endswith(".SZ"):
        qd = _quantdb_daily(symbol, date)
        if qd is not None:
            return {
                "symbol": symbol,
                "date": date,
                "ohlcv": {
                    "open": qd["open"],
                    "high": qd["high"],
                    "low": qd["low"],
                    "close": qd["close"],
                    "volume": qd["volume"],
                },
                "source": "quantdb",
            }

    data_path = _workspace_data_path(filename, symbol)
    if not data_path.exists():
        return {"error": f"Data file not found: {data_path}", "symbol": symbol, "date": date}

    with data_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            doc = json.loads(line)
            meta = doc.get("Meta Data", {})
            if meta.get("2. Symbol") != symbol:
                continue
            series = doc.get("Time Series (Daily)", {})
            day = series.get(date)
            if day is None:
                sample_dates = sorted(series.keys(), reverse=True)[:5]
                return {
                    "error": f"Data not found for date {date}. Please verify the date exists in data. Sample available dates: {sample_dates}",
                    "symbol": symbol,
                    "date": date,
                }
            if date == get_config_value("TODAY_DATE"):
                return {
                    "symbol": symbol,
                    "date": date,
                    "ohlcv": {
                        "open": _open_val(day),
                        "high": "You can not get the current high price",
                        "low": "You can not get the current low price", 
                        "close": "You can not get the next close price",
                        "volume": "You can not get the current volume",
                    },
                }
            else:
                return {
                    "symbol": symbol,
                    "date": date,
                    "ohlcv": {
                        "open": _open_val(day),
                        "high": day.get("2. high"),
                        "low": day.get("3. low"), 
                        "close": _close_val(day),
                        "volume": day.get("5. volume"),
                    },
                }


    return {"error": f"No records found for stock {symbol} in local data", "symbol": symbol, "date": date}


def get_price_local_hourly(symbol: str, date: str) -> Dict[str, Any]:
    """Read OHLCV data for specified stock and date. Get historical information for specified stock.

    Args:
        symbol: Stock symbol, e.g. 'IBM' or '600243.SHH'.
        date: Date in 'YYYY-MM-DD' format.

    Returns:
        Dictionary containing symbol, date and ohlcv data.
    """
    filename = "merged.jsonl"
    try:
        _validate_date_hourly(date)
    except ValueError as e:
        return {"error": str(e), "symbol": symbol, "date": date}

    data_path = _workspace_data_path(filename)
    if not data_path.exists():
        return {"error": f"Data file not found: {data_path}", "symbol": symbol, "date": date}

    with data_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            doc = json.loads(line)
            meta = doc.get("Meta Data", {})
            if meta.get("2. Symbol") != symbol:
                continue
            series = doc.get("Time Series (60min)", {})
            day = series.get(date)
            if day is None:
                sample_dates = sorted(series.keys(), reverse=True)[:5]
                return {
                    "error": f"Data not found for date {date}. Please verify the date exists in data. Sample available dates: {sample_dates}",
                    "symbol": symbol,
                    "date": date
                }
            if date == get_config_value("TODAY_DATE"):
                return {
                    "symbol": symbol,
                    "date": date,
                    "ohlcv": {
                        "open": _open_val(day),
                        "high": "You can not get the current high price",
                        "low": "You can not get the current low price", 
                        "close": "You can not get the next close price",
                        "volume": "You can not get the current volume",
                    },
                }
            else:
                return {
                    "symbol": symbol,
                    "date": date,
                    "ohlcv": {
                        "open": _open_val(day),
                        "high": day.get("2. high"),
                        "low": day.get("3. low"), 
                        "close": _close_val(day),
                        "volume": day.get("5. volume"),
                    },
                }

    return {"error": f"No records found for stock {symbol} in local data", "symbol": symbol, "date": date}


def get_price_local_function(symbol: str, date: str, filename: str = "merged.jsonl") -> Dict[str, Any]:
    """Read OHLCV data for specified stock and date from local JSONL data.

    Args:
        symbol: Stock symbol, e.g. 'IBM' or '600243.SHH'.
        date: Date in 'YYYY-MM-DD' format.
        filename: Data filename, defaults to 'merged.jsonl' (located in data/ under project root).

    Returns:
        Dictionary containing symbol, date and ohlcv data.
    """
    try:
        _validate_date(date)
    except ValueError as e:
        return {"error": str(e), "symbol": symbol, "date": date}

    data_path = _workspace_data_path(filename, symbol)
    if not data_path.exists():
        return {"error": f"Data file not found: {data_path}", "symbol": symbol, "date": date}

    with data_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            doc = json.loads(line)
            meta = doc.get("Meta Data", {})
            if meta.get("2. Symbol") != symbol:
                continue
            series = doc.get("Time Series (Daily)", {})
            day = series.get(date)
            if day is None:
                sample_dates = sorted(series.keys(), reverse=True)[:5]
                return {
                    "error": f"Data not found for date {date}. Please verify the date exists in data. Sample available dates: {sample_dates}",
                    "symbol": symbol,
                    "date": date,
                }
            return {
                "symbol": symbol,
                "date": date,
                "ohlcv": {
                    "buy price": _open_val(day),
                    "high": day.get("2. high"),
                    "low": day.get("3. low"),
                    "sell price": _close_val(day),
                    "volume": day.get("5. volume"),
                },
            }

    return {"error": f"No records found for stock {symbol} in local data", "symbol": symbol, "date": date}


# ---------- A股盘中 L2 级行情（TdxAiData 通达信官方接口） ----------

_L2_CACHE_TTL = 30  # 秒；TdxAiData 测试版限流严重（放行 2-3 次后冷却），必须低频调用
_l2_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


@mcp.tool()
def get_l2_market_data(symbol: str, ticks: int = 100) -> Dict[str, Any]:
    """A股盘中 L2 级行情：五档盘口 + 最近逐笔成交（主动买卖聚合）。仅支持 A 股（如 600519.SH）。

    数据源 TdxAiData（通达信官方接口）。⚠️ 限流严格：每只股票 30 秒内重复调用返回缓存；
    只对持仓/候选股使用，不要对全市场循环调用。

    Args:
        symbol: A股代码（后缀式），如 '600519.SH'、'000001.SZ'
        ticks: 逐笔条数上限（1-500，默认 100）

    Returns:
        snapshot: 五档盘口/最新价/开高低/昨收/内外盘/涨速
        ticks: 最近逐笔 [{time, price, volume, side}]
        agg: 逐笔聚合 {buy_vol, sell_vol, net_buy_vol, buy_pct, n}
        ok/error: 结果状态
    """
    now = time.time()
    cached = _l2_cache.get(symbol)
    if cached and now - cached[0] < _L2_CACHE_TTL:
        return {**cached[1], "cached": True}
    try:
        from agent_tools.datasources.tdx_aidata import get_quote, get_tick_data

        if ticks < 1 or ticks > 500:
            ticks = 100
        q = get_quote(symbol)
        if not isinstance(q, dict):
            return {"ok": False, "error": f"快照异常: {q}", "symbol": symbol}
        today = date.today().isoformat()
        try:
            tk = get_tick_data(symbol, today, startxh=0, wantnum=ticks)
        except Exception as e:  # 盘中数据可能尚未就绪/接口限流
            tk = {"error": f"{type(e).__name__}: {e}"}
        ticks_out, buy_vol, sell_vol = [], 0, 0
        if isinstance(tk, dict) and "Price" in tk:
            for i, price in enumerate(tk["Price"]):
                flag = str(tk.get("BSFlag", ["0"])[i] if i < len(tk.get("BSFlag", [])) else "0")
                vol = int(float(tk.get("Volume", ["0"])[i]) if i < len(tk.get("Volume", [])) else 0)
                side = {"1": "buy", "2": "sell"}.get(flag, "neutral")
                if side == "buy":
                    buy_vol += vol
                elif side == "sell":
                    sell_vol += vol
                ticks_out.append({
                    "time": tk.get("Time", [""])[i] if i < len(tk.get("Time", [])) else "",
                    "price": float(price) if price not in ("", None) else None,
                    "volume": vol,
                    "side": side,
                })
        total = buy_vol + sell_vol
        result = {
            "ok": True,
            "cached": False,
            "symbol": symbol,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "snapshot": {
                "now": _l2_num(q.get("Now")),
                "open": _l2_num(q.get("Open")),
                "high": _l2_num(q.get("Max")),
                "low": _l2_num(q.get("Min")),
                "last_close": _l2_num(q.get("LastClose")),
                "avg_price": _l2_num(q.get("Average")),
                "speed_5min": _l2_num(q.get("Before5MinNow")),
                "inside_vol": _l2_num(q.get("Inside")),
                "outside_vol": _l2_num(q.get("Outside")),
                "bid": [_l2_num(v) for v in q.get("Buyp", [])[:5]],
                "bid_vol": [_l2_num(v) for v in q.get("Buyv", [])[:5]],
                "ask": [_l2_num(v) for v in q.get("Sellp", [])[:5]],
                "ask_vol": [_l2_num(v) for v in q.get("Sellv", [])[:5]],
            },
            "ticks": ticks_out[-ticks:],
            "agg": {
                "n": len(ticks_out),
                "buy_vol": buy_vol,
                "sell_vol": sell_vol,
                "net_buy_vol": buy_vol - sell_vol,
                "buy_pct": round(buy_vol / total * 100, 1) if total else None,
            },
        }
        _l2_cache[symbol] = (now, result)
        return result
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "symbol": symbol}


def _l2_num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- A股盘中新闻（Huntly/RSSHub 聚合 → quantmind /api/v1/news） ----------

NEWS_API_BASE = os.getenv("NEWS_API_BASE", "http://172.17.0.1:8000")
_NEWS_CACHE_TTL = 60  # 秒
_news_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


@mcp.tool()
def get_stock_news(symbol: str, hours: int = 24, limit: int = 10) -> Dict[str, Any]:
    """A股个股盘中新闻：RSS 聚合（财联社/同花顺/界面等）+ 情感标签。仅支持 A 股。

    数据源：Huntly + RSSHub 聚合（quantmind /api/v1/news/articles 按 ticker 检索），
    每篇带 enrichment（tickers/情感）。60 秒内重复调用返回缓存。

    Args:
        symbol: A股代码（后缀式），如 '600519.SH'
        hours: 回看小时数（1-168，默认 24；盘中分析建议 8）
        limit: 返回条数上限（1-30，默认 10）

    Returns:
        articles: [{title, summary, source, published_at, url, sentiment}]
        agg: 情感汇总 {bullish, bearish, neutral}
        ok/error: 结果状态
    """
    import urllib.request
    from datetime import datetime, timedelta, timezone

    now = time.time()
    cached = _news_cache.get(symbol)
    if cached and now - cached[0] < _NEWS_CACHE_TTL:
        return {**cached[1], "cached": True}
    hours = max(1, min(168, hours))
    limit = max(1, min(30, limit))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{NEWS_API_BASE}/api/v1/news/articles?tickers={symbol}"
           f"&since={since}&page_size={limit}&sort=time_desc")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "symbol": symbol}
    arts = payload.get("articles", []) or []
    articles, agg = [], {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in arts:
        en = a.get("enrichment") or {}
        label = (en.get("sentiment_label") or "neutral").lower()
        agg[label if label in agg else "neutral"] += 1
        articles.append({
            "title": a.get("title"),
            "summary": (a.get("summary") or "")[:200],
            "source": a.get("source_name"),
            "published_at": (a.get("published_at") or "")[:16],
            "url": a.get("url"),
            "sentiment": label,
        })
    result = {"ok": True, "cached": False, "symbol": symbol, "articles": articles, "agg": agg}
    _news_cache[symbol] = (now, result)
    return result


if __name__ == "__main__":

    port = int(os.getenv("GETPRICE_HTTP_PORT", "8003"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
