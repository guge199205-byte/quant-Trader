import json
import os
import sys
from datetime import datetime
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


if __name__ == "__main__":
    
    port = int(os.getenv("GETPRICE_HTTP_PORT", "8003"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
