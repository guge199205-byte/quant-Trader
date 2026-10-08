"""AiData 闸门契约（P0-1）。

事故链（2026-09-02→09-22 实测）：`_load()` 成功（/opt/tdx-aidata 三件套齐备）
→ `available()` 恒 True，但一次真实 `get_klines("600519.SH", interval="5m")`
**60 秒不返回**（通道级故障，.so 内部自行重连并向 stdout 打「[错误码 11] 连接失败」）。
2026-09-21 的止血（.env 置 LIVE_QUOTES_DISABLE_AIDATA=1）只被 live_quotes 解析
→ live_l2_capture 绕过它空打了三周（617 轮里 502 轮烧在超时上）。

本文件钉死的契约：
  1. 开关置位 → available() 必须 False、_require() 必须抛；
  2. 置位期间 **一次网络都不许发、一秒都不许睡**；
  3. 开关**每次现读**（进程内改 env 立即生效，不需要重启/重导入）；
  4. 禁用是可撤销的临时态，不得污染「加载失败」缓存。
"""
from __future__ import annotations

import sys
import types

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from agent_tools.datasources import tdx_aidata as TA  # noqa: E402

OFF_VAR = "LIVE_QUOTES_DISABLE_AIDATA"
ALT_VAR = "TDX_AIDATA_DISABLE"


class _Clock:
    """假时钟：time() 可控、sleep() 只记账不真睡。

    直接 patch `time.sleep` 会污染所有模块；把 `TA.time` 换成这个对象，作用域
    只在 tdx_aidata 内部，且能同时断言「睡了几次」。
    """

    def __init__(self, t: float = 1_760_000_000.0):
        self.t = t
        self.slept: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@pytest.fixture
def env(monkeypatch):
    """无开关、干净加载缓存、假 tqs、假时钟的 tdx_aidata。

    本机 /opt/tdx-aidata 真实存在 → 必须把 AIDATA_DIR 指走，否则 _load() 会真的
    import tqServer（= 接真网络）。tqServer 用假模块注入，命中 sys.modules 优先。
    """
    clock = _Clock()
    calls: list[tuple] = []

    def _get_market_data(**kw):
        calls.append(("get_market_data", kw))
        return {"Close": {"600519.SH": [1.0]}}

    fake = types.SimpleNamespace(get_market_data=_get_market_data,
                                 get_market_snapshot=lambda **kw: None)
    monkeypatch.setitem(sys.modules, "tqServer", types.SimpleNamespace(tqs=fake))
    monkeypatch.setattr(TA, "AIDATA_DIR", "/nonexistent-aidata-for-test")
    monkeypatch.setattr(TA, "_tqs", None)
    monkeypatch.setattr(TA, "_load_error", None)
    monkeypatch.setattr(TA, "_last_ok_write", 0.0)
    monkeypatch.setattr(TA, "time", clock)
    monkeypatch.delenv(OFF_VAR, raising=False)
    monkeypatch.delenv(ALT_VAR, raising=False)
    return {"clock": clock, "calls": calls, "fake": fake}


# ---------- 1. 开关置位 → 不可用 ----------

@pytest.mark.parametrize("var", [OFF_VAR, ALT_VAR])
def test_available_false_when_switch_set(env, monkeypatch, var):
    """两个开关变量都生效：新名 TDX_AIDATA_DISABLE + 复用旧名 LIVE_QUOTES_DISABLE_AIDATA。

    复用旧名是刻意的——conftest 的 autouse fixture 与生产 .env:46 都在用旧名，
    改名会让「测试隔离」静默失效（那次事故的注释就写在 conftest:129-134）。
    """
    monkeypatch.setenv(var, "1")
    assert TA.available() is False
    with pytest.raises(RuntimeError, match="已禁用"):
        TA._require()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " Yes "])
