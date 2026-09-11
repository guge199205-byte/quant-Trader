"""实盘配置文件 JSON 完整性闸门。

动机（2026-09-11 实录）：往 configs/live_symbols.json 的 `_risk_note` 里写了一个
ASCII 双引号，文件变成非法 JSON。**整个系统没有一处会报错**——所有读者都是
fail-open：symbol_policy 退回「只禁 ST、黑名单为空」，risk_list 退回 DEFAULTS。
结果就是 55 只永久黑名单在无声中失效，直到有人手动核对才发现。

fail-open 是运行时必须的设计（数据故障不能停掉全天买入），但**配置本身写坏
必须在下一次下单前被拦住**。本测试就是那道闸门：所有 configs/*.json 必须可解析，
且关键运行时段的结构/取值必须合法。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_config_integrity.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG_FILES = sorted((ROOT / "configs").rglob("*.json"))
SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def test_config_dir_has_files():
    """路径写错（空 glob）会让下面所有断言变成空转——先钉死它不是空的。"""
    assert len(CONFIG_FILES) >= 10


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_config_is_valid_json_object(path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{path.relative_to(ROOT)} 不是合法 JSON：{exc} —— "
                    f"实盘读者会静默 fail-open，黑名单/闸门全部失效")
    assert isinstance(doc, dict), f"{path.name} 顶层必须是对象"


def test_live_symbols_shape():
    """实盘标的边界：黑名单代码格式 + risk 段存在（读不出=静默放行）。"""
    doc = json.loads((ROOT / "configs" / "live_symbols.json").read_text(encoding="utf-8"))
    block = doc.get("block_buy")
    assert isinstance(block, list) and block, "block_buy 缺失/为空 = 永久黑名单整体失效"
    bad = [c for c in block if not SYMBOL_RE.match(str(c))]
    assert not bad, f"黑名单代码格式不合法：{bad}"
    assert len(set(block)) == len(block), "黑名单有重复项"
    assert isinstance(doc.get("risk"), dict) and doc["risk"].get("enabled") is True, \
        "risk 段缺失或未启用 → 事件风险闸门整体失效"
    assert doc.get("allow_st") is False, "allow_st 必须显式 false（ST 默认禁买）"


def test_live_symbols_risk_numeric_keys_are_numbers():
    """数值型判据键必须是数字——写成字符串会被 _num 静默吞掉退回默认值。"""
    risk = json.loads((ROOT / "configs" / "live_symbols.json").read_text(
        encoding="utf-8"))["risk"]
    for key, val in risk.items():
        if key.startswith("_"):
            continue
        assert isinstance(val, (int, float, bool)), f"risk.{key} 应为数字/布尔，实为 {type(val).__name__}"


def test_intraday_exec_switch_is_boolean():
    """盘中执行总闸：必须是显式布尔（字符串 "false" 会被当成真值）。"""
    p = ROOT / "configs" / "intraday_exec.json"
    if not p.exists():
        pytest.skip("本机无该文件（仓库默认关闭态）")
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(doc.get("enabled"), bool), "enabled 必须是 JSON 布尔量"
