"""受理确认面：桥没回委托号时，任何路径都不许当成成功。

2026-09-11 盘点（order-path 清单 ①1，逐行核过）：

  - `tdx_bridge._place_order` 对 `orders=[]` / 缺 order_id 的桥响应不抛错，
    message 还默认写「TDX 已受理」；
  - 哨兵随即打印「✅ 已受理」并**消费条件位**（return True）；
  - `live_fills.add_pending` 对空 order_id 直接 return（静默）。

合起来：单可能已在柜台、系统却零跟踪、零事件、零告警，条件位还被吃掉——
分账账本继续当持仓在，之后所有额度/闸门/告警都建立在假账上。

修法：受理确认以 **order_id** 为准——
  1. 桥返回如实标注（无委托号不得写「已受理」）；
  2. add_pending 空号不再是静默 no-op，落一条 untracked_order 事件（alert.sh 上报）；
  3. 哨兵拿不到委托号 → 不消费条件位，下一分钟用同一 plan_id 重试；
     若那笔其实已受理，桥回 duplicate → 走既有的 _recover_duplicate 回捞委托号，
     两条路都收敛（不重复卖、也不丢账）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_order_ack.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills  # noqa: E402

CN = live_fills.CN_TZ
NOW = datetime(2026, 9, 11, 10, 30, 0, tzinfo=CN)
CODE = "600362.SH"
AGENT = "agentA"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """在途单/事件文件隔离到 tmp（真实 data/ 是生产告警面，测试不得触碰）。"""
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "events.json")
    return tmp_path


# ---------- 桥返回面 ----------

@pytest.fixture
def bridge(monkeypatch):
    """TdxBridgeBroker 实例桩：只换 _post，走真实的 _place_order 解析段。"""
    import agent_tools.risk as risk
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    class _Policy:
        approval_required = False

    monkeypatch.setattr(risk.RiskPolicy, "from_backend_config",
                        classmethod(lambda cls: _Policy()))
    b = object.__new__(TdxBridgeBroker)
    b.account = ""
    b.account_type = "stock"
    return b


def test_place_order_without_order_id_never_claims_accepted(bridge):
    """桥回 orders=[]：order_id 空，message 不得写「已受理」（原来是这么写的）。"""
    bridge._post = lambda *a, **kw: {"orders": []}

    out = bridge.sell(None, None, CODE, 100)

    assert out["order_id"] == ""
    assert "已受理" not in out["message"]
    assert "未返回委托号" in out["message"]


def test_place_order_with_order_id_keeps_message(bridge):
    """对照组：正常返回原样透传（message 优先取桥的）。"""
    bridge._post = lambda *a, **kw: {"orders": [
        {"order_id": "123", "status": "submitted", "message": "提交交易成功！"}]}

    out = bridge.sell(None, None, CODE, 100)

    assert out["order_id"] == "123" and out["message"] == "提交交易成功！"


def test_place_order_rejected_still_raises(bridge):
    """拒单仍抛错（不能被「无委托号」改动放松）。"""
    from agent_tools.brokers.base import BrokerError

    bridge._post = lambda *a, **kw: {"orders": [
        {"status": "rejected", "message": "资金不足"}]}

    with pytest.raises(BrokerError, match="资金不足"):
        bridge.sell(None, None, CODE, 100)


# ---------- 记账面：空委托号不再是静默 no-op ----------

def test_add_pending_without_order_id_alerts(files):
    live_fills.add_pending("", AGENT, CODE, "sell", 100, 10.0, NOW.isoformat())

    assert live_fills.load_pending() == []          # 不写假在途单
    (ev,) = json.loads((files / "events.json").read_text(encoding="utf-8")).values()
    assert ev["kind"] == "untracked_order" and ev["code"] == CODE
    assert ev["side"] == "sell" and ev["alert"] is True


def test_add_pending_with_order_id_stays_quiet(files):
    """对照组：有委托号 → 正常挂 pending，不产生事件。"""
    live_fills.add_pending("T1", AGENT, CODE, "sell", 100, 10.0, NOW.isoformat())

    assert live_fills.load_pending()[0]["order_id"] == "T1"
    assert not (files / "events.json").exists()


# ---------- 哨兵面：拿不到委托号 → 条件位保留 ----------

@pytest.fixture
def sentinel(files, monkeypatch):
    import live_price_watch as W

    monkeypatch.setattr(W, "_log_line", lambda rec: None)
    return W


class _NoIdBroker:
    def sell(self, *a, **kw):
        return {"order_id": "", "status": "unknown",
                "message": "桥未返回委托号（受理状态未知）"}


class _OkBroker:
    def sell(self, *a, **kw):
        return {"order_id": "T9", "status": "submitted", "message": "提交交易成功！"}


def _rule():
    return {"code": CODE, "stop_loss": 50.0, "pct": 1.0,
            "created_ts": "2026-09-11 10:00:00", "reason": "测试"}


def test_sentinel_keeps_rule_when_no_order_id(sentinel, files, capsys):
    """桥没回委托号 → 不消费条件位（False）、不挂 pending、落 untracked 事件。"""
    ok = sentinel._execute_sell(_NoIdBroker(), AGENT, _rule(), 48.89, 49.0,
                                "stop_loss", 100)

    assert ok is False                              # 条件位保留 → 下一分钟同号重试
    assert live_fills.load_pending() == []
    assert "未返回委托号" in capsys.readouterr().out
    (ev,) = json.loads((files / "events.json").read_text(encoding="utf-8")).values()
    assert ev["kind"] == "no_order_id" and ev["alert"] is True


def test_sentinel_consumes_rule_with_order_id(sentinel, files):
    """对照组：有委托号 → 照常消费条件位并挂 pending 跟踪成交。"""
    ok = sentinel._execute_sell(_OkBroker(), AGENT, _rule(), 48.89, 49.0,
                                "stop_loss", 100)

    assert ok is True
    assert live_fills.load_pending()[0]["order_id"] == "T9"
