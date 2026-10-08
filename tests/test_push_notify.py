"""push_notify / premarket_win_probe 单元测试（全程无真实网络）。

覆盖：QQ 发送报文格式（QQBot 前缀、msg_seq）、令牌缓存、股票名称解析回退、
交易事件消息组装（清仓标记/T+1/余额）、通知失败绝不阻断主流程、盘前探测状态机。
"""
from __future__ import annotations

import json
import types

import pytest

import push_notify as pn
import premarket_win_probe as probe_mod


# ---------- 发送报文格式 ----------


class _FakeResponse:
    def __init__(self, payload=None, status_error=None, status_code=200):
        self._payload = payload or {}
        self._status_error = status_error
        self.status_code = status_code

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == pn.TOKEN_URL:
            return _FakeResponse({"access_token": "FAKE_TOKEN", "expires_in": 7200})
        return _FakeResponse()

    def get(self, url, **kwargs):
        return _FakeResponse()


@pytest.fixture
def fake_requests(monkeypatch):
    fr = _FakeRequests()
    monkeypatch.setitem(__import__("sys").modules, "requests", fr)
    return fr


def test_send_text_uses_qqbot_prefix_and_msg_seq(fake_requests, monkeypatch):
    # Arrange
    monkeypatch.setattr(pn, "_get_token", lambda: "FAKE_TOKEN")
    pn.TOKEN_CACHE.unlink(missing_ok=True)

    # Act
    resp = pn.send_text("测试消息")

    # Assert
    url, kwargs = fake_requests.calls[0]
    assert "api.sgroup.qq.com/v2/users/" in url
    assert kwargs["headers"]["Authorization"].startswith("QQBot ")
    body = kwargs["json"]
    assert body["msg_type"] == 0
    assert body["content"] == "测试消息"
    assert isinstance(body["msg_seq"], int)
    assert resp == {}


def test_token_refreshes_when_cache_expired(fake_requests, monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(pn, "TOKEN_CACHE", tmp_path / "t.json")
    (tmp_path / "t.json").write_text(json.dumps({"token": "OLD", "expires_at": 1}))
    monkeypatch.setenv(pn.SECRET_REF, "S")

    # Act
    tok = pn._get_token()

    # Assert
    assert tok == "FAKE_TOKEN"
    cached = json.loads((tmp_path / "t.json").read_text())
    assert cached["token"] == "FAKE_TOKEN"


def test_get_token_raises_when_secret_missing(monkeypatch):
    # Arrange
    monkeypatch.setenv(pn.SECRET_REF, "")

    # Act & Assert
    with pytest.raises(RuntimeError):
        pn._get_token()


def test_notify_swallows_send_failure(monkeypatch, capsys):
    # Arrange：发送抛异常，推送绝不能影响主流程
    def boom(*a, **k):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(pn, "send_markdown", boom)
    monkeypatch.setattr(pn, "send_text", boom)

    # Act & Assert（不能抛）
    pn.notify("标题", "内容")
    pn.notify_order(side="buy", symbol="600000.SH", volume=100, price=10.0, agent="a")


def test_notify_falls_back_to_plain_text_when_markdown_fails(monkeypatch):
    # Arrange：markdown 被拒时剥掉 ** 降级纯文本重发
    def md_boom(*a, **k):
        raise RuntimeError("markdown 无权限")

    sent = []
    monkeypatch.setattr(pn, "send_markdown", md_boom)
    monkeypatch.setattr(pn, "send_text",
                        lambda content: sent.append(content) or {})

    # Act
    pn.notify("▲ 买入 **火炬电子**", "余额 **¥50,859**\n委托 **248397**")

    # Assert：降级重发，且不再含 ** 符号
    assert len(sent) == 1
    assert "**" not in sent[0]
    assert "▲ 买入 火炬电子" in sent[0]
    assert "余额 ¥50,859" in sent[0]


def test_send_markdown_uses_msg_type_2(fake_requests, monkeypatch):
    # Arrange
    monkeypatch.setattr(pn, "_get_token", lambda: "FAKE_TOKEN")

    # Act
    pn.send_markdown("**加粗** 内容")

    # Assert
    url, kwargs = fake_requests.calls[0]
    assert kwargs["json"]["msg_type"] == 2
    assert kwargs["json"]["markdown"]["content"] == "**加粗** 内容"


# ---------- 股票名称解析 ----------


def test_stock_name_uses_cache_without_mcp(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"600519.SH": "贵州茅台"}))
    called = []

    def dont_call_mcp(symbol):
        called.append(symbol)
        return ""

    monkeypatch.setattr(pn, "_mcp_lookup_name", dont_call_mcp)

    # Act
    name = pn.stock_name("600519.SH")

    # Assert
    assert name == "贵州茅台"
    assert called == []


