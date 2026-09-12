"""账户快照退化必须上告警面（2026-09-12 P2）。

2026-09-10 实录：桥假活一整天（行情通道正常、交易账号掉线），`account/query`
恒返 asset=0、positions=[]。哨兵虽已识别并跳过（条件位保住），但提醒只有一行
`print` —— 人翻日志才发现，没有任何告警通道被点亮。本组用例钉住：
`_notify_once` 在**小时内首次**提醒时同时 `record_event`（alert.sh 会读
`data/live_order_events.json` 上报），去重仍按小时，不刷屏。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_watch_notify_alert.py -q
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills as F  # noqa: E402
import live_price_watch as W  # noqa: E402

CN = W.CN_TZ
CODE = "600362.SH"
RULE = {"code": CODE, "stop_loss": 50.0, "pct": 1.0, "reason": "测试"}
NOW = datetime(2026, 9, 12, 10, 5, 0, tzinfo=CN)


def _events(path: Path) -> list:
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def _degenerate(events_path: Path) -> list:
    return [e for e in _events(events_path) if e["kind"] == "account_degenerate"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """哨兵退化分支的外围全桩：状态/事件/委托文件落 tmp，开关打开。"""
    import live_hourly_analysis

    monkeypatch.setattr(W, "SKIP_STATE_FILE", tmp_path / "notify_state.json")
    monkeypatch.setattr(W, "LOG_DIR", tmp_path)
    monkeypatch.setattr(F, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(F, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(F, "reconcile", lambda broker: None)
    monkeypatch.setattr(live_hourly_analysis, "intraday_exec_enabled", lambda: True)
    monkeypatch.setattr(W, "now_cn", lambda: NOW)
    return tmp_path


class _DegenerateBroker:
    """桥假活形态：行情通道可达，但账户资产归零、持仓为空。"""

    def _account_query(self):
        return {"asset": {"asset": 0.0, "cash": 0.0}, "positions": []}


def test_notify_once_records_alert_event_once_per_hour(env):
    events = env / "events.json"

    W._notify_once("account_degenerate", "🚫 账户快照不可信（测试）")
    hits = _degenerate(events)
    assert len(hits) == 1
    assert hits[0]["alert"] is True                      # 必须上告警面
    assert "账户快照不可信" in hits[0]["msg"]

    # 同一小时内重复提醒：边车去重 → 不新增事件（不刷屏）
    W._notify_once("account_degenerate", "🚫 账户快照不可信（测试）")
    assert len(_degenerate(events)) == 1


def test_notify_once_records_new_event_next_hour(env, monkeypatch):
    events = env / "events.json"

    W._notify_once("account_degenerate", "🚫 账户快照不可信（第一小时）")
    monkeypatch.setattr(W, "now_cn", lambda: NOW + timedelta(hours=1))
    W._notify_once("account_degenerate", "🚫 账户快照不可信（第二小时）")

    hits = _degenerate(events)
    assert len(hits) == 2                                # 事件 key 带小时 tag
    assert hits[0]["msg"] != hits[1]["msg"]


def test_run_watch_degenerate_account_alerts_and_keeps_rules(env, monkeypatch):
    """接线：退化分支真跑一轮 → 出事件；条件位一个字节不改。"""
    monkeypatch.setattr(W, "load_watch", lambda: {"agentA": [dict(RULE)]})
    saved = []
    monkeypatch.setattr(W, "save_watch", lambda rules: saved.append(rules))

    counts = W.run_watch(_DegenerateBroker(), now=NOW)

    assert sum(counts.values()) == 0                     # 本轮不动作
    assert saved == []                                   # 规则文件不被回写
    hits = _degenerate(env / "events.json")
    assert len(hits) == 1 and hits[0]["alert"] is True
