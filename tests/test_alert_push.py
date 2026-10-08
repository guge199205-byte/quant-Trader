"""alert_push（alert.sh → QQ 转发器）单元测试：全程无真实网络。

覆盖：指纹归一（数字/时间戳变化不产生新指纹）、TTL 去重、过期清理、
首次必发/指纹变化必发/发送失败不阻断。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import alert_push as ap  # noqa: E402
import push_notify as pn  # noqa: E402


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    f = tmp_path / "alert_push_state.json"
    monkeypatch.setattr(ap, "STATE_FILE", f)
    return f


@pytest.fixture
def capture(monkeypatch):
    sent = []
    monkeypatch.setattr(pn, "notify", lambda title, body: sent.append((title, body)))
    return sent


def test_fingerprint_ignores_numbers_and_whitespace():
    a = "🟡 新闻分子停更 1144 分钟（盘中应每小时一期）"
    b = "🟡 新闻分子停更   1184   分钟（盘中应每小时一期）"
    assert ap.fingerprint(a) == ap.fingerprint(b)


def test_fingerprint_changes_when_content_changes():
    a = "🔴 委托终态 rejected：600176.SH sell 成交 0/200 股"
    b = "🔴 账户通道掉线：行情能看但账户查询哑火"
    assert ap.fingerprint(a) != ap.fingerprint(b)


def test_should_push_first_time_and_ttl():
    now = 1_000_000.0
    assert ap.should_push({}, "fp1", now) is True
    assert ap.should_push({"fp1": now - 10}, "fp1", now) is False          # TTL 内
    assert ap.should_push({"fp1": now - ap.TTL_SEC - 1}, "fp1", now) is True  # 过期重发


def test_prune_drops_old_entries():
    now = 1_000_000.0
    state = {"old": now - 25 * 3600, "new": now - 60}
    assert set(ap.prune(state, now)) == {"new"}


def test_main_sends_then_dedups_then_resends_on_change(state_file, capture, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["alert_push.py", "[Trade Agent] 🔴 卖单被拒 600176 0/200"])
    assert ap.main() == 0
    assert len(capture) == 1 and capture[0][0] == "🔔 交易告警"

    # 同一持续故障（分钟数变化）→ 指纹不变 → 跳过
    monkeypatch.setattr(sys, "argv", ["alert_push.py", "[Trade Agent] 🔴 卖单被拒 600176 5/200"])
    assert ap.main() == 0
    assert len(capture) == 1

    # 新问题 → 立即发
    monkeypatch.setattr(sys, "argv", ["alert_push.py", "[Trade Agent] 🔴 账户通道掉线"])
    assert ap.main() == 0
    assert len(capture) == 2
    assert json.loads(state_file.read_text(encoding="utf-8"))  # 指纹已落盘


def test_main_send_failure_does_not_record_and_does_not_raise(state_file, monkeypatch):
    def boom(title, body):
        raise RuntimeError("QQ 挂了")

    monkeypatch.setattr(pn, "notify", boom)
    monkeypatch.setattr(sys, "argv", ["alert_push.py", "🔴 测试"])
    assert ap.main() == 0                       # 绝不阻断 alert.sh
    assert not state_file.exists()              # 没发出去 → 不写指纹（下次重试）


def test_main_empty_message_noop(state_file, capture, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["alert_push.py", "   "])
    assert ap.main() == 0
    assert capture == []