def test_truthy_spellings_all_disable(env, monkeypatch, value):
    """与 live_quotes._aidata_allowed 同语义（1/true/yes），不引入第二套方言。"""
    monkeypatch.setenv(OFF_VAR, value)
    assert TA.available() is False


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_falsy_values_keep_channel_open(env, monkeypatch, value):
    monkeypatch.setenv(OFF_VAR, value)
    assert TA.available() is True


@pytest.mark.parametrize("var", [OFF_VAR, ALT_VAR])
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " Yes ", "0", "false", "no", ""])
def test_the_caller_gate_and_the_adapter_gate_agree(env, monkeypatch, var, value):
    """两边口径必须逐字一致（2026-09-22 审查 LOW-2）。

    真实形态是**静默**的：探针按 strip/lower 判「已关闭」，而真正决定要不要调用
    AiData 的 `live_quotes._aidata_allowed()` 当时是裸 `in ("1","true","yes")`——
    `.env` 写成 `...=True` 时，看板报「关闭」、调用侧照旧走 AiData 并失败，
    看板绿着而通道在报错。
    """
    import live_quotes as LQ

    monkeypatch.setenv(var, value)

    assert bool(TA.gate_reason()) == (not LQ._aidata_allowed()), \
        f"{var}={value!r} 两边判定分歧"


def test_gate_reason_names_the_variable_and_value_that_fired(env, monkeypatch):
    """文案要指得出是哪个变量、哪个值（硬编码 `=1` 会在 any 别的写法下误导）。"""
    monkeypatch.setenv(ALT_VAR, "yes")

    reason = TA.gate_reason()

    assert ALT_VAR in reason and f"{ALT_VAR}=yes" in reason, reason


# ---------- 2. 置位期间零网络、零 sleep ----------

def test_disabled_never_touches_network(env, monkeypatch):
    """闸门关闭时 get_klines 必须抛，且底层一次都没被调用。

    这是本文件的核心用例：旧形态下每次调用都要等 _RETRY_GAPS 阶梯 + .so 内建
    重连，单次最坏 ≈380s，而调用方只等 5s。
    """
    monkeypatch.setenv(OFF_VAR, "1")
    with pytest.raises(RuntimeError, match="已禁用"):
        TA.get_klines("600519.SH", interval="5m", count=3)
    assert env["calls"] == [], "闸门关闭却仍发起了调用"
    assert env["clock"].slept == [], "闸门关闭却仍在退避睡眠"


def test_call_with_retry_short_circuits_prebound_fn(env, monkeypatch):
    """直接传已绑定的 fn 也必须被拦（调用方可能绕过 _require）。"""
    monkeypatch.setenv(OFF_VAR, "1")
    with pytest.raises(RuntimeError, match="已禁用"):
        TA._call_with_retry(env["fake"].get_market_data, stock_list=["600519.SH"])
    assert env["calls"] == []
    assert env["clock"].slept == []


# ---------- 3. 开关每次现读（热生效） ----------

def test_gate_is_read_fresh_not_cached(env, monkeypatch):
    """先加载成功，再置开关 → 立即生效；撤销 → 立即恢复。

    旧实现在 import 时算常量，或把禁用写进 _load_error 缓存，两种都会让
    「运维改了 .env 但进程没重启」变成静默无效。
    """
    assert TA.available() is True           # 已加载（_tqs 缓存已填充）
    monkeypatch.setenv(OFF_VAR, "1")
    assert TA.available() is False          # 已加载也照样关
    monkeypatch.delenv(OFF_VAR)
    assert TA.available() is True           # 可撤销


def test_disable_does_not_poison_load_error_cache(env, monkeypatch):
    """禁用期间不得把原因写进 _load_error（那是「加载失败」的缓存，写进去就开不回来）。"""
    monkeypatch.setenv(OFF_VAR, "1")
    assert TA.available() is False
    assert TA._load_error is None, "禁用被误写进 _load_error 缓存"
    monkeypatch.delenv(OFF_VAR)
    assert TA.available() is True
