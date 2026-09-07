"""桥 IP 自动发现单测：健康判定 / 探针顺序 / 局网扫描 / 冷却 / 覆盖持久化 / broker 重试。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_bridge_discovery.py -q
"""
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_tools"))

import agent_tools.brokers.bridge_discovery as D  # noqa: E402
import agent_tools.brokers.tdx_bridge as TB  # noqa: E402

ENV = "http://192.168.31.13:8550"
OVR = "http://192.168.31.50:8550"
DEAD = "http://192.168.31.254:8550"


@pytest.fixture
def files(tmp_path):
    return {
        "override": tmp_path / "tdx_bridge.json",
        "state": tmp_path / "scan.json",
        "lock": tmp_path / "scan.lock",
    }


class FakeProbe:
    """按 URL 存活表返回探针结果，记录每次探测。"""

    def __init__(self, alive):
        self.alive = set(alive)
        self.calls = []

    def __call__(self, url, timeout=1.0):
        self.calls.append(url)
        return url in self.alive


# ---------- 健康探针判据 ----------

def test_probe_accepts_bridge_signature(monkeypatch):
    body = {"status": "ok", "tdx_connected": True, "server_time": "x"}

    class Resp:
        status_code = 200

        def json(self):
            return body

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert D.probe_url(ENV) == body


def test_probe_rejects_non_bridge(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"title": "路由器管理页"}  # 普通设备不会返回 status=ok

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert D.probe_url(ENV) is None


