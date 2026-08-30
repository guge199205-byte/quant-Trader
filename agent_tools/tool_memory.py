"""交易记忆工具：每市场独立记忆，agent 越用越好用。

记忆文件按市场隔离（backend.yaml markets -> data_dir）：
- US: data/agent_data/market_memory.md
- CN: data/agent_data_astock/market_memory.md

用法（agent persona 指示）：
- 每日开盘/决策前: read_memory() 回顾历史心得
- 每日收盘后: append_memory(section, "今天学到的...") 沉淀经验

文件超过 MEMORY_MAX_LINES（默认 200 行）时自动归档到
market_memory.archive.md 并清空正文，防止无限膨胀。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.general_tools import get_config_value  # noqa: E402

load_dotenv()

mcp = FastMCP("Memory")

# 固定 section，防止记忆文件被写乱
MEMORY_SECTIONS = ["策略心得", "成功案例", "失败教训", "市场观察", "待改进"]
MEMORY_MAX_LINES = int(os.getenv("MEMORY_MAX_LINES", "200"))


def _market_data_dir() -> Path:
    """当前市场的数据目录（从 runtime_env 的 MARKET 定位）。"""
    market = get_config_value("MARKET", "us")
    base = Path(__file__).resolve().parents[1] / "data"
    return base / {"us": "agent_data", "cn": "agent_data_astock", "hk": "agent_data_hk"}.get(market, "agent_data")


def _memory_file() -> Path:
    return _market_data_dir() / "market_memory.md"


def _archive_file() -> Path:
    return _market_data_dir() / "market_memory.archive.md"


def _ensure_memory_file() -> Path:
    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        market = get_config_value("MARKET", "us")
        market_name = {"us": "美股（纳斯达克100）", "cn": "A股（上证50）", "hk": "港股（恒生指数）"}.get(market, market)
        path.write_text(
            f"# 市场记忆 - {market_name}\n\n"
            + "".join(f"## {s}\n\n" for s in MEMORY_SECTIONS),
            encoding="utf-8",
        )
    return path


@mcp.tool()
def read_memory() -> str:
    """读取当前市场的全部记忆（决策前回顾：策略心得/成功案例/失败教训/市场观察/待改进）。"""
    path = _ensure_memory_file()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        return f"读取记忆失败: {e}"


@mcp.tool()
def append_memory(section: str, content: str) -> str:
    """向指定记忆分区追加一条心得（收盘后沉淀经验）。

    Args:
        section: 分区名，必须为：策略心得 / 成功案例 / 失败教训 / 市场观察 / 待改进
        content: 记忆内容，建议包含日期与可复用的结论
    """
    if section not in MEMORY_SECTIONS:
        return (f"分区无效：{section}。可用分区：{'、'.join(MEMORY_SECTIONS)}")

    path = _ensure_memory_file()
    lines = path.read_text(encoding="utf-8").splitlines()

    # 找到分区标题行，在其后插入新条目
    section_idx = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {section}":
            section_idx = i
            break
    if section_idx is None:
        return f"分区不存在：{section}"

    entry = f"- {content.strip()}"
    # 插入到该分区第一行条目之前（标题后）
    insert_at = section_idx + 1
    while insert_at < len(lines) and lines[insert_at].strip().startswith("-"):
        insert_at += 1
    lines.insert(insert_at, entry)

    # 超限归档
    if len(lines) > MEMORY_MAX_LINES:
        archive = _archive_file()
        try:
            existing = archive.read_text(encoding="utf-8") if archive.exists() else ""
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            archive.write_text(
                f"{existing}\n\n## 归档于 {stamp}\n\n" + "\n".join(lines) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        # 归档后重建骨架
        market = get_config_value("MARKET", "us")
        market_name = {"us": "美股（纳斯达克100）", "cn": "A股（上证50）", "hk": "港股（恒生指数）"}.get(market, market)
        lines = (
            [f"# 市场记忆 - {market_name}", ""]
            + [line for s in MEMORY_SECTIONS for line in ["## " + s, ""]]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"已写入记忆 [{section}]：{content[:60]}{'...' if len(content) > 60 else ''}"


@mcp.tool()
def list_memory_sections() -> str:
    """列出记忆分区及当前条目数（用于了解记忆规模）。"""
    path = _ensure_memory_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"读取记忆失败: {e}"
    counts = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            current = line[3:]
            counts.setdefault(current, 0)
        elif line.startswith("- ") and current:
            counts[current] = counts.get(current, 0) + 1
    total_lines = len(text.splitlines())
    return (
        f"记忆文件: {path.name}（{total_lines} 行）\n"
        + "\n".join(f"- {s}: {counts.get(s, 0)} 条" for s in MEMORY_SECTIONS)
    )


if __name__ == "__main__":
    port = int(os.getenv("MEMORY_HTTP_PORT", "8104"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
