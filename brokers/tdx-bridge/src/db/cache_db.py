"""
通达信数据缓存层 - SQLite

把从通达信拉取的数据 (K线/股票信息/财务/行情快照) 持久化到本地 SQLite,
供后续随时查询, 减少重复连通达信, 数据可追溯。

设计:
- 单文件 SQLite (data/tdx_cache.db), 零配置, Windows 原生支持
- 每种数据一张表, 按 symbol+date 做唯一键
- 自动建表, 写入 upsert
- 提供最近 N 条 / 区间查询
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)


class CacheDb:
    """SQLite 缓存, 线程安全 (桥内多线程访问)."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- 表结构 ----

    def _create_tables(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript("""
            CREATE TABLE IF NOT EXISTS kline (
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL,
                fetched_at TEXT,
                PRIMARY KEY (symbol, period, date)
            );
            CREATE TABLE IF NOT EXISTS stock_info (
                symbol TEXT PRIMARY KEY,
                data TEXT,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS market_snapshot (
                symbol TEXT PRIMARY KEY,
                data TEXT,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS financial (
                symbol TEXT NOT NULL,
                report_type TEXT,
                data TEXT,
                fetched_at TEXT,
                PRIMARY KEY (symbol, report_type)
            );
            CREATE TABLE IF NOT EXISTS sector_stocks (
                block_code TEXT PRIMARY KEY,
                stocks TEXT,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tdx_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method TEXT,
                category TEXT,
                symbol TEXT,
                side TEXT,
                volume REAL,
                price REAL,
                order_id TEXT,
                status TEXT,
                params TEXT,
                result TEXT,
                duration_ms REAL,
                error TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT,
                event TEXT,
                symbol TEXT,
                side TEXT,
                volume REAL,
                price REAL,
                order_id TEXT,
                status TEXT,
                message TEXT,
                detail TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            """)
            self._conn.commit()

    # ---- 通用 upsert ----

    def _upsert(self, table: str, row: dict, conflict_cols: tuple):
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        conflict = ",".join(conflict_cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_cols)
        sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT({conflict}) DO UPDATE SET {updates}")
        with self._lock:
            self._conn.execute(sql, [row[c] for c in cols])
            self._conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # ---- K线 ----

    def save_kline(self, symbol: str, period: str, bars: list[dict]):
        """保存K线. bars: [{date, open, high, low, close, volume, amount}]"""
        for b in bars:
            self._upsert("kline", {
                "symbol": symbol, "period": period,
                "date": b.get("date", ""),
                "open": b.get("open"), "high": b.get("high"),
                "low": b.get("low"), "close": b.get("close"),
                "volume": b.get("volume"), "amount": b.get("amount"),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }, ("symbol", "period", "date"))

    def get_kline(self, symbol: str, period: str = "1d", limit: int = 500) -> list[dict]:
        return self._query(
            "SELECT * FROM kline WHERE symbol=? AND period=? ORDER BY date DESC LIMIT ?",
            (symbol, period, limit))

    # ---- 股票信息 / 快照 / 财务 / 板块 ----

    def save_stock_info(self, symbol: str, data: dict):
        self._upsert("stock_info", {
            "symbol": symbol, "data": json.dumps(data, ensure_ascii=False),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }, ("symbol",))

    def get_stock_info(self, symbol: str) -> Optional[dict]:
        rows = self._query("SELECT data FROM stock_info WHERE symbol=?", (symbol,))
        return json.loads(rows[0]["data"]) if rows else None

    def save_snapshot(self, symbol: str, data: dict):
        self._upsert("market_snapshot", {
            "symbol": symbol, "data": json.dumps(data, ensure_ascii=False),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }, ("symbol",))

    def get_snapshot(self, symbol: str) -> Optional[dict]:
        rows = self._query("SELECT data FROM market_snapshot WHERE symbol=?", (symbol,))
        return json.loads(rows[0]["data"]) if rows else None

    def save_financial(self, symbol: str, report_type: str, data: dict):
        self._upsert("financial", {
            "symbol": symbol, "report_type": report_type,
            "data": json.dumps(data, ensure_ascii=False),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }, ("symbol", "report_type"))

    def get_financial(self, symbol: str, report_type: str = "") -> Optional[dict]:
        if report_type:
            rows = self._query("SELECT data FROM financial WHERE symbol=? AND report_type=?",
                               (symbol, report_type))
        else:
            rows = self._query("SELECT data FROM financial WHERE symbol=? ORDER BY fetched_at DESC LIMIT 1",
                               (symbol,))
        return json.loads(rows[0]["data"]) if rows else None

    def save_sector_stocks(self, block_code: str, stocks: list):
        self._upsert("sector_stocks", {
            "block_code": block_code, "stocks": json.dumps(stocks, ensure_ascii=False),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }, ("block_code",))

    def get_sector_stocks(self, block_code: str) -> Optional[list]:
        rows = self._query("SELECT stocks FROM sector_stocks WHERE block_code=?", (block_code,))
        return json.loads(rows[0]["stocks"]) if rows else None

    # ---- 操作日志 ----

    def log_call(self, method: str, symbol: str, params: dict, result: Any,
                 category: str = "", side: str = "", volume: float = None,
                 price: float = None, order_id: str = "", status: str = "",
                 duration_ms: float = None, error: str = ""):
        """记录所有桥调用 (查询/下单/推送), 精确到耗时/状态/结果."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO tdx_log (method, category, symbol, side, volume, price, "
                "order_id, status, params, result, duration_ms, error, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (method, category, symbol or "", side or "", volume, price,
                 order_id or "", status or "",
                 json.dumps(params, ensure_ascii=False, default=str)[:2000],
                 json.dumps(result, ensure_ascii=False, default=str)[:4000],
                 duration_ms, error or "",
                 datetime.now().isoformat(timespec="milliseconds")))
            self._conn.commit()

    def log_trade(self, plan_id: str, event: str, symbol: str, side: str,
                  volume: float, price: float, order_id: str, status: str,
                  message: str = "", detail: dict = None):
        """记录交易事件 (下单/成交/撤单/失败), 精确到委托号/状态."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO trade_log (plan_id, event, symbol, side, volume, price, "
                "order_id, status, message, detail, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (plan_id or "", event, symbol or "", side or "", volume, price,
                 order_id or "", status or "", message or "",
                 json.dumps(detail or {}, ensure_ascii=False, default=str)[:2000],
                 datetime.now().isoformat(timespec="milliseconds")))
            self._conn.commit()

    def get_trade_logs(self, symbol: str = "", limit: int = 100) -> list[dict]:
        if symbol:
            return self._query(
                "SELECT * FROM trade_log WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, limit))
        return self._query("SELECT * FROM trade_log ORDER BY id DESC LIMIT ?", (limit,))

    def get_logs(self, method: str = "", limit: int = 100) -> list[dict]:
        if method:
            return self._query(
                "SELECT * FROM tdx_log WHERE method=? ORDER BY id DESC LIMIT ?", (method, limit))
        return self._query("SELECT * FROM tdx_log ORDER BY id DESC LIMIT ?", (limit,))

    def get_stats(self) -> dict:
        """返回缓存统计."""
        with self._lock:
            cur = self._conn.cursor()
            counts = {}
            allowed_tables = {"kline", "stock_info", "market_snapshot", "financial",
                              "sector_stocks", "tdx_log", "trade_log"}
            for table in allowed_tables:
                try:
                    # 表名来自硬编码白名单, 双重校验防注入
                    if table not in allowed_tables:
                        continue
                    counts[table] = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except Exception:
                    counts[table] = 0
            return counts

    def get_cache_disk(self) -> int:
        """缓存数据库文件大小 (字节)."""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    # ---- Token 管理 (SHA256 哈希存储, 不存明文) ----

    @staticmethod
    def _hash_token(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def add_token(self, token: str, name: str = "") -> bool:
        """新增 token (返回 False 表示已存在)."""
        h = self._hash_token(token)
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM tokens WHERE token_hash=?", (h,)).fetchone()
            if exists:
                return False
            self._conn.execute(
                "INSERT INTO tokens (token_hash, name, active, created_at) VALUES (?,?,?,?)",
                (h, name, 1, datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()
            return True

    def delete_token(self, token_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM tokens WHERE token_hash=?", (token_hash,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_tokens(self) -> list[dict]:
        return self._query(
            "SELECT id, token_hash, name, active, created_at FROM tokens "
            "ORDER BY id DESC")

    def token_hashes(self) -> set:
        """返回全部活跃 token 的哈希集合."""
        rows = self._query("SELECT token_hash FROM tokens WHERE active=1")
        return {r["token_hash"] for r in rows}

    def is_token_valid(self, token: str) -> bool:
        """校验 token 是否有效."""
        h = self._hash_token(token)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM tokens WHERE token_hash=? AND active=1", (h,)).fetchone()
            return bool(row)
