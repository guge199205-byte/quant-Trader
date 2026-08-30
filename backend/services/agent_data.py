"""agent_data 聚合服务：读取交易落盘数据，提供结构化视图。

position.jsonl 每行: {"date", "id", "this_action"?, "positions": {"CASH": ...}}
log 每行: {"signature", "new_messages": [{"role", "content"}]}
兼容日级（date 为 YYYY-MM-DD）与小时级（YYYY-MM-DD HH:MM:SS）两种格式。
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import get_data_root


def list_agents(config: dict, market: str = "us") -> List[Dict[str, Any]]:
    """扫描 data/{agent_data_dir}/ 下的模型目录，返回元信息列表。"""
    markets = config.get("markets", {})
    market_cfg = markets.get(market, {})
    agent_dir = get_data_root(config) / market_cfg.get("data_dir", "agent_data")
    agents = []
    if not agent_dir.exists():
        return agents
    for folder in sorted(agent_dir.iterdir()):
        if not folder.is_dir():
            continue
        position_file = folder / "position" / "position.jsonl"
        log_dir = folder / "log"
        info = {
            "name": folder.name,
            "has_position": position_file.exists(),
            "has_log": log_dir.exists(),
            "latest_date": None,
            "total_records": 0,
            "cash": None,
        }
        if position_file.exists():
            records = _read_jsonl(position_file)
            info["total_records"] = len(records)
            if records:
                info["latest_date"] = records[-1].get("date")
                info["cash"] = records[-1].get("positions", {}).get("CASH")
        agents.append(info)
    return agents


def load_position_records(config: dict, agent: str, market: str = "us",
                          limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """读取某 agent 的 position.jsonl 全量记录（按时间正序）。"""
    position_file = _agent_position_file(config, agent, market)
    if not position_file.exists():
        return []
    records = _read_jsonl(position_file)
    return records[-limit:] if limit and limit > 0 else records


def load_trades(config: dict, agent: str, market: str = "us",
                limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """从 position 记录中提取有实际交易动作的行。"""
    records = load_position_records(config, agent, market)
    trades = [r for r in records if r.get("this_action")]
    return trades[-limit:] if limit and limit > 0 else trades


def load_agent_logs(config: dict, agent: str, market: str = "us",
                    date: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取某 agent 的日志记录；date 指定时只读该日期目录（精确匹配前缀）。"""
    log_dir = _agent_log_dir(config, agent, market)
    if not log_dir.exists():
        return []
    lines = []
    for folder in sorted(log_dir.iterdir()):
        if not folder.is_dir():
            continue
        if date and not folder.name.startswith(date):
            continue
        log_file = folder / "log.jsonl"
        if log_file.exists():
            lines.extend(_read_jsonl(log_file))
    return lines


def compute_equity_series(config: dict, agent: str, market: str = "us") -> List[Dict[str, Any]]:
    """净值序列：用 merged.jsonl 当日价格估值持仓，equity = CASH + Σ(股数×价格)。

    找不到当日价格时用记录中上次已知价格；全部缺失时以 CASH 兜底。
    """
    records = load_position_records(config, agent, market)
    if not records:
        return []
    prices = _load_price_lookup(config, market)
    series = []
    last_known_price: Dict[str, float] = {}
    for record in records:
        positions = record.get("positions", {})
        cash = positions.get("CASH", 0.0)
        date_key = record.get("date", "")[:10]
        market_value = 0.0
        for symbol, shares in positions.items():
            if symbol == "CASH" or not shares:
                continue
            price = None
            day_prices = prices.get(date_key, {})
            if symbol in day_prices:
                price = day_prices[symbol]
                last_known_price[symbol] = price
            elif symbol in last_known_price:
                price = last_known_price[symbol]
            if price is not None:
                market_value += float(shares) * float(price)
        series.append({
            "date": record.get("date"),
            "cash": round(float(cash), 2),
            "market_value": round(market_value, 2),
            "equity": round(float(cash) + market_value, 2),
            "action": record.get("this_action"),
        })
    return series


def _agent_position_file(config: dict, agent: str, market: str) -> Path:
    market_cfg = config.get("markets", {}).get(market, {})
    return get_data_root(config) / market_cfg.get("data_dir", "agent_data") / agent / "position" / "position.jsonl"


def _agent_log_dir(config: dict, agent: str, market: str) -> Path:
    market_cfg = config.get("markets", {}).get(market, {})
    return get_data_root(config) / market_cfg.get("data_dir", "agent_data") / agent / "log"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    lines = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return lines


def _load_price_lookup(config: dict, market: str) -> Dict[str, Dict[str, float]]:
    """构建 {date: {symbol: price}} 查询表（用每日第一个小时价作为当日价）。

    仅当市场为小时级数据时逐 key 取日期前缀；日级数据直接使用。
    """
    market_cfg = config.get("markets", {}).get(market, {})
    if market == "cn":
        merged = get_data_root(config) / "A_stock" / "merged.jsonl"
    else:
        merged = get_data_root(config) / "merged.jsonl"
    lookup: Dict[str, Dict[str, float]] = {}
    if not merged.exists():
        return lookup
    for line in _read_raw_lines(merged):
        doc = json.loads(line) if line else None
        if not doc:
            continue
        symbol = None
        meta = doc.get("Meta Data", {})
        symbol = meta.get("2. Symbol")
        if not symbol:
            continue
        for key, value in doc.items():
            if not key.startswith("Time Series") or not isinstance(value, dict):
                continue
            for ts_key, bar in value.items():
                date_key = ts_key[:10]
                buy_price = bar.get("1. buy price") or bar.get("1. open")
                if buy_price is None:
                    continue
                lookup.setdefault(date_key, {}).setdefault(symbol, float(buy_price))
    return lookup


def _read_raw_lines(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return
