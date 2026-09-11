"""QMT 桥适配器单测（agent_tools/brokers/qmt_bridge.py，阶段二：下单默认关闭）。

不碰真 Redis / 真 QMT：把 `bigqmt_signal_trader.xtquant_compat` 用假模块注入
sys.modules，走真实的 _ensure() 路径（含 configure 调用与错误脱敏）。

重点钉住四件容易出事的事：
  1. 返回形状必须与 TdxBridgeBroker._account_query 一致（上层 10+ 脚本按形状读）；
  2. available_volume 如实映射 can_use_volume（T+1 卖出闸门的唯一依据）；
  3. 状态码映射不得把"报撤中/部成待撤"当成终态（会导致漏记成交/提前清在途单）；
  4. 下单默认关闭且校验先于 RPC（入参不合法不能把注定被拒的单子发到桥上）。

运行：/home/zbox/quant-Trader/.venv/bin/python -m pytest tests/test_qmt_bridge.py -q
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_tools.brokers.base import BrokerError  # noqa: E402
import agent_tools.brokers.qmt_bridge as Q  # noqa: E402


class _Pos:
    def __init__(self, code, volume, can_use, avg_price=10.0, mv=10000.0, name=""):
        self.stock_code, self.volume, self.can_use_volume = code, volume, can_use
        self.avg_price, self.market_value, self.stock_name = avg_price, mv, name


class _Asset:
    total_asset, cash, market_value, frozen_cash = 100000.0, 20000.0, 80000.0, 0.0


class _Order:
    def __init__(self, oid, code, status, otype=23, price=10.0,
                 traded_volume=0.0, traded_price=0.0, order_volume=100.0):
        self.order_id, self.stock_code, self.order_status = oid, code, status
        self.order_type, self.price = otype, price
        self.traded_volume, self.traded_price = traded_volume, traded_price
        self.order_volume, self.order_time, self.status_msg = order_volume, "", ""


class _FakeTrader:
    def __init__(self, asset=None, positions=None, orders=None, trades=None,
                 ping_error=None, rpc=None):
        self._asset = asset if asset is not None else _Asset()
        self._positions = positions or []
        self._orders = orders or []
        self._trades = trades or []
        self._ping_error = ping_error
        # method -> 该 RPC 的返回体（缺省时 ping 回 pong，其余回 {}）
        self._rpc = dict(rpc or {})
        self.calls = []          # [(method, params), ...]
        self.client = types.SimpleNamespace(call=self._call)
        self.configured = []

    def _call(self, method, params):
        self.calls.append((method, params))
        if self._ping_error:
            raise RuntimeError(self._ping_error)
        if method in self._rpc:
            return self._rpc[method]
        if method == "ping":
            return {"pong": True}
        return {}

    def query_stock_asset(self, account):
        return self._asset

    def query_stock_positions(self, account):
        return self._positions

    def query_stock_orders(self, account, cancelable_only=False):
        return self._orders

    def query_stock_trades(self, account):
        return self._trades


@pytest.fixture
def fake_qmt(monkeypatch, tmp_path):
    """注入假 big-convert 模块 + 隔离 config 覆盖文件；返回 (broker, trader, 配置记录)。"""
    monkeypatch.setattr(Q, "_OVERRIDE_FILE", tmp_path / "qmt_bridge.json")
    for k in ("QMT_EXEC_ACCOUNT_ID", "QMT_EXEC_REDIS_HOST", "BIGQMT_ACCOUNT_ID",
              "BIGQMT_REDIS_HOST"):
        monkeypatch.delenv(k, raising=False)

    def _make(trader=None, **cfg):
        trader = trader or _FakeTrader()
        mod = types.ModuleType("bigqmt_signal_trader.xtquant_compat")
        calls = []

        def configure(**kw):
            calls.append(kw)

        mod.configure = configure
        mod.StockAccount = lambda cid, ctype: types.SimpleNamespace(
            account_id=cid, account_type=ctype)
        mod.xt_trader = trader
        pkg = types.ModuleType("bigqmt_signal_trader")
        monkeypatch.setitem(sys.modules, "bigqmt_signal_trader", pkg)
        monkeypatch.setitem(sys.modules, "bigqmt_signal_trader.xtquant_compat", mod)
        broker = Q.QmtBridgeBroker({"account_id": "123456",
                                    "redis_host": "10.0.0.1",
                                    "redis_password": "pw-secret", **cfg})
        return broker, trader, calls

    return _make


# ---------- 配置 ----------

def test_missing_account_or_redis_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(Q, "_OVERRIDE_FILE", tmp_path / "nope.json")
    monkeypatch.delenv("QMT_EXEC_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("BIGQMT_ACCOUNT_ID", raising=False)

    with pytest.raises(BrokerError, match="未配置资金账号"):
        Q.QmtBridgeBroker({"redis_host": "10.0.0.1"})

    monkeypatch.setenv("QMT_EXEC_ACCOUNT_ID", "123456")
    with pytest.raises(BrokerError, match="未配置 Redis 地址"):
        Q.QmtBridgeBroker({})


def test_config_file_overrides_env_and_params(monkeypatch, tmp_path):
    """优先级：构造参数 > config/qmt_bridge.json > 环境变量。"""
    monkeypatch.setattr(Q, "_OVERRIDE_FILE", tmp_path / "qmt_bridge.json")
    (tmp_path / "qmt_bridge.json").write_text(
        '{"account_id": "from-file", "redis_host": "192.168.31.13", "redis_port": 6381}',
        encoding="utf-8")
    monkeypatch.setenv("QMT_EXEC_ACCOUNT_ID", "from-env")

    b = Q.QmtBridgeBroker()

    assert b.account_id == "from-file" and b.redis["host"] == "192.168.31.13"
    assert b.redis["port"] == 6381 and b.account_type == "STOCK"


def test_configure_failure_redacts_redis_password(fake_qmt):
    """底层库报错会把 Redis URL 连密码一起带出来——不能原样进日志。"""
    trader = _FakeTrader()
    broker, _, calls = fake_qmt(trader, account_id="123456")
    mod = sys.modules["bigqmt_signal_trader.xtquant_compat"]

    def boom(**kw):
        raise RuntimeError("redis://:pw-secret@10.0.0.1:6380 连接失败")

    mod.configure = boom
    with pytest.raises(BrokerError) as ei:
        broker.ping()

    assert "pw-secret" not in str(ei.value) and "***" in str(ei.value)


# ---------- 账户/持仓（形状对齐 TDX） ----------

def test_account_query_shape_matches_tdx(fake_qmt):
    trader = _FakeTrader(positions=[
        _Pos("600309.SH", 500, 500, avg_price=78.2, mv=40000.0, name="万华化学"),
        _Pos("001312.SZ", 0, 0),          # 清仓残留：必须剔除
    ])
    broker, trader, calls = fake_qmt(trader)

    acct = broker._account_query()

    assert acct["asset"]["asset"] == 100000.0      # total_asset
    assert acct["asset"]["cash"] == 20000.0
    assert acct["channel_used"] == "qmt"
    assert [p["stock_code"] for p in acct["positions"]] == ["600309.SH"]
    p = acct["positions"][0]
    assert p["total_volume"] == 500 and p["available_volume"] == 500   # can_use_volume
    assert p["cost_price"] == 78.2 and p["last_price"] == 80.0         # mv/volume
    assert calls and calls[0]["account_id"] == "123456"


def test_get_positions_and_cash_contract(fake_qmt):
    broker, _, _ = fake_qmt(_FakeTrader(positions=[_Pos("600309.SH", 500, 100)]))

    assert broker.get_positions("glm", "2026-09-10") == {"600309.SH": 500.0}
    assert broker.get_cash("glm", "2026-09-10") == 20000.0


def test_ping_and_error_path(fake_qmt):
    broker, trader, _ = fake_qmt()
    assert broker.ping()["ok"] is True

    dead, _, _ = fake_qmt(_FakeTrader(ping_error="Connection refused"))
    with pytest.raises(BrokerError, match="QMT 桥 ping 失败"):
        dead.ping()


# ---------- 委托状态映射 ----------

@pytest.mark.parametrize("code,expected", [
    (48, "submitted"), (51, "submitted"), (52, "partial_fill"),
    (53, "cancelled"), (54, "cancelled"), (55, "partial_fill"),
    (56, "filled"), (57, "rejected"), (255, "submitted"), (999, "submitted"),
])
def test_status_map_matches_live_fills_terminals(fake_qmt, code, expected):
    """51/52/255 绝不能落进 live_fills 的终态集合（会漏记成交/提前清在途单）。"""
    broker, _, _ = fake_qmt(_FakeTrader(orders=[_Order("1", "600309.SH", code)]))

    o = broker.get_orders()[0]

    assert o["status"] == expected
    assert o["status_code"] == code


def test_get_orders_fields_filter_and_side(fake_qmt):
    broker, _, _ = fake_qmt(_FakeTrader(orders=[
        _Order("1", "600309.SH", 56, otype=23, price=78.0,
               traded_volume=500.0, traded_price=78.2, order_volume=500.0),
        _Order("2", "001312.SZ", 54, otype=24),
    ]))

    o = broker.get_orders()[0]
    assert (o["order_id"], o["side"], o["status"]) == ("1", "buy", "filled")
    assert o["filled_volume"] == 500.0 and o["filled_price"] == 78.2
    assert o["order_price"] == 78.0 and o["total_volume"] == 500.0
    assert broker.get_orders(stock_code="001312.SZ")[0]["side"] == "sell"


def test_get_trades_shape(fake_qmt):
    t = types.SimpleNamespace(traded_id="T1", order_id="1", stock_code="600309.SH",
                              order_type=24, traded_volume=500.0, traded_price=80.0,
                              traded_time="093000")
    broker, _, _ = fake_qmt(_FakeTrader(trades=[t]))

    assert broker.get_trades() == [{
        "trade_id": "T1", "order_id": "1", "stock_code": "600309.SH", "side": "sell",
        "filled_volume": 500.0, "filled_price": 80.0, "time": "093000"}]


# ---------- 阶段二：下单接线（默认关闭） ----------

_OPEN_CFG = {"allow_trading": True}
_WIN_OPEN = {"ping": {"pong": True, "allow_order_methods": True}}
_WIN_CLOSED = {"ping": {"pong": True, "allow_order_methods": False}}


def test_trading_is_off_unless_explicitly_enabled(fake_qmt):
    """默认关闭：接线到位 ≠ 能下单。三个写方法一律拒绝，且不碰网络。"""
    broker, trader, _ = fake_qmt()

    assert broker.allow_trading is False
    for call in (lambda: broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0),
                 lambda: broker.sell("glm", "2026-09-10", "600309.SH", 100, 80.0),
                 lambda: broker.cancel_order("600309.SH", "1")):
        with pytest.raises(BrokerError, match="下单未开启"):
            call()
    assert trader.calls == []


def test_allow_trading_reads_config_then_env(monkeypatch, tmp_path):
    monkeypatch.setattr(Q, "_OVERRIDE_FILE", tmp_path / "qmt_bridge.json")
    monkeypatch.delenv("QMT_EXEC_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("QMT_EXEC_ALLOW_TRADING", raising=False)
    (tmp_path / "qmt_bridge.json").write_text(
        '{"account_id": "1", "redis_host": "h", "allow_trading": true}', encoding="utf-8")
    assert Q.QmtBridgeBroker().allow_trading is True

    # 文件没写就是关；环境变量可开
    (tmp_path / "qmt_bridge.json").write_text(
        '{"account_id": "1", "redis_host": "h"}', encoding="utf-8")
    assert Q.QmtBridgeBroker().allow_trading is False
    monkeypatch.setenv("QMT_EXEC_ALLOW_TRADING", "1")
    assert Q.QmtBridgeBroker().allow_trading is True


def test_order_refused_when_windows_switch_is_closed(fake_qmt):
    """本侧开了、Windows 侧没开 → 发单前就拒，不把一个注定被拒的单子发出去。"""
    broker, trader, _ = fake_qmt(_FakeTrader(rpc=_WIN_CLOSED), **_OPEN_CFG)

    with pytest.raises(BrokerError, match="rpc_allow_order_methods"):
        broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)
    assert [m for m, _ in trader.calls] == ["ping"]      # 只有探针，没有 submit_order


def test_buy_sends_submit_order_with_limit_price(fake_qmt):
    trader = _FakeTrader(rpc=dict(_WIN_OPEN, submit_order={
        "status": "SUBMITTED", "user_order_id": "baymax-1", "order_sys_id": "S1",
        "message": ""}))
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)

    out = broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)

    method, params = trader.calls[-1]
    assert method == "submit_order"
    assert params["action"] == "BUY" and params["stock_code"] == "600309.SH"
    assert params["volume"] == 100 and params["price"] == 80.0
    assert params["price_type"] == "LIMIT"
    assert params["account_id"] == "123456"
    assert params["strategy_name"] == "baymax"
    # 回填委托号：桥的委托号是异步分配的，没有 remark 就认不回自己的单
    assert params["remark"] and params["signal_id"]
    assert out["order_sys_id"] == "S1" and out["user_order_id"] == "baymax-1"
    assert out["status"] == "submitted" and out["raw"]["message"] == ""


def test_sell_without_price_uses_latest_price(fake_qmt):
    trader = _FakeTrader(rpc=_WIN_OPEN)
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)

    broker.sell("glm", "2026-09-10", "600309.SH", 100)

    params = trader.calls[-1][1]
    assert params["action"] == "SELL" and params["price"] == 0.0
    assert params["price_type"] == "LATEST_PRICE"


def test_plan_id_goes_into_remark_for_reconciliation(fake_qmt):
    """执行路径传 plan_id（幂等键）时接口与 TdxBridgeBroker 对齐：

    哨兵/整点轮用 broker.sell(..., plan_id=...) 的调用形状不能在这里 TypeError，
    且 plan_id 要进 remark/signal_id —— 委托号是异步分配的，remark 是唯一回填线索。
    哨兵路径 signature 为空，plan_id 让委托从 baymax-x-... 变成可辨识的 watch-... 前缀。
    """
    trader = _FakeTrader(rpc=_WIN_OPEN)
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)

    broker.sell("glm", "2026-09-10", "600309.SH", 100, 15.5,
                plan_id="watch-deepseek-v4-pro-001312.SZ-202609111000")

    remark = trader.calls[-1][1]["remark"]
    assert remark.startswith("baymax-watch-deepseek-v-")     # plan_id 前 16 字符
    assert trader.calls[-1][1]["signal_id"] == remark


def test_plan_id_tag_never_exceeds_16_chars(fake_qmt):
    """tag 恒 ≤16 字符（remark 长度上限未知，不因 plan_id 变长）；无 plan_id 退回 signature。"""
    trader = _FakeTrader(rpc=_WIN_OPEN)
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)
    plan_id = "watch-deepseek-v4-pro-001312.SZ-202609111000"   # 46 字符

    broker.sell("glm", "2026-09-10", "600309.SH", 100, 15.5, plan_id=plan_id)
    with_plan = trader.calls[-1][1]["remark"]

    broker.sell("glm", "2026-09-10", "600309.SH", 100, 15.5)
    without_plan = trader.calls[-1][1]["remark"]

    assert with_plan.startswith(f"baymax-{plan_id[:16]}-")
    assert len(with_plan) - len(plan_id[:16]) == len(without_plan) - len("glm")
    assert without_plan.startswith("baymax-glm-")            # 无 plan_id 时退回 signature


@pytest.mark.parametrize("amount", [0, -100])
def test_non_positive_volume_rejected_without_touching_the_bridge(fake_qmt, amount):
    broker, trader, _ = fake_qmt(_FakeTrader(rpc=_WIN_OPEN), **_OPEN_CFG)

    with pytest.raises(BrokerError, match="数量必须为正"):
        broker.buy("glm", "2026-09-10", "600309.SH", amount, 80.0)
    assert trader.calls == []


def test_negative_price_rejected_without_touching_the_bridge(fake_qmt):
    broker, trader, _ = fake_qmt(_FakeTrader(rpc=_WIN_OPEN), **_OPEN_CFG)

    with pytest.raises(BrokerError, match="价格必须为正"):
        broker.buy("glm", "2026-09-10", "600309.SH", 100, -1.0)
    assert trader.calls == []


def test_cancel_order_passes_sysid_and_user_order_id(fake_qmt):
    trader = _FakeTrader(rpc=dict(_WIN_OPEN, cancel_order={
        "success": True, "message": ""}))
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)

    out = broker.cancel_order("600309.SH", "S1", user_order_id="baymax-1")

    params = trader.calls[-1][1]
    assert trader.calls[-1][0] == "cancel_order"
    assert params["order_sys_id"] == "S1" and params["user_order_id"] == "baymax-1"
    assert params["account_id"] == "123456"
    assert out["ok"] is True


def test_cancel_requires_order_id(fake_qmt):
    broker, trader, _ = fake_qmt(_FakeTrader(rpc=_WIN_OPEN), **_OPEN_CFG)

    with pytest.raises(BrokerError, match="缺少委托号"):
        broker.cancel_order("600309.SH", "")
    assert trader.calls == []


# ---------- submit 结果诚实性（与 TdxBridgeBroker 2026-09-11 口径对齐）----------
# 对端 submit_order 的 data 形状 = OrderSubmitResult{status, user_order_id,
# order_sys_id, message}（redis_rpc.to_jsonable 序列化，见对端 models.py）。
# 客户端 call() 已把 ok=false / server_error（passorder 提交但委托没进系统）
# 转成异常；这里钉的是**异常之外**仍可能出现的三种假成功。

def _submit_says(fake_qmt, data):
    trader = _FakeTrader(rpc=dict(_WIN_OPEN, submit_order=data))
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)
    return broker


def test_submit_rejected_status_raises_instead_of_looking_accepted(fake_qmt):
    """对端回废单（status=57/REJECTED）必须抛错——废单不是成功。

    数值码要过 STATUS_MAP（代码注释承诺的「status 口径同 STATUS_MAP」此前没兑现，
    `str(57).lower()` 会原样吐出 "57"，下游按终态集合比对时谁也认不出来）。
    """
    for raw_status in (57, "57", "REJECTED"):
        broker = _submit_says(fake_qmt, {
            "status": raw_status, "user_order_id": "baymax-1",
            "order_sys_id": "", "message": "可用资金不足"})

        with pytest.raises(BrokerError) as ei:
            broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)
        assert "废单" in str(ei.value) or "rejected" in str(ei.value).lower()
        assert "可用资金不足" in str(ei.value)


def test_submit_numeric_status_maps_through_status_map(fake_qmt):
    """数值状态码按 STATUS_MAP 归一（对端有的版本给码、有的给串）。

    浮点形状也要认（2026-09-11 审查 LOW）：JSON 里 50.0 很常见，只判 isdigit
    会得成字符串 "50.0"，落不进 live_fills 的终态集合。"""
    for raw_status in (50, "50", 50.0, "50.0"):
        broker = _submit_says(fake_qmt, {
            "status": raw_status, "user_order_id": "baymax-1",
            "order_sys_id": "S1", "message": ""})

        assert broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)["status"] \
            == "submitted", f"status={raw_status!r} 没归一"


def test_submit_ok_flag_requires_order_id(fake_qmt):
    """ok 口径 = 可跟踪的受理：无委托号时不得为 True（原为死条件 status!="rejected"）。"""
    with_id = _submit_says(fake_qmt, {
        "status": "SUBMITTED", "user_order_id": "baymax-1",
        "order_sys_id": "S1", "message": ""})
    assert with_id.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)["ok"] is True

    without_id = _submit_says(fake_qmt, {
        "status": "SUBMITTED", "user_order_id": "baymax-1",
        "order_sys_id": None, "message": "passorder submitted"})
    assert without_id.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)["ok"] is False


def test_submit_without_order_id_does_not_claim_acceptance(fake_qmt):
    """回 200 但没回填委托号：单可能已在柜台（对端查单异常时就是这个形状，
    静默降级、无 server_error）。成交跟踪不了 → 必须如实说明，不能写「已受理」，
    也不能让 message 看着像已成功（与 tdx_bridge 2026-09-11 同口径）。"""
    broker = _submit_says(fake_qmt, {
        "status": "SUBMITTED", "user_order_id": "baymax-1",
        "order_sys_id": None, "message": "passorder submitted"})

    out = broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)

    assert out["order_id"] == "" and out["order_sys_id"] == ""
    assert "未返回委托号" in out["message"]
    assert "不要重发" in out["message"]      # 对端无 plan_id 去重，重试=重复下单
    assert "已受理" not in out["message"]


def test_order_rpc_failure_is_redacted(fake_qmt):
    """底层 RPC 报错会把 Redis URL 连密码带出来——不能原样进日志。"""
    trader = _FakeTrader(rpc=_WIN_OPEN)
    broker, _, _ = fake_qmt(trader, **_OPEN_CFG)

    def boom(method, params):
        raise RuntimeError("redis://:pw-secret@10.0.0.1:6380 断开")

    trader.client = types.SimpleNamespace(call=boom)
    with pytest.raises(BrokerError) as ei:
        broker.buy("glm", "2026-09-10", "600309.SH", 100, 80.0)

    assert "pw-secret" not in str(ei.value) and "***" in str(ei.value)


def test_bridge_status_reports_order_switch(fake_qmt):
    broker, _, _ = fake_qmt(_FakeTrader(rpc=_WIN_OPEN))

    assert broker.bridge_status()["allow_order_methods"] is True


def test_quotes_are_not_served_by_qmt_channel(fake_qmt):
    broker, _, _ = fake_qmt()

    with pytest.raises(BrokerError, match="不提供行情"):
        broker.get_quote("600309.SH", "2026-09-10")
    with pytest.raises(BrokerError, match="不提供行情"):
        broker.get_klines("600309.SH", "2026-09-01", "2026-09-10")


def test_registered_in_registry():
    import agent_tools.brokers as B

    assert "qmt" in B.available_brokers()
