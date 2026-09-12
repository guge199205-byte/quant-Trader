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


# ---------- 下单回执文案：受理以委托号为准（2026-09-11 审查 MEDIUM）----------
# 四条下单路径的 print 写死「✅ 已受理」——桥没回委托号时照样打✅，人看日志
# 以为成交在跟踪，实际是账外单。文案必须由委托号决定。

def test_ack_line_marks_accepted_only_with_order_id():
    from live_fills import ack_line

    ok = ack_line("[pro] 卖出 600362.SH", {"order_id": "T1", "status": "submitted"})
    assert ok.startswith("✅") and "已受理" in ok and "T1" in ok

    bare = ack_line("[pro] 卖出 600362.SH", {
        "order_id": "", "status": "unknown", "message": "桥未返回委托号（受理状态未知）"})
    assert "已受理" not in bare and bare.startswith("⚠️")
    assert "桥未返回委托号" in bare


def test_order_paths_have_no_hardcoded_accepted_print():
    """源码级钉子：下单回执不许写死「已受理: {result}」（结果里没有委托号时是假话）。

    HK 也要扫（2026-09-11 审查 LOW）：漏了它，这个钉子会给出「全仓下单回执已统一」
    的假安心，而 HK 路径照样在无委托号时打 ✅。"""
    root = Path(__file__).resolve().parents[1]
    for name in ("live_price_watch", "live_llm_trade", "live_trade_picks",
                 "live_hourly_analysis", "live_hourly_analysis_us",
                 "live_hourly_analysis_hk"):
        text = (root / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert "已受理: {result}" not in text, f"{name} 里还有写死的受理回执"
        assert "已受理: {res}" not in text, f"{name} 里还有写死的受理回执"


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
    """桥没回委托号 → 不消费条件位（"keep"）、不挂 pending、落 untracked 事件。"""
    ok = sentinel._execute_sell(_NoIdBroker(), AGENT, _rule(), 48.89, 49.0,
                                "stop_loss", 100)

    assert ok == "keep"                             # 条件位保留 → 下一分钟同号重试
    assert live_fills.load_pending() == []
    assert "未返回委托号" in capsys.readouterr().out
    (ev,) = json.loads((files / "events.json").read_text(encoding="utf-8")).values()
    assert ev["kind"] == "no_order_id" and ev["alert"] is True


def test_sentinel_places_and_tags_rule_with_order_id(sentinel, files):
    """对照组：有委托号 → 下单并挂 pending 跟踪成交；规则保留并打标（不再消费）。

    2026-09-12 P0-3：旧实现这里 return True = 下单即消费，001312 那笔单随后被
    柜台判废（成交 0/300），条件位已消失 → 持仓当日裸奔。"""
    rule = _rule()
    ok = sentinel._execute_sell(_OkBroker(), AGENT, rule, 48.89, 49.0,
                                "stop_loss", 100)

    assert ok == "placed"
    assert live_fills.load_pending()[0]["order_id"] == "T9"
    assert rule["pending_order_id"] == "T9" and rule["pending_volume"] == 100


# ---------- 回捞不猜：候选不唯一 → 人工（2026-09-11 审查 MEDIUM）----------
# _recover_duplicate 原来按「同价同量优先，否则取最新一笔」回捞。两笔未跟踪单
# 并存时（两次「下单成功但 add_pending 前进程崩」），会把**别人的**成交记进本
# 规则的分账——错账比不接管更难查。改为：精确匹配必须唯一，否则交人工。

class _OrdersBroker:
    def __init__(self, orders):
        self._orders = orders

    def get_orders(self):
        return self._orders


def _order(oid, price, vol, time_):
    return {"order_id": oid, "side": "sell", "stock_code": CODE,
            "order_price": price, "total_volume": vol, "time": time_}


@pytest.fixture
def recover(monkeypatch):
    import live_price_watch as W

    monkeypatch.setattr(W, "_log_line", lambda rec: None)
    return W._recover_duplicate


def test_recover_unique_exact_candidate_is_adopted(recover):
    """常态：同代码卖单只此一笔且同价同量 → 回捞到它。"""
    cand = recover(_OrdersBroker([_order("T7", 15.5, 300, "13:35:09")]),
                   CODE, 300, 15.5)

    assert cand["order_id"] == "T7"


def test_recover_single_loose_candidate_is_adopted(recover):
    """同代码卖单只一笔、价格手数被合规调整过 → 仍接它（量价由调用方取桥真值）。"""
    cand = recover(_OrdersBroker([_order("T8", 15.50, 330, "13:35:09")]),
                   CODE, 300, 15.5)

    assert cand["order_id"] == "T8" and cand["total_volume"] == 330


def test_recover_ambiguous_exact_match_refuses(recover):
    """两笔同价同量（都是候选）→ 不许猜，返回 {} 落 dup_unresolved 人工事件。"""
    cand = recover(_OrdersBroker([_order("T1", 15.5, 300, "13:35:09"),
                                  _order("T2", 15.5, 300, "13:40:00")]),
                   CODE, 300, 15.5)

    assert cand == {}


def test_recover_multiple_none_exact_refuses(recover):
    """多笔同代码卖单、无一精确匹配 → 同样不猜。"""
    cand = recover(_OrdersBroker([_order("T1", 15.30, 200, "13:35:09"),
                                  _order("T2", 16.10, 500, "13:40:00")]),
                   CODE, 300, 15.5)

    assert cand == {}


def test_recover_terminal_unfilled_flagged_not_adopted(recover):
    """唯一候选是**终态未成交**的废单（001312 实录 rejected 0/300）→ 不接管，
    标记 _terminal_unfilled 交给调用方换 plan 号重新布防。

    接管的后果：reconcile 下一轮立刻移除它 → 条件位再次触发 → 桥判重复 →
    回捞同一笔废单……每分钟空转，且同号被桥永久判 duplicate，永远拿不到真单。"""
    o = dict(_order("T9", 15.5, 300, "13:35:09"), status="rejected", filled_volume=0)

    cand = recover(_OrdersBroker([o]), CODE, 300, 15.5)

    assert cand["_terminal_unfilled"] is True
    assert cand["order_id"] == "T9" and cand["wanted"] == 300


def test_recover_terminal_candidate_already_recorded_is_not_flagged(recover, monkeypatch):
    """对照：那笔「终态未成交」其实**已记账**（回捞候选不按 agent/plan 过滤，
    可能是别的规则的单）→ 不许标成废单：调用方会照 0 成交重布防、再卖一笔。

    2026-09-12 审查 LOW：终态早退原先排在 fill_recorded 检查之前。"""
    import live_fills

    monkeypatch.setattr(live_fills, "fill_recorded", lambda oid: True)
    o = dict(_order("T9", 15.5, 300, "13:35:09"), status="rejected", filled_volume=0)

    assert recover(_OrdersBroker([o]), CODE, 300, 15.5) == {}