def test_stock_name_repairs_latin1_mojibake_cache(monkeypatch, tmp_path):
    # Arrange：SSE 响应被 latin-1 误解码后落进缓存的历史坏数据
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    mojibake = "ç¦\x8fæ\x81©è\x82¡ä»½"  # 「福恩股份」的 UTF-8 被按 latin-1 解
    (tmp_path / "names.json").write_text(json.dumps({"001312.SZ": mojibake}))

    # Act
    name = pn.stock_name("001312.SZ")

    # Assert：读缓存时自动修复，不再查 MCP
    assert name == "福恩股份"


def test_repair_name_leaves_foreign_names_alone():
    # Arrange & Act & Assert
    assert pn._repair_name("NVIDIA") == "NVIDIA"
    assert pn._repair_name("贵州茅台") == "贵州茅台"


def test_stock_name_falls_back_when_mcp_fails(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")

    def fail_mcp(symbol):
        raise ConnectionError("quantdb 不可达")

    monkeypatch.setattr(pn, "_mcp_lookup_name", fail_mcp)

    # Act
    name = pn.stock_name("999999.SZ")

    # Assert：拿不到名称必须优雅回退成空串
    assert name == ""


def test_stock_name_caches_mcp_hit(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    monkeypatch.setattr(pn, "_mcp_lookup_name", lambda s: "中国平安")

    # Act
    first = pn.stock_name("601318.SH")
    second = pn.stock_name("601318.SH")

    # Assert
    assert first == second == "中国平安"
    assert "601318.SH" in json.loads((tmp_path / "names.json").read_text())


# ---------- 交易事件消息组装 ----------


def _capture_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(pn, "notify", lambda title, content: calls.append((title, content)))
    return calls


def test_notify_order_buy_message(monkeypatch, tmp_path):
    # Arrange
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"603678.SH": "火炬电子"}))
    monkeypatch.setattr(pn, "_ledger_cash", lambda agent: 50858.8)

    # Act
    pn.notify_order(side="buy", symbol="603678.SH", volume=100, price=50.82,
                    agent="deepseek-v4-flash", reason="军工主线逆势走强",
                    order_id="248397", source="调仓")

    # Assert
    assert len(calls) == 1
    title, content = calls[0]
    assert "▲ 买入 **火炬电子(603678.SH)** **100股 @¥50.82**" == title
    assert "原因：军工主线逆势走强" in content
    assert "委托 **248397** ｜ 余额 **¥50,859**" in content
    assert "T+1：今日买入，明日可卖" in content
    assert "账号：deepseek-v4-flash（调仓）" in content


def test_notify_order_reason_clamped_at_sentence_boundary(monkeypatch, tmp_path):
    # Arrange：超长证据链理由 → 在分句边界收尾，不腰斩半句
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"001312.SZ": "福恩股份"}))
    long_reason = "证据链：" + "平台中轴三线粘合、量能背离持续恶化、" * 20

    # Act
    pn.notify_order(side="sell", symbol="001312.SZ", volume=400, price=17.31,
                    agent="deepseek-v4-pro", reason=long_reason, order_id="300543")

    # Assert：以「、…」收口且长度受控
    _, content = calls[0]
    line = [l for l in content.splitlines() if l.startswith("原因：")][0]
    assert line.endswith("、…")
    assert len(line) <= 300 + 4


def test_notify_order_reason_hard_clamped_without_punctuation(monkeypatch, tmp_path):
    # Arrange：毫无标点的长文才硬截
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"001312.SZ": "福恩股份"}))

    # Act
    pn.notify_order(side="sell", symbol="001312.SZ", volume=400, price=17.31,
                    agent="deepseek-v4-pro", reason="证据链：" + "平" * 400,
                    order_id="300543")

    # Assert
    _, content = calls[0]
    line = [l for l in content.splitlines() if l.startswith("原因：")][0]
    assert len(line) <= 300 + 4
    assert line.endswith("…")


def test_notify_order_short_reason_untouched(monkeypatch, tmp_path):
    # Arrange：短原因原样显示，不加省略号
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"600817.SH": "宇通重工"}))

    # Act
    pn.notify_order(side="sell", symbol="600817.SH", volume=600, price=9.33,
                    agent="deepseek-v4-flash", reason="L2 持续背离，跌破成本，减仓避险",
                    order_id="111693")

    # Assert
    _, content = calls[0]
    assert "原因：L2 持续背离，跌破成本，减仓避险" in content
    assert "…" not in [l for l in content.splitlines() if l.startswith("原因：")][0]


