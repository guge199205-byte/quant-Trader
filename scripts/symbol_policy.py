#!/usr/bin/env python3
"""标的边界（权限白名单）：实盘买入前的**标的级**硬约束。

为什么需要（2026-09-08）：候选池只约束"新开仓买什么"，而池子是研究链路的
产物，不构成安全边界——池里出现 ST/*ST/退市整理股时，买入路径此前照单全收
（`ashare_rules` 只用 ST 算涨跌停幅度，没有任何禁买）。`agent_tools/risk.py`
有 blacklist，但只作用于 MCP 路径，实盘循环读不到。本模块把"哪些标的
**绝对不许买**"从提示词挪进代码：模型说什么都不放行。

边界：
- **只拦买入**。卖出永远放行——否则被套的仓位出不来，这是比"买错"更糟的故障。
- 名称取不到 → **放行**（fail-open）。宁可漏拦一只，也不能因为名称表拉不到
  就把当天所有买入停掉（同 live_breaker 的取舍）。代码黑名单不依赖名称，
  任何时候都生效。
- 默认禁 ST（`allow_st=False`）：调用方忘读配置也不会丢掉这条底线。
"""
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "configs" / "live_symbols.json"

# 风险警示股名称前缀（含未股改 SST/S*ST 的历史写法）
_ST_PREFIXES = ("ST", "*ST", "SST", "S*ST")
# 退市整理期股票名称含"退"（如"退市海润"/"海润退"）；中文名里"退"字基本只出现在这类
_DELIST_MARK = "退"


@dataclass(frozen=True)
class SymbolPolicy:
    """标的边界配置。

    block_buy：操作员黑名单（代码精确匹配，大小写不敏感），随时可改
    `configs/live_symbols.json` 生效，无需改代码。
    allow_st：显式放行 ST（默认否）。
    """

    block_buy: frozenset = frozenset()
    allow_st: bool = False


def is_risky_name(name) -> bool:
    """风险警示/退市整理股识别。name 为空 → False（见模块 fail-open 说明）。"""
    n = str(name or "").strip().upper().replace(" ", "")
    if not n:
        return False
    return n.startswith(_ST_PREFIXES) or _DELIST_MARK in n


def check_symbol(code: str, name, policy: SymbolPolicy) -> str:
    """标的边界判定：放行返回 ""，否则返回拒绝原因（可直接进 BuyDecision.reason）。"""
    c = str(code or "").strip().upper()
    if c in policy.block_buy:
        return f"{code} 在操作员黑名单（configs/live_symbols.json）"
    if not policy.allow_st and is_risky_name(name):
        return f"{code} {name} 属 ST/*ST/退市整理股（默认禁买）"
    return ""


def load_policy(path: Path | None = None) -> SymbolPolicy:
    """读 configs/live_symbols.json；缺失/损坏 → 默认策略（仍禁 ST，仅黑名单为空）。

    文件形态：{"block_buy": ["600666.SH"], "allow_st": false}
    黑名单条目须带交易所后缀（"600666.SH"），且只接受字符串——JSON 里的
    null/数字会被忽略，不生成永远匹配不上的垃圾条目。
    """
    try:
        doc = json.loads((path or CONF).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SymbolPolicy()
    if not isinstance(doc, dict):
        return SymbolPolicy()
    raw = doc.get("block_buy")
    codes = ({str(c).strip().upper() for c in raw
              if isinstance(c, str) and c.strip()}
             if isinstance(raw, list) else set())
    return SymbolPolicy(block_buy=frozenset(codes), allow_st=bool(doc.get("allow_st")))
