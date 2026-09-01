"""比赛配置目录 + 每模型选择（Arena「比赛配置」↔ 分析引擎 的单一事实源）。

比赛配置：每个模型可多选分析配置；选中 N 个配置 → 每次分析按 N 个配置各做一轮。
- Arena 前端：GET/PUT /api/comp-config（backend/api_server.py 读写本模块）
- 分析引擎：scripts/live_hourly_analysis.py（实盘盘中，每配置一轮独立 LLM 分析）、
  prompts/agent_prompt*.py（历史回放，把选中配置注入系统提示词）
配置文件：configs/comp-config.json（api 容器与宿主机 cron 都挂载/可达）
"""

import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "configs" / "comp-config.json"

MODES = [
    {
        "id": "baseline",
        "name": "基线模式",
        "prompt": "按标准流程逐只简评：结合当前盈亏、今日涨跌与趋势给出可执行建议，冷静客观，注意 A股 T+1 与风险控制。",
    },
    {
        "id": "monk",
        "name": "苦行模式",
        "prompt": "精简推理：直接给结论与操作建议，少说废话；同时严格风控：单笔不超过权益 20%、日亏熔断 5%、保留现金、避开黑名单。",
    },
    {
        "id": "awareness",
        "name": "情境感知",
        "prompt": "知己知彼：清楚当前赛况（各 agent 的总收益与当日盈亏排行榜）。知道自己是领先还是落后，据此调整进攻/防守节奏，并把与对手的对比结论写进分析。",
    },
    {
        "id": "leverage",
        "name": "极限杠杆",
        "prompt": "高风险高回报：敢于集中持仓、尽量打满可用现金，基于当前现金余额给出最大买入量的操作建议；只在极度危险时减仓。",
    },
]

DEFAULT = "baseline"  # 未选择时的兜底配置

# 各市场交易规则（注入系统提示词，让同一套模式目录在不同市场措辞正确）
MARKET_RULES: Dict[str, str] = {
    "cn": "注意 A股 T+1（当日买入次日方可卖出）、涨跌停板、单笔不超权益 20%、日亏 5% 熔断。",
    "hk": "注意港股 T+0（当日可买卖）、可做空（需融券额度）、无涨跌停板、最小交易单位 100 股、汇率风险。",
    "us": "注意美股 T+0、可做空、无涨跌停板、最小交易单位 1 股、盘前盘后流动性低。",
}


def load_selection(market: str = "cn") -> Dict[str, List[str]]:
    """读取 {模型名: [配置id, ...]}（按市场分区）。

    文件结构：{"selection": {"cn": {模型: [...]}, "hk": {...}, ...}}。
    兼容旧格式 {"selection": {模型: [...]}}（无 market 分区）→ 视作 cn。
    """
    mk = (market or "cn").lower()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        sel = data.get("selection", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    # 新格式：按市场分区
    if mk in sel and isinstance(sel[mk], dict):
        return {k: v for k, v in sel[mk].items() if isinstance(v, list)}
    # 旧格式兼容：扁平 {模型: [...]} 只在 cn 下回退
    if mk == "cn" and all(isinstance(v, list) for v in sel.values()):
        return dict(sel)
    return {}


def save_selection(market: str, selection: Dict[str, List[str]]) -> None:
    """原子写 selection（按市场分区，保留其他市场配置）。"""
    mk = (market or "cn").lower()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
        if not isinstance(data, dict):
            data = {}
        sel = data.setdefault("selection", {})
        if not isinstance(sel, dict) or any(isinstance(v, list) for v in sel.values() if not isinstance(v, dict)):
            # 旧扁平格式 → 迁移到 cn 分区
            old = {k: v for k, v in sel.items() if isinstance(v, list)} if isinstance(sel, dict) else {}
            sel = {"cn": old} if old else {}
        sel[mk] = selection
        data["selection"] = sel
    except (OSError, json.JSONDecodeError):
        data = {"selection": {mk: selection}}
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def selected_modes(model: str, market: str = "cn") -> List[dict]:
    """模型选中的配置对象列表（按目录顺序）；未选择/全无效 → [基线模式]。"""
    ids = load_selection(market).get(model) or []
    by_id = {m["id"]: m for m in MODES}
    picked = [by_id[i] for i in ids if i in by_id]
    return picked or [by_id[DEFAULT]]


def mode_label(model: str, market: str = "cn") -> str:
    """选中配置的中文名（'基线模式/苦行模式' 拼接），用于日志与落盘标注。"""
    return "/".join(m["name"] for m in selected_modes(model, market))


def market_rules(market: str = "cn") -> str:
    """该市场的交易规则提示词段。"""
    return MARKET_RULES.get((market or "cn").lower(), MARKET_RULES["cn"])


def comp_config_section(model: str, market: str = "cn") -> str:
    """把模型选中的配置翻译成系统提示词追加段（回放路径用：一轮运行内分 N 轮分析）。

    未选择/全无效 → 基线模式单轮，与历史行为等价。
    注入选中市场的交易规则（A股 T+1 / 港股 T+0 可做空 等），让同一套模式在不同市场措辞正确。
    """
    modes = selected_modes(model, market)
    lines = [
        "",
        "【本次分析要求 · 比赛配置】",
        f"你需要按 {len(modes)} 个配置分别完成独立分析（每个配置单独一轮：观察→推理→操作建议），",
        "轮次之间用分隔线标明配置名称，全部完成后再输出最终指令。",
        "",
        f"【市场规则】{market_rules(market)}",
        "",
    ]
    for m in modes:
        lines.append(f"配置《{m['name']}》：{m['prompt']}")
    return "\n".join(lines)