def test_notify_order_full_sell_marks_clearing(monkeypatch, tmp_path):
    # Arrange
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"001312.SZ": "某标的"}))
    monkeypatch.setattr(pn, "_ledger_cash", lambda agent: 69285.8)

    # Act：清仓 = 卖出量 >= 持仓量（held_before 显式给出）
    pn.notify_order(side="sell", symbol="001312.SZ", volume=1100, price=15.26,
                    agent="deepseek-v4-pro", held_before=1100, source="条件位")

    # Assert
    title, content = calls[0]
    assert "▼ **清仓** **某标的(001312.SZ)** **1100股 @¥15.26**" == title
    assert "账号：deepseek-v4-pro（条件位）" in content


def test_notify_order_partial_sell_no_clearing(monkeypatch, tmp_path):
    # Arrange
    calls = _capture_notify(monkeypatch)
    monkeypatch.setattr(pn, "NAME_CACHE", tmp_path / "names.json")
    (tmp_path / "names.json").write_text(json.dumps({"001312.SZ": "某标的"}))

    # Act：减仓 30% = 300/1100（条件位卖出，带止损/止盈展示）
    pn.notify_order(side="sell", symbol="001312.SZ", volume=300, price=15.26,
                    agent="deepseek-v4-pro", held_before=1100, source="条件位",
                    stop_loss=16.0, take_profit=18.5)

    # Assert
    title, content = calls[0]
    assert "▼ 卖出 **某标的(001312.SZ)** **300股 @¥15.26**" == title
    assert "清仓" not in title
    assert "止损 **¥16.00** ｜ 止盈 **¥18.50**" in content


# ---------- 盘前探测状态机 ----------


class _FakeBroker:
    def __init__(self, *, health_error=None, account_error=None, cash=None):
        self.health_error = health_error
        self.account_error = account_error
        self.cash = cash
        self.bridge_url = "http://192.0.2.1:8550"

    def _account_query(self):
        if self.account_error:
            raise self.account_error
        return {"asset": {"cash": self.cash}}


def test_probe_ok_when_health_and_account_ok(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(probe_mod, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setitem(__import__("sys").modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: _FakeResponse(),
                                              post=lambda *a, **k: _FakeResponse()))

    # Act
    res = probe_mod.probe(_FakeBroker(cash=123456.0))

    # Assert
    assert res["ok"] is True
    assert "¥123,456" in res["detail"]


def test_probe_down_when_account_query_fails(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(probe_mod, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setitem(__import__("sys").modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: _FakeResponse()))

    # Act
    res = probe_mod.probe(_FakeBroker(account_error=ConnectionError("桥假活")))

    # Assert
    assert res["ok"] is False
    assert "账户查询 3 次失败" in res["detail"]


def test_probe_down_when_health_unreachable(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(probe_mod, "STATE_FILE", tmp_path / "s.json")

    class _NoRequests:
        def get(self, *a, **k):
            raise ConnectionError("no route to host")

    monkeypatch.setitem(__import__("sys").modules, "requests", _NoRequests())

    # Act
    res = probe_mod.probe(_FakeBroker(health_error=ConnectionError()))

    # Assert
    assert res["ok"] is False
    assert "桥进程不可达" in res["detail"]


def test_main_notifies_once_on_down_then_recovers(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(probe_mod, "STATE_FILE", tmp_path / "s.json")
    sent = []
    monkeypatch.setattr(probe_mod, "notify", lambda t, c: sent.append((t, c)))

    fr = _FakeRequests()
    monkeypatch.setitem(__import__("sys").modules, "requests", fr)

    # Act 1：挂了
    monkeypatch.setattr(probe_mod, "probe",
                        lambda broker=None: {"ok": False, "detail": "桥进程不可达: ConnectionError"})
    rc1 = probe_mod.main()

    # Assert 1
    assert rc1 == 1
    assert len(sent) == 1
    assert "不在线" in sent[0][0]
    assert json.loads((tmp_path / "s.json").read_text())["down"] is True

    # Act 2：恢复了 → 应发恢复通知
    monkeypatch.setattr(probe_mod, "probe",
                        lambda broker=None: {"ok": True, "detail": "账户通道正常"})
    rc2 = probe_mod.main()

    # Assert 2
    assert rc2 == 0
    assert len(sent) == 2
    assert "已恢复" in sent[1][0]

    # Act 3：持续正常 → 不再打扰
    rc3 = probe_mod.main()
    assert rc3 == 0
    assert len(sent) == 2