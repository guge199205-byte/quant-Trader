"""数据平台：多市场 parquet 仓库浏览 / 预览（借鉴 quantmind 数据管理台）。

数据直接复用本机 quantmind 仓库（宿主 /home/zbox/projects/quantmind/data/*，
容器内经 /data/* 只读直挂），同一份 parquet，格式与 quantmind 完全一致。
本服务只做三件事：
  - catalog   数据集目录统计（分组 / 文件数 / 大小 / 数据区间）
  - preview   数据集本地预览（symbol 检索 + 行数 + JSON 化表格）
  - scan      文件夹选择预检（识别目录下的数据集 + 文件数 / 大小）
不含同步 / 远端 / 云端直供：那是 quantmind 的同步链路，这里不做。

规格与读取逻辑移植自 quantmind：
  backend/shared/quantdb_datasets.py（A股 28 数据集）
  backend/services/api/routers/admin/global_market_console.py（US/HK/FUTURES）
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Layout = Literal["partition", "symbol", "single"]

MAX_PREVIEW_ROWS = 200
MAX_SYMBOL_CHOICES = 500

# 状态文件（/app/data 挂载，宿主 Quant-Trader/data/ 可见）：
# 记录各市场用户自定义的数据根目录
_STATE_FILE = Path(os.getenv("DATA_PLATFORM_STATE", "/app/data/data_platform.json"))


@dataclass(frozen=True)
class DatasetSpec:
    dataset: str
    name: str
    category_id: str
    group: str
    rel_dir: str
    layout: Layout
    note: str = ""


GROUPS: list[dict[str, str]] = [
    {"id": "kline", "name": "K线行情", "category_id": "1"},
    {"id": "base_sector", "name": "基础板块", "category_id": "2"},
    {"id": "financial", "name": "财务数据", "category_id": "3"},
    {"id": "bond_etf", "name": "债券/ETF", "category_id": "4"},
    {"id": "analyst", "name": "分析师/持仓/期权", "category_id": "4"},
    {"id": "technical", "name": "技术衍生", "category_id": "5"},
    {"id": "ml", "name": "ML数据集", "category_id": "6"},
]

# ---------------------------------------------------------------------------
# 数据集规格（移植自 quantmind）
# ---------------------------------------------------------------------------

_QUANTDB: tuple[DatasetSpec, ...] = (
    # 1 K线行情
    DatasetSpec("daily_forward", "日线前复权", "1", "kline", "1_kline_data/daily_forward", "partition", "训练/回测主用，随除权每日全量重写"),
    DatasetSpec("daily_backward", "日线后复权", "1", "kline", "1_kline_data/daily_backward", "partition", "当前价×复权因子，除权后整段回溯"),
    DatasetSpec("daily_unadjusted", "日线不复权", "1", "kline", "1_kline_data/daily_unadjusted", "partition", "原始价，撮合/涨跌停判定用"),
    DatasetSpec("index_daily", "指数日线", "1", "kline", "1_kline_data/index_daily", "partition", "主要指数日K走势"),
    DatasetSpec("min5_kline", "5分钟线", "1", "kline", "1_kline_data/min5_kline", "symbol", "5 分钟级日内行情"),
    DatasetSpec("min1_kline", "1分钟线", "1", "kline", "1_kline_data/min1_kline", "symbol", "体积大，按需同步"),
    DatasetSpec("tick_data", "Tick逐笔", "1", "kline", "1_kline_data/tick_data", "partition", "逐笔成交，流量消耗极高"),
    # 2 基础板块
    DatasetSpec("instrument_detail", "个股详情", "2", "base_sector", "2_base_sector/instrument_detail", "single", "152 列基本面快照"),
    DatasetSpec("sector_concept", "板块概念", "2", "base_sector", "2_base_sector/sector_concept", "single", "行业/概念板块成分"),
    DatasetSpec("index_weights", "指数权重", "2", "base_sector", "2_base_sector/index_weights", "symbol", "沪深300/中证500/1000 等"),
    DatasetSpec("trading_calendar", "交易日历", "2", "base_sector", "2_base_sector/trading_calendar", "single", "A股交易日历"),
    DatasetSpec("margin_trading", "融资融券", "2", "base_sector", "2_base_sector/margin_trading", "partition", "两融余额明细"),
    # 3 财务数据
    DatasetSpec("balance", "资产负债表", "3", "financial", "3_financial_data/balance", "symbol", "资产/负债/权益"),
    DatasetSpec("income", "利润表", "3", "financial", "3_financial_data/income", "symbol", "营收/净利/每股收益"),
    DatasetSpec("cashflow", "现金流量表", "3", "financial", "3_financial_data/cashflow", "symbol", "经营/投资/筹资现金流"),
    DatasetSpec("capital", "股本结构", "3", "financial", "3_financial_data/capital", "symbol", "总股本/流通股本变动"),
    DatasetSpec("pershare_index", "每股指标", "3", "financial", "3_financial_data/pershare_index", "symbol", "每股收益/净资产等"),
    DatasetSpec("dividend_factors", "分红因子", "3", "financial", "3_financial_data/dividend_factors", "symbol", "历次分红送转因子"),
    DatasetSpec("holder_num", "股东户数", "3", "financial", "3_financial_data/holder_num", "symbol", "股东户数变化"),
    # 4 债券/ETF
    DatasetSpec("etf_pcf", "ETF申赎清单", "4", "bond_etf", "4_bond_etf/etf_pcf", "symbol", "ETF 申购赎回清单"),
    DatasetSpec("convertible_bond", "可转债", "4", "bond_etf", "4_bond_etf/convertible_bond", "symbol", "可转债行情与条款"),
    # 5 技术衍生
    DatasetSpec("valuation", "估值", "5", "technical", "5_technical_derived/valuation", "partition", "PE/PB/市值"),
    DatasetSpec("technical_indicators", "技术指标", "5", "technical", "5_technical_derived/technical_indicators", "partition", "MA/MACD/RSI 等技术指标"),
    DatasetSpec("market_sentiment", "市场情绪", "5", "technical", "5_technical_derived/market_sentiment", "partition", "市场情绪指标"),
    # 6 ML数据集
    DatasetSpec("features_daily", "日频特征", "6", "ml", "6_ml_datasets/features_daily", "partition", "技术指标 + 估值合并，PG 填充主源"),
    DatasetSpec("l1_factors", "L1 因子", "6", "ml", "6_ml_datasets/l1_factors", "partition", "因子挖掘核心"),
    DatasetSpec("l2_factors", "L2 因子", "6", "ml", "6_ml_datasets/l2_factors", "partition", "高频微观因子"),
    DatasetSpec("l1_l2_factors", "L1+L2 合并", "6", "ml", "6_ml_datasets/l1_l2_factors", "partition", "L1 与 L2 因子合并集"),
)

_MARKET_BASE: tuple[DatasetSpec, ...] = (
    # 1 K线行情
    DatasetSpec("daily_forward", "日线", "1", "kline", "1_kline_data/daily_forward", "partition", "akshare 日线(不复权)"),
    DatasetSpec("index_daily", "指数日线", "1", "kline", "1_kline_data/index_daily", "partition"),
    # 2 基础板块
    DatasetSpec("instrument_detail", "标的详情", "2", "base_sector", "2_base_sector/instrument_detail", "single"),
    DatasetSpec("sector", "行业板块", "2", "base_sector", "2_base_sector/sector", "symbol"),
    DatasetSpec("f10", "基本面快照", "2", "base_sector", "2_base_sector/f10", "symbol"),
    # 3 财务数据
    DatasetSpec("income", "利润表", "3", "financial", "3_financial_data/income", "symbol"),
    DatasetSpec("balance", "资产负债表", "3", "financial", "3_financial_data/balance", "symbol"),
    DatasetSpec("cashflow", "现金流量表", "3", "financial", "3_financial_data/cashflow", "symbol"),
    DatasetSpec("dividend", "分红", "3", "financial", "3_financial_data/dividend", "symbol"),
    DatasetSpec("splits", "拆股", "3", "financial", "3_financial_data/splits", "symbol"),
    # 5 技术衍生
    DatasetSpec("valuation", "估值", "5", "technical", "5_technical_derived/valuation", "partition", "yahoo info 快照"),
    # 4 分析师/持仓/期权
    DatasetSpec("recommendations", "分析师评级", "4", "analyst", "4_analyst/recommendations", "symbol"),
    DatasetSpec("upgrades_downgrades", "评级调整", "4", "analyst", "4_analyst/upgrades_downgrades", "symbol"),
    DatasetSpec("earnings_history", "盈利历史", "4", "analyst", "4_analyst/earnings_history", "symbol"),
    DatasetSpec("earnings_dates", "财报日期", "4", "analyst", "4_analyst/earnings_dates", "symbol"),
    DatasetSpec("earnings_estimate", "盈利预期", "4", "analyst", "4_analyst/earnings_estimate", "symbol"),
    DatasetSpec("revenue_estimate", "营收预期", "4", "analyst", "4_analyst/revenue_estimate", "symbol"),
    DatasetSpec("growth_estimates", "增长预期", "4", "analyst", "4_analyst/growth_estimates", "symbol"),
    DatasetSpec("analyst_price_targets", "目标价", "4", "analyst", "4_analyst/analyst_price_targets", "symbol"),
    DatasetSpec("major_holders", "主要股东", "4", "analyst", "4_analyst/major_holders", "symbol"),
    DatasetSpec("mutual_fund_holders", "共同基金持仓", "4", "analyst", "4_analyst/mutual_fund_holders", "symbol"),
    DatasetSpec("calendar", "分红/财报日历", "4", "analyst", "4_analyst/calendar", "symbol"),
    DatasetSpec("insider_transactions", "内部人交易", "4", "analyst", "4_analyst/insider_transactions", "symbol"),
    DatasetSpec("options_chain", "期权链", "4", "analyst", "4_options", "symbol"),
)

_QUANTUS: tuple[DatasetSpec, ...] = _MARKET_BASE + (
    DatasetSpec("us_universe", "标的池(市值Top)", "2", "base_sector", "2_base_sector/us_universe", "single", "按市值 Top1000 扩容的标的池与新增代码清单"),
    DatasetSpec("l1_factors", "L1因子(日频)", "6", "ml", "6_ml_datasets/l1_factors", "partition", "本地计算的量价因子日频分区，训练直连读取"),
)

_QUANTHK: tuple[DatasetSpec, ...] = _MARKET_BASE + (
    DatasetSpec("akshare_valuation", "估值(akshare)", "2", "base_sector", "2_base_sector/akshare_valuation", "symbol", "akshare 真实估值：PE/PB/PS/PCF + 排名"),
    DatasetSpec("akshare_financial", "财务指标(akshare)", "2", "base_sector", "2_base_sector/akshare_financial", "symbol", "akshare 财务指标：EPS/ROE/市值/股息率 21项"),
    DatasetSpec("akshare_profile", "公司资料(akshare)", "2", "base_sector", "2_base_sector/akshare_profile", "symbol", "akshare 公司资料：行业/董事长/员工数等"),
    DatasetSpec("ccass_top50", "CCASS机构持仓", "2", "base_sector", "2_base_sector/ccass_top50", "partition", "港股CCASS top50机构持股，stock_code 5位"),
    DatasetSpec("hsgt_south", "南向资金(港股通)", "2", "base_sector", "2_base_sector/hsgt_south", "partition", "港股通南向资金持仓，symbol 4位+.HK"),
    DatasetSpec("ah_premium", "AH溢价", "2", "base_sector", "2_base_sector/ah_premium", "partition", "A/H 配对溢价率日截面"),
    DatasetSpec("ah_membership", "AH配对清单", "2", "base_sector", "2_base_sector/ah_membership", "single"),
    DatasetSpec("hsgt_membership", "港股通成分", "2", "base_sector", "2_base_sector/hsgt_membership", "single"),
    DatasetSpec("index_weights", "指数成分权重", "2", "base_sector", "2_base_sector/index_weights", "symbol", "中证港股通系列指数成分权重"),
    DatasetSpec("adjust_factors", "复权因子", "2", "base_sector", "2_base_sector/adjust_factors", "symbol", "由付费源昨收推算的复权因子链"),
    DatasetSpec("l1_factors", "L1因子(日频)", "6", "ml", "6_ml_datasets/l1_factors", "partition", "本地计算的量价因子日频分区"),
)

_QUANTFUTURES: tuple[DatasetSpec, ...] = (
    DatasetSpec("daily_forward", "期货日K", "1", "kline", "1_kline_data/daily_forward", "partition", "期货/贵金属日K（国际 CL.FUT / 国内主力 / 上金所）"),
    DatasetSpec("contracts_daily", "分合约日K", "1", "kline", "2_base_sector/contracts_daily", "symbol", "国内分合约日K（含真实结算价/持仓量）"),
    DatasetSpec("futures_realtime", "实时行情", "2", "base_sector", "2_base_sector/futures_realtime", "symbol", "国际/国内期货实时快照"),
    DatasetSpec("warehouse_receipts", "交易所仓单", "2", "base_sector", "2_base_sector/warehouse_receipts", "partition", "DCE/CZCE/GFEX 仓单日报"),
    DatasetSpec("member_positions", "会员持仓排名", "2", "base_sector", "2_base_sector/member_positions", "partition", "DCE/GFEX 前20会员多空持仓"),
    DatasetSpec("cftc", "CFTC持仓", "2", "base_sector", "2_base_sector/cftc", "single", "CFTC COT 周度持仓"),
    DatasetSpec("fx_daily", "汇率(中行牌价)", "2", "base_sector", "2_base_sector/fx_daily", "symbol", "主流货币兑人民币日度牌价"),
    DatasetSpec("l1_factors", "L1因子(日频)", "6", "ml", "6_ml_datasets/l1_factors", "partition", "本地计算的量价因子日频分区"),
)

SPECS: dict[str, tuple[DatasetSpec, ...]] = {
    "quantdb": _QUANTDB,
    "quantus": _QUANTUS,
    "quanthk": _QUANTHK,
    "quantfutures": _QUANTFUTURES,
}

MARKETS: list[dict[str, str]] = [
    {"id": "quantdb", "label": "A股市场", "code": "QuantDB", "flag": "🇨🇳", "beta": False, "default_root": "/data/quantdb"},
    {"id": "quanthk", "label": "港股市场", "code": "QuantHK", "flag": "🇭🇰", "beta": True, "default_root": "/data/quanthk"},
    {"id": "quantus", "label": "美股市场", "code": "QuantUS", "flag": "🇺🇸", "beta": True, "default_root": "/data/quantus"},
    {"id": "quantfutures", "label": "国内期货", "code": "QuantFutures", "flag": "⚡", "beta": True, "default_root": "/data/quantfutures"},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 数据根目录
# ---------------------------------------------------------------------------

def _env_root(market: str) -> str:
    """env 覆盖（QM_QUANTDB_DATA_DIR 等）→ 默认 /data/{market}。"""
    env_map = {
        "quantdb": "QM_QUANTDB_DATA_DIR",
        "quantus": "QM_QUANTUS_DATA_DIR",
        "quanthk": "QM_QUANTHK_DATA_DIR",
        "quantfutures": "QM_QUANTFUTURES_DATA_DIR",
    }
    return os.getenv(env_map[market], "").strip() or next(
        m["default_root"] for m in MARKETS if m["id"] == market
    )


def _load_state() -> dict[str, str]:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("data_platform state 写入失败: %s", exc)


def data_root(market: str) -> str:
    """当前数据根：用户自定义（state 文件）优先 → env → 默认 /data/{market}。"""
    custom = _load_state().get(market, "").strip()
    return custom or _env_root(market)


def set_data_root(market: str, root: str) -> str:
    state = _load_state()
    state[market] = root.strip()
    _save_state(state)
    return state[market]


# ---------------------------------------------------------------------------
# 目录统计
# ---------------------------------------------------------------------------

def _partition_dates(root: Path) -> list[str]:
    out = []
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith("dt="):
            out.append(p.name[3:])
        elif p.is_dir() and p.name.startswith("quarter="):
            out.append(p.name[8:])
    out.sort()
    return out


_DATE_IN_NAME = re.compile(r"(20\d{6})")


def _dataset_dates(spec: DatasetSpec, d: Path) -> list[str]:
    """按日数据集的可用日期（分区目录 + 平铺文件名两种形态都要计入）。"""
    dates = set(_partition_dates(d))
    for f in d.glob("*.parquet"):
        m = _DATE_IN_NAME.search(f.stem)
        if m:
            dates.add(m.group(1))
    return sorted(dates)


def _dataset_stats(spec: DatasetSpec, root: Path) -> dict[str, Any]:
    d = root / spec.rel_dir
    if not d.is_dir():
        return {"synced": False, "files": 0, "size_mb": 0.0}
    files = [f for f in d.rglob("*.parquet") if f.is_file()]
    size_mb = round(sum(f.stat().st_size for f in files) / 1024 / 1024, 1)
    stats: dict[str, Any] = {"synced": bool(files), "files": len(files), "size_mb": size_mb}
    if spec.layout == "partition":
        dates = _dataset_dates(spec, d)
        if dates:
            stats["start_date"] = dates[0]
            stats["end_date"] = dates[-1]
            stats["partitions"] = len(dates)
    if files:
        latest = max(f.stat().st_mtime for f in files)
        stats["updated_at"] = datetime.fromtimestamp(latest, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return stats


def build_catalog(market: str) -> dict[str, Any]:
    root = Path(data_root(market))
    items = []
    for spec in SPECS[market]:
        items.append({
            "dataset": spec.dataset,
            "name": spec.name,
            "group": spec.group,
            "category_id": spec.category_id,
            "layout": spec.layout,
            "rel_dir": spec.rel_dir,
            "note": spec.note,
            **_dataset_stats(spec, root),
        })
    groups = []
    for g in GROUPS:
        members = [it for it in items if it["group"] == g["id"]]
        if not members:
            continue
        groups.append({
            **g,
            "dataset_count": len(members),
            "synced_count": sum(1 for it in members if it["synced"]),
            "files": sum(it["files"] for it in members),
            "size_mb": round(sum(it["size_mb"] for it in members), 1),
        })
    return {
        "market": market,
        "data_dir": str(root),
        "exists": root.is_dir(),
        "groups": groups,
        "datasets": items,
        "timestamp": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 文件夹选择预检（scan）：识别 root 下的数据集 + 文件数/大小
# ---------------------------------------------------------------------------

def scan_folder(market: str, root_str: str) -> dict[str, Any]:
    root = Path(root_str.strip() or data_root(market))
    if not root.is_dir():
        return {"root": str(root), "exists": False, "total_files": 0, "total_bytes": 0,
                "datasets": [], "unknown": [], "timestamp": _now_iso()}

    datasets = []
    for spec in SPECS[market]:
        d = root / spec.rel_dir
        if not d.is_dir():
            continue
        files = [f for f in d.rglob("*.parquet") if f.is_file()]
        datasets.append({
            "dataset": spec.dataset,
            "name": spec.name,
            "layout": spec.layout,
            "rel_dir": spec.rel_dir,
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        })

    # 未登记的目录（根下非规格内的一级数据目录，便于发现新数据）
    known_prefixes = {spec.rel_dir.split("/")[0] for spec in SPECS[market]}
    unknown = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if d.name in known_prefixes:
            continue
        files = [f for f in d.rglob("*.parquet") if f.is_file()]
        if files:
            unknown.append({
                "rel_dir": d.name,
                "files": len(files),
                "bytes": sum(f.stat().st_size for f in files),
            })

    all_files = sum(ds["files"] for ds in datasets) + sum(u["files"] for u in unknown)
    all_bytes = sum(ds["bytes"] for ds in datasets) + sum(u["bytes"] for u in unknown)
    return {
        "root": str(root),
        "exists": True,
        "total_files": all_files,
        "total_bytes": all_bytes,
        "datasets": datasets,
        "unknown": unknown,
        "timestamp": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 数据预览（本地 parquet 优先；JSON 化规则与 quantmind 一致）
# ---------------------------------------------------------------------------

def _pick_local_file(spec: DatasetSpec, root: Path, symbol: str | None) -> Path | None:
    d = root / spec.rel_dir
    if not d.is_dir():
        return None
    if spec.layout == "partition":
        dates = _partition_dates(d)
        for dt in reversed(dates):
            files = sorted((d / f"dt={dt}").glob("*.parquet"))
            if files:
                return files[0]
        flat = sorted(d.glob("*.parquet"))
        return flat[-1] if flat else None
    files = sorted(f for f in d.glob("*.parquet") if f.is_file())
    if not files:
        return None
    if symbol:
        target = symbol.strip().upper()
        for f in files:
            if f.stem.upper() == target:
                return f
        raise LookupError(f"{spec.dataset} 无 {symbol} 的本地文件")
    return files[0]


def _symbol_choices(spec: DatasetSpec, root: Path) -> dict[str, Any]:
    if spec.layout != "symbol":
        return {}
    d = root / spec.rel_dir
    if not d.is_dir():
        return {}
    stems = sorted(f.stem for f in d.glob("*.parquet") if f.is_file())
    return {"symbol_total": len(stems), "symbol_choices": stems[:MAX_SYMBOL_CHOICES]}


def _json_safe(value: Any) -> Any:
    """parquet 单元格 → JSON 可序列化（ndarray/list/NaN/Inf/Timestamp 逐值处理）。"""
    import numpy as np
    import pandas as pd

    if value is None:
        return None
    if isinstance(value, (np.ndarray, list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, bytes)):
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    return str(value)


def preview_dataset(market: str, dataset: str, symbol: str | None = None, limit: int = 50) -> dict[str, Any]:
    import pandas as pd

    specs = {s.dataset: s for s in SPECS[market]}
    spec = specs.get(dataset)
    if spec is None:
        raise LookupError(f"未知数据集: {dataset}")
    root = Path(data_root(market))
    file_path = _pick_local_file(spec, root, symbol)
    if file_path is None:
        raise LookupError(f"{dataset} 本地无数据（目录 {root / spec.rel_dir} 无 parquet 文件）")

    df = pd.read_parquet(file_path)
    if df is None or df.empty:
        return {
            "dataset": dataset,
            "name": spec.name,
            "source": "local",
            "file": str(file_path.relative_to(root)),
            "rows_total": 0,
            "column_count": 0,
            "columns": [],
            "data": [],
            **_symbol_choices(spec, root),
            "timestamp": _now_iso(),
        }

    columns = [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns]
    records = _json_safe_records(df.head(min(limit, MAX_PREVIEW_ROWS)))
    return {
        "dataset": dataset,
        "name": spec.name,
        "source": "local",
        "file": str(file_path.relative_to(root)),
        "rows_total": int(len(df)),
        "column_count": len(columns),
        "columns": columns,
        "data": records,
        **_symbol_choices(spec, root),
        "timestamp": _now_iso(),
    }


def _json_safe_records(df: Any) -> list[dict[str, Any]]:
    return [{str(k): _json_safe(v) for k, v in row.items()} for row in df.to_dict(orient="records")]