@pytest.mark.parametrize("exc", [
    requests.exceptions.ConnectionError("no route"),
    requests.exceptions.ReadTimeout("slow"),
])
def test_probe_network_error_returns_none(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(requests, "get", boom)
    assert D.probe_url(ENV) is None


def test_probe_http_error_returns_none(monkeypatch):
    class Resp:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert D.probe_url(ENV) is None


# ---------- 扫描候选集 ----------

def test_subnet_hosts_shape():
    hosts = D.subnet_hosts("192.168.31.13")
    assert len(hosts) == 253  # .1–.254 减去已知地址自身（刚失败过，不再探）
    assert "192.168.31.0" not in hosts and "192.168.31.255" not in hosts
    assert "192.168.31.13" not in hosts  # 已知死地址自身不入候选
    assert all(h.startswith("192.168.31.") for h in hosts)


def test_subnet_hosts_exclude_and_distance_order():
    hosts = D.subnet_hosts("192.168.31.13", exclude={"192.168.31.14"})
    assert len(hosts) == 252 and "192.168.31.14" not in hosts
    # 数值距离近的先探（DHCP 漂移常见近邻）
    assert hosts[0] == "192.168.31.12"


def test_subnet_hosts_reject_non_ip():
    assert D.subnet_hosts("bridge.local") == []


# ---------- 候选解析顺序：env → override → 扫描 ----------

def test_resolve_env_first_when_alive(files):
    probe = FakeProbe({ENV})
    scanner = lambda *a, **k: pytest.fail("env 活着不应扫网")  # noqa: E731
    got = D.resolve_bridge(ENV, ENV, probe=probe, scanner=scanner,
                           override_file=files["override"],
                           scan_state_file=files["state"],
                           lock_file=files["lock"])
    assert got == ENV
    assert probe.calls[0] == ENV
    assert not files["override"].exists()  # 地址没变不落盘


def test_resolve_override_when_env_dead(files):
    files["override"].write_text(json.dumps(
        {"bridge_url": OVR, "bridge_token": "tok-123"}), encoding="utf-8")
    probe = FakeProbe({OVR})
    scanner = lambda *a, **k: pytest.fail("override 活着不应扫网")  # noqa: E731
    got = D.resolve_bridge(ENV, DEAD, probe=probe, scanner=scanner,
                           override_file=files["override"],
                           scan_state_file=files["state"],
                           lock_file=files["lock"])
    assert got == OVR
    # 未变动的覆盖文件原样保留（含 UI 写的 token）
    cur = json.loads(files["override"].read_text(encoding="utf-8"))
    assert cur["bridge_url"] == OVR and cur["bridge_token"] == "tok-123"


def test_resolve_env_recovery_heals_stale_override(files):
    """env 静态 IP 恢复后：即使 override 还指向旧地址，也以 env 为准并回写。"""
    files["override"].write_text(json.dumps({"bridge_url": OVR}), encoding="utf-8")
    probe = FakeProbe({ENV})
    got = D.resolve_bridge(ENV, OVR, probe=probe,
                           scanner=lambda *a, **k: None,  # noqa: E731
                           override_file=files["override"],
                           scan_state_file=files["state"],
                           lock_file=files["lock"])
    assert got == ENV
    cur = json.loads(files["override"].read_text(encoding="utf-8"))
    assert cur["bridge_url"] == ENV


def test_resolve_scan_and_persist(files):
    """env/override 全死 → 扫网找到桥 → 落盘 override（全链路收敛）。"""
    files["override"].write_text(json.dumps({"bridge_token": "tok-9"}), encoding="utf-8")
    probe = FakeProbe(set())
    scanner = lambda host, port, **k: "192.168.31.77"  # noqa: E731
    got = D.resolve_bridge(ENV, DEAD, probe=probe, scanner=scanner,
                           override_file=files["override"],
                           scan_state_file=files["state"],
                           lock_file=files["lock"])
    assert got == "http://192.168.31.77:8550"
    cur = json.loads(files["override"].read_text(encoding="utf-8"))
    assert cur["bridge_url"] == got and cur["bridge_token"] == "tok-9"
    assert cur.get("bridge_url_source") == "lan-scan"


def test_resolve_scan_miss_no_override_write(files):
    probe = FakeProbe(set())
    scanner = lambda host, port, **k: None  # noqa: E731
    got = D.resolve_bridge(ENV, DEAD, probe=probe, scanner=scanner,
                           override_file=files["override"],
                           scan_state_file=files["state"],
                           lock_file=files["lock"])
    assert got is None
    assert not files["override"].exists()
    st = json.loads(files["state"].read_text(encoding="utf-8"))
    assert st["winner"] is None


def test_resolve_scan_cooldown(files):
    """冷却期内（<180s）不再重复扫网：连续失败不会每分钟全量扫。"""
    probe = FakeProbe(set())
    scans = []

    def scanner(host, port, **k):
        scans.append(host)
        return None

    D.resolve_bridge(ENV, DEAD, probe=probe, scanner=scanner,
                     override_file=files["override"],
                     scan_state_file=files["state"], lock_file=files["lock"])
    got = D.resolve_bridge(ENV, DEAD, probe=probe, scanner=scanner,
                           override_file=files["override"],
                           scan_state_file=files["state"], lock_file=files["lock"])
    assert got is None
    assert len(scans) == 1  # 第二次被冷却拦下
    # 冷却只拦扫网，不拦已知候选快探
    assert len(probe.calls) >= 4


# ---------- broker 断线重试（幂等读重试一次 / 写单不重试） ----------

class _Resp:
    def __init__(self, data=None, exc=None):
        self._data = data if data is not None else {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._data


def _broker(url=DEAD):
    return TB.TdxBridgeBroker(config={"bridge_url": url})


def test_broker_account_retries_once_after_discovery(monkeypatch):
    broker = _broker()
    broker._discover_or_none = lambda: OVR  # 模拟发现成功
    posts = []

    def fake_post(url, **kw):
        posts.append(url)
        if len(posts) == 1:
            raise requests.exceptions.ConnectionError("no route to host")
        return _Resp({"asset": {"asset": 100.0}})

    monkeypatch.setattr(requests, "post", fake_post)
    out = broker._account_query()
    assert out["asset"]["asset"] == 100.0
    assert posts == [f"{DEAD}/api/v1/account/query", f"{OVR}/api/v1/account/query"]
    assert broker.bridge_url == OVR  # 实例内收敛，本进程后续请求直达新址


def test_broker_no_retry_when_discovery_fails(monkeypatch):
    broker = _broker()
    broker._discover_or_none = lambda: None  # 扫网无果
    posts = []

    def fake_post(url, **kw):
        posts.append(url)
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(TB.BrokerError, match="TDX 桥账户查询失败"):
        broker._account_query()
    assert len(posts) == 1


def test_broker_no_auto_retry_for_order_write(monkeypatch):
    """下单/撤单非幂等：超时≠失败，绝不自动重试（防重复下单）。"""
    broker = _broker()
    broker._discover_or_none = lambda: OVR
    posts = []

    def fake_post(url, **kw):
        posts.append(url)
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(TB.BrokerError, match="TDX 桥撤单失败"):
        broker.cancel_order("600309.SH", "o-1")
    assert len(posts) == 1


def test_broker_http_error_skips_discovery(monkeypatch):
    """应用层错误（4xx/5xx）说明 URL 通、服务端问题——不做 IP 发现。"""
    broker = _broker()
    discovered = []

    def fake_discover():
        discovered.append(1)
        return OVR

    broker._discover_or_none = fake_discover
    exc = requests.exceptions.HTTPError("401 Unauthorized")

    def fake_post(url, **kw):
        return _Resp(exc=exc)

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(TB.BrokerError, match="TDX 桥账户查询失败"):
        broker._account_query()
    assert not discovered
