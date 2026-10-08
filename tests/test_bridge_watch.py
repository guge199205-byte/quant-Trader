"""bridge_watch（盘中桥哨兵）单测：状态机全 mock，无网络无真实探测。

覆盖：断线首推、持续断不轰炸、30 分钟重提醒、恢复通知（含断开时长）、
非交易时段跳过、正常态静默。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import bridge_watch as bw

CN = ZoneInfo("Asia/Shanghai")
TRADING_NOW = datetime(2026, 9, 15, 10, 30, tzinfo=CN)     # 周二盘中
WEEKEND_NOW = datetime(2026, 9, 19, 10, 30, tzinfo=CN)     # 周六


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(bw, "STATE_FILE", tmp_path / "probe.json")
    sent = []
    monkeypatch.setattr(bw, "notify", lambda t, c: sent.append((t, c)))
    return sent


def _probe_down(monkeypatch, detail="账户查询 3 次失败: Read timed out"):
    monkeypatch.setattr(bw, "probe", lambda broker=None: {"ok": False, "detail": detail})


def _probe_up(monkeypatch, detail="账户通道正常"):
    monkeypatch.setattr(bw, "probe", lambda broker=None: {"ok": True, "detail": detail})


def test_first_down_alert_fires_once(monkeypatch, tmp_path):
    # Arrange
    sent = _setup(monkeypatch, tmp_path)
    _probe_down(monkeypatch)

    # Act：连续两次探测都是断
    assert bw.run_once(TRADING_NOW) == "down"
    assert bw.run_once(TRADING_NOW) == "down"

    # Assert：只推一次（状态去重）
    assert len(sent) == 1
    assert "断线" in sent[0][0]
    state = json.loads((tmp_path / "probe.json").read_text())
    assert state["down"] is True and state["down_ts"]


def test_state_write_failure_preserves_old_state(monkeypatch, tmp_path):
    """原子写：状态写失败不把已有状态截成 0 字节（2026-09-21 ENOSPC 实录——共享
    premarket_probe_state.json 被非原子写截成 0 字节，桥哨兵状态静默丢失）。"""
    # Arrange：已有旧状态；让原子写的临时文件无法创建（同名目录占位）
    sent = _setup(monkeypatch, tmp_path)
    _probe_down(monkeypatch)
    p = tmp_path / "probe.json"
    p.write_text(json.dumps({"down": False, "ts": "old"}))
    (tmp_path / "probe.json.tmp").mkdir()

    # Act
    assert bw.run_once(TRADING_NOW) == "down"

    # Assert：推送照发；旧状态原样保留（未被截成 0 字节/半截 JSON）
    assert len(sent) == 1
    assert json.loads(p.read_text()) == {"down": False, "ts": "old"}


def test_realert_after_30_minutes(monkeypatch, tmp_path):
    # Arrange：首断 → 手动把 last_alert 拨回 31 分钟前
    sent = _setup(monkeypatch, tmp_path)
    _probe_down(monkeypatch)
    bw.run_once(TRADING_NOW)
    state = json.loads((tmp_path / "probe.json").read_text())
    state["last_alert_ts"] = time.time() - 31 * 60
    (tmp_path / "probe.json").write_text(json.dumps(state))

    # Act
    assert bw.run_once(TRADING_NOW) == "realert"

    # Assert：重提醒一次，且再次重置计时（再跑不推）
    assert len(sent) == 2
    assert "仍离线" in sent[1][0]
    assert bw.run_once(TRADING_NOW) == "down"
    assert len(sent) == 2


def test_recovery_notifies_with_downtime(monkeypatch, tmp_path):
    # Arrange：先断，再恢复
    sent = _setup(monkeypatch, tmp_path)
    _probe_down(monkeypatch)
    bw.run_once(TRADING_NOW)
    _probe_up(monkeypatch)

    # Act
    assert bw.run_once(TRADING_NOW) == "recovered"

    # Assert
    assert len(sent) == 2
    assert "已恢复" in sent[1][0]
    state = json.loads((tmp_path / "probe.json").read_text())
    assert state["down"] is False


def test_recovery_after_healthy_is_silent(monkeypatch, tmp_path):
    # Arrange：一直正常 → 不推任何消息
    sent = _setup(monkeypatch, tmp_path)
    _probe_up(monkeypatch)

    # Act & Assert
    assert bw.run_once(TRADING_NOW) == "ok"
    assert sent == []


def test_skips_outside_market_hours_without_probing(monkeypatch, tmp_path):
    # Arrange：周末不应触发探测（probe 被调用即失败）
    sent = _setup(monkeypatch, tmp_path)

    def _must_not_probe(broker=None):
        raise AssertionError("非交易时段不应探测")

    monkeypatch.setattr(bw, "probe", _must_not_probe)

    # Act & Assert
    assert bw.run_once(WEEKEND_NOW) == "skip"
    assert sent == []


def test_force_bypasses_hours_gate(monkeypatch, tmp_path):
    # Arrange
    sent = _setup(monkeypatch, tmp_path)
    _probe_up(monkeypatch)

    # Act & Assert：--force（手动调试）无视时段
    assert bw.run_once(WEEKEND_NOW, force=True) == "ok"