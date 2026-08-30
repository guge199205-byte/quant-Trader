"""交易记忆工具测试（隔离临时目录）。"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_RUNTIME = Path(__file__).resolve().parents[1] / "runtime_env_test.json"
os.environ["RUNTIME_ENV_PATH"] = str(TEST_RUNTIME)

from tools.general_tools import write_config_value  # noqa: E402
import agent_tools.tool_memory as mem  # noqa: E402


def _fn(tool):
    """fastmcp 装饰后的工具是 FunctionTool，取原始函数。"""
    return getattr(tool, "fn", tool)


read_memory = _fn(mem.read_memory)
append_memory = _fn(mem.append_memory)
list_memory_sections = _fn(mem.list_memory_sections)


@pytest.fixture(autouse=True)
def clean_memory(tmp_path, monkeypatch):
    """把记忆目录重定向到临时目录，隔离真实记忆。"""
    write_config_value("MARKET", "us")
    fake_dir = tmp_path / "agent_data"
    fake_dir.mkdir()
    monkeypatch.setattr(mem, "_market_data_dir", lambda: fake_dir)
    yield
    if TEST_RUNTIME.exists():
        TEST_RUNTIME.unlink()


class TestMemory:
    def test_read_creates_skeleton(self):
        content = read_memory()
        assert "市场记忆" in content
        for section in mem.MEMORY_SECTIONS:
            assert f"## {section}" in content

    def test_append_and_read(self):
        append_memory("策略心得", "2025-10-30 测试心得")
        content = read_memory()
        assert "测试心得" in content

    def test_invalid_section_rejected(self):
        result = append_memory("乱写", "x")
        assert "分区无效" in result

    def test_list_sections(self):
        append_memory("失败教训", "教训A")
        listing = list_memory_sections()
        assert "失败教训: 1 条" in listing

    def test_market_isolation(self, tmp_path, monkeypatch):
        # cn 市场写记忆 → 独立文件
        write_config_value("MARKET", "cn")
        fake_cn = tmp_path / "agent_data_astock"
        fake_cn.mkdir()
        monkeypatch.setattr(mem, "_market_data_dir", lambda: fake_cn)
        append_memory("市场观察", "CN观察")
        assert "CN观察" in read_memory()
