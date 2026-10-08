"""AiData 跨进程熔断器契约（P0-1）。

为什么必须跨进程：`live_l2_capture` 由 cron 每 5 分钟拉一个新进程。进程内的
`_MINUTE_OK` 标记（live_l2_capture.py:431）注释写「本轮起跳过」，语义其实是
「本进程起跳过」——下一轮新进程照打，实测 617 轮里 502 轮重复付超时代价。

为什么 record_fail() 是公开 API：通道级故障的形态是 `.so` 内部挂死（stdout 打
「[错误码 11] 连接失败」），异常根本回不到 `_call_with_retry`——只有做时间盒的
调用方看得见超时，超时不上报则熔断永不开。

**当前仓库里没有这样的调用方**（`live_l2_capture._timebox` 已随 AiData 分钟路径
退役删除）→ 挂死型故障目前不受熔断保护。这是**已知缺口，不是设计**：`record_fail`
的 docstring 与 `tests/test_aidata_breaker.py::test_external_timebox_can_trip_the_breaker`
一起留着这个 API 的契约，等时间盒回来接上。

字段语义逐字对齐 rt_probe.apply_streaks（首败时刻不被后续失败覆盖 =
「自 Y 时刻起」），时间口径对齐 live_account_cache（ts 为 epoch + ts_cn 显式 +08:00）。
"""
from __future__ import annotations

import json
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_tools.datasources import tdx_aidata as TA  # noqa: E402

OFF_VAR = "LIVE_QUOTES_DISABLE_AIDATA"
CN_OFFSET = "+08:00"


class _Clock:
    def __init__(self, t: float = 1_760_000_000.0):
        self.t = t
        self.slept: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@pytest.fixture
def env(monkeypatch, tmp_path):
    """干净熔断器（路径重定向由 conftest 兜底网负责，这里断言它真的生效）。"""
    clock = _Clock()
    monkeypatch.setattr(TA, "time", clock)
    monkeypatch.setattr(TA, "_last_ok_write", 0.0)
    monkeypatch.delenv(OFF_VAR, raising=False)
    monkeypatch.delenv("TDX_AIDATA_DISABLE", raising=False)
    assert str(TA.BREAKER_FILE).startswith(str(tmp_path)), \
        "熔断状态文件没被隔离——用例会写生产 logs/aidata_breaker.json"
    return clock


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch):
    """本文件有若干用例驱动 `rt_probe.probe_aidata`，而探针的 disabled 判定会直接
    读真实 .env（生产常态：LIVE_QUOTES_DISABLE_AIDATA=1，2026-09-21 止血开关）——
    不隔离的话这些用例会被真实 .env 判成「关闭」，breaker 状态机根本跑不到。
    同 test_rt_aidata_probe.py 的隔离。"""
    try:
        import rt_probe as RP
    except ImportError:      # 采集期缺依赖时不影响本文件其余用例
        return
    monkeypatch.setattr(RP, "_env_kv", lambda prefix="": {})


def _boom(*a, **k):
    raise ConnectionError("错误码 11 连接失败")


def _empty(*a, **k):
    return {}          # 限流签名：整个结果为空


def test_threshold_below_opens_no_window(env):
    """未达阈值不开窗：单次抖动不该让整条链停摆。"""
    for _ in range(TA._BREAKER_THRESHOLD - 1):
        state = TA.record_fail("抖动")
    assert state["fail_streak"] == TA._BREAKER_THRESHOLD - 1
    assert TA.breaker_open() is None
    assert state["open_until"] == 0


def test_threshold_reached_opens_window(env):
    for _ in range(TA._BREAKER_THRESHOLD):
        state = TA.record_fail("挂了")
    opened = TA.breaker_open()
    assert opened is not None
    assert opened["fail_streak"] == TA._BREAKER_THRESHOLD
    assert opened["open_until"] > env.t
    assert opened["open_until_cn"].endswith(CN_OFFSET)
    assert state["ts_cn"].endswith(CN_OFFSET), "ts_cn 必须是显式 UTC+8（本机是 JST）"


def test_cooldown_grows_with_streak(env):
    """阶梯递增：限流是分钟级自愈的，一上来就封一小时会过度压制。"""
    windows = []
    for _ in range(TA._BREAKER_THRESHOLD + len(TA._BREAKER_COOLDOWN_LADDER) + 2):
        before = env.t
        TA.record_fail("持续故障")
        opened = TA.breaker_open()
        # 熔断窗是「上次失败时刻 + 冷却」，每次 record_fail 都刷新
        windows.append(round(opened["open_until"] - before, 1) if opened else None)
    graded = [w for w in windows if w]
    assert graded == sorted(graded), f"冷却时长应单调不减: {windows}"
    assert graded[-1] == TA._BREAKER_COOLDOWN_LADDER[-1], "持续故障应封顶在最长冷却"
    assert TA._BREAKER_COOLDOWN_LADDER[-1] > 300, \
        "封顶必须大于 cron 间隔（300s），否则每轮都会重打 = 热循环"


def test_first_fail_ts_is_never_overwritten(env):
    """首败时刻固定 = 「自 Y 时刻起连续失败」（对齐 rt_probe.apply_streaks）。"""
    env.t = 1_760_000_000.0
    first = TA.record_fail("第一次")
    env.t += 600
    later = TA.record_fail("第 N 次")
    assert first["first_fail_ts"] == later["first_fail_ts"]
    assert later["fail_streak"] == first["fail_streak"] + 1


def test_breaker_auto_releases_after_cooldown(env):
    """窗口过期自动放行（不重置计数）——否则一次失败就把通道永久锁死。"""
    for _ in range(TA._BREAKER_THRESHOLD):
        TA.record_fail("挂了")
    assert TA.breaker_open() is not None
    env.t += TA._BREAKER_COOLDOWN_LADDER[0] + 1
    assert TA.breaker_open() is None
    assert TA.read_breaker()["fail_streak"] == TA._BREAKER_THRESHOLD, "过期不该清计数"


# ---------- 短路：窗口内零网络、零 sleep ----------

def test_open_window_blocks_network_and_sleep(env):
    """核心用例：熔断窗口内一次调用都不发、一秒都不睡。"""
    for _ in range(TA._BREAKER_THRESHOLD):
        TA.record_fail("挂了")
    env.slept.clear()
    calls: list = []

    def _fn(**kw):
        calls.append(kw)
        return {"ok": 1}

    with pytest.raises(RuntimeError, match="熔断中"):
        TA._call_with_retry(_fn, stock_list=["600519.SH"])
    assert calls == [], "熔断窗口内仍发起了调用"
    assert env.slept == [], "熔断窗口内仍睡了退避"


def test_failing_call_burns_the_whole_ladder_once(env):
    """一次全败的调用把退避阶梯走完（3 次尝试），但**只记一次失败**。

    这里钉的是「阶梯走完」：重试本身要照做（单次抖动重试一次就好了）。记几次失败
    由 TestFailureCountingUnit 管。
    """
    calls = []

    def _fn(**kw):
        calls.append(kw)
        raise ConnectionError("错误码 11 连接失败")

    with pytest.raises(RuntimeError):
        TA._call_with_retry(_fn)
    assert len(calls) == len(TA._retry_gaps()) + 1, f"退避阶梯没走完：{len(calls)} 次"


def test_empty_result_counts_as_failure_and_still_returns_none(env):
    """**批量**调用的空结果是限流的既定签名（错误码 13 由 .so 打 stdout，不抛异常）
    → 计入熔断。

    但对外契约不变：空结果不抛，返回 None 让调用方回退桥。
    """
    assert TA._call_with_retry(_empty) is None
    assert TA.read_breaker()["fail_streak"] == 1


def test_external_timebox_can_trip_the_breaker(env):
    """时间盒超时（挂死型）由调用方上报——异常回不到 _call_with_retry。"""
    for _ in range(TA._BREAKER_THRESHOLD):
        TA.record_fail("TdxAiData 调用超时（限流？）")
    opened = TA.breaker_open()
    assert opened is not None
    assert "超时" in opened["error"]


# ---------- 恢复 ----------

def test_success_resets_streak_and_clears_window(env):
    for _ in range(TA._BREAKER_THRESHOLD):
        TA.record_fail("挂了")
    env.t += TA._BREAKER_COOLDOWN_LADDER[0] + 1

    assert TA._call_with_retry(lambda **k: {"ok": 1}) == {"ok": 1}
    state = TA.read_breaker()
    assert state["ok"] is True
    assert state["fail_streak"] == 0
    assert state["open_until"] == 0
    assert state["last_ok_ts"].endswith(CN_OFFSET)
    assert TA.breaker_open() is None


def test_success_after_single_failure_still_clears_streak(env):
    """一次失败后来一次成功 → 计数必须归零，否则断续故障会累积到开窗。"""
    TA.record_fail("抖动")
    TA.record_ok()
    assert TA.read_breaker()["fail_streak"] == 0
    for _ in range(TA._BREAKER_THRESHOLD - 1):
        TA.record_fail("抖动")
    assert TA.breaker_open() is None, "归零后应重新计数"


def test_success_write_is_throttled_when_clean(env):
    """健康路径不该每次调用都写盘；但恢复（有失败痕迹）时必须立刻写。"""
    TA.record_ok()
    first = TA.BREAKER_FILE.read_bytes()
    env.t += 1
    TA.record_ok()
    assert TA.BREAKER_FILE.read_bytes() == first, "无失败痕迹时不该反复写盘"

    TA.record_fail("抖动")
    TA.record_ok()
    assert TA.read_breaker()["fail_streak"] == 0, "恢复必须立刻可见"


# ---------- 落盘健壮性 ----------

def test_infinite_counter_does_not_raise(env):
    """JSON 允许 `Infinity`/`NaN` token → `int(inf)` 抛 **OverflowError**（≠ ValueError）。

    熔断状态是跨进程落盘的共享文件，形状不受本进程控制；这个字段由 `record_fail`
    读回来 +1，一旦抛出去，AiData 的**每一次失败**都会变成一个异常——
    比失败本身更难查（调用方看到的是 OverflowError，不是「通道断了」）。
    """
    assert TA._as_int(float("inf")) == 0
    assert TA._as_int(float("nan")) == 0
    assert TA._as_int(True) == 0, "布尔不是计数"


def test_record_fail_tolerates_an_infinite_counter(env):
    TA.BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TA.BREAKER_FILE.write_text('{"ok": false, "fail_streak": Infinity}', encoding="utf-8")

    TA.record_fail("挂了")          # 不抛

    assert json.loads(TA.BREAKER_FILE.read_text(encoding="utf-8"))["fail_streak"] == 1


def test_corrupt_file_reads_as_closed(env):
    """写坏的熔断文件（2026-09-21 盘满曾把状态文件截成 0 字节）→ **判据仍是未熔断**
    （fail-open：状态文件坏掉不该挡住调用），但坏掉这件事本身必须留下痕迹，见
    TestUnreadableStateIsVisible。"""
    TA.BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    for junk in ("", "{", "[]", "null"):
        TA.BREAKER_FILE.write_text(junk, encoding="utf-8")
        assert TA.breaker_open() is None
        assert TA.read_breaker().get("_unreadable") is True


def test_write_is_atomic_no_tmp_left(env):
    """原子写：tmp 名带 pid 后缀（并发写者不互踩），所以钉「不留残渣」而不是某个具体名字。"""
    TA.record_fail("挂了")

    assert not list(TA.BREAKER_FILE.parent.glob(TA.BREAKER_FILE.name + "*.tmp")), "有 tmp 残渣"
    assert json.loads(TA.BREAKER_FILE.read_text(encoding="utf-8"))["fail_streak"] == 1


def test_unwritable_state_file_does_not_break_calls(env, monkeypatch, tmp_path):
    """熔断状态写不进去（盘满/权限）时，调用本身不能跟着炸。"""
    # 父路径是个**文件** → 写必失败（NotADirectoryError），且整个形状留在 tmp 里：
    # 原来写死的 /nonexistent-dir-for-test 在 root 权限下 mkdir 会成功，
    # 用例于是既可能在真实根目录建目录、又测不到「写失败」。
    parent_is_a_file = tmp_path / "not-a-dir"
    parent_is_a_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(TA, "BREAKER_FILE", parent_is_a_file / "b.json")

    assert TA._call_with_retry(lambda **k: {"ok": 1}) == {"ok": 1}
    TA.record_fail("挂了")          # 不抛
    assert TA.breaker_open() is None


# ---------- 计数单位：**调用**，不是尝试 ----------

class TestFailureCountingUnit:
    """`fail_streak` 的语义是「连续几次**调用**失败」——看板、告警、docstring 都这么写。

    旧实现把 `record_fail` 放在重试循环里（每次尝试各记一次），于是：

      * 一次全败的调用 = 3 次计数（退避阶梯 [5,10] ⇒ 3 次尝试），阈值 3 被一次调用打满；
      * 冷却阶梯按 streak 取档 ⇒ **第二次**失败的调用就把窗口推到阶梯顶（1 小时），
        而设计上那是「持续多轮」才该到的档位。

    循环里那句「本轮第 1 次失败就已开窗」的自辩也不成立：streak 从 0 起记时前两次
    尝试都不开窗，短路根本没发生 —— 唯一的效果是阶梯走了 3 倍快。
    """

    def test_one_exhausted_call_counts_once(self, env):
        def _fn(**kw):
            raise ConnectionError("错误码 11 连接失败")

        with pytest.raises(RuntimeError):
            TA._call_with_retry(_fn)
        assert TA.read_breaker()["fail_streak"] == 1, "一次调用被记成了多次失败"

    def test_ladder_advances_one_rung_per_failed_call(self, env):
        """冷却阶梯按「连续几次**调用**失败」取档：每失败一次走一档。

        旧实现一次调用记 3 次 ⇒ 阶梯走 3 倍快：**第二次**失败的调用就已封顶
        （1 小时），而设计上那是「持续多轮」才该到的档位。
        """
        def _fn(**kw):
            raise ConnectionError("挂了")

        rungs = []
        for _ in range(TA._BREAKER_THRESHOLD + 1):     # streak 1..4 → 出窗的是 3、4
            with pytest.raises(RuntimeError):
                TA._call_with_retry(_fn)
            opened = TA.breaker_open()
            if opened:
                rungs.append(round(opened["open_until"] - env.t, 1))
                env.t = opened["open_until"] + 1       # 越过窗口，让下一次真的重试
            else:
                env.t += 1
        assert rungs == [TA._BREAKER_COOLDOWN_LADDER[0], TA._BREAKER_COOLDOWN_LADDER[1]], \
            f"阶梯没按调用次数走：{rungs}"
        assert TA.read_breaker()["fail_streak"] == TA._BREAKER_THRESHOLD + 1

    def test_threshold_still_protects_the_rest_of_the_round(self, env):
        """放大器保护不丢：第三次调用失败即开窗，同轮的其余调用不再触网。"""
        def _fn(**kw):
            raise ConnectionError("挂了")

        for i in range(TA._BREAKER_THRESHOLD):
            with pytest.raises(RuntimeError):
                TA._call_with_retry(_fn)
            env.t += TA._BREAKER_COOLDOWN_LADDER[-1] + 1     # 强制窗口过期，继续试
        env.t = TA.read_breaker()["ts"]                       # 回到窗口内
        calls = []

        def _never(**kw):
            calls.append(1)
            return {"ok": 1}

        with pytest.raises(RuntimeError, match="熔断"):
            TA._call_with_retry(_never)
        assert calls == [], "开窗后仍在触网"


class TestEmptyResultIsNotAlwaysChannelLevel:
    """「空结果 = 通道级」只对**批量**调用成立。

    `get_klines` 走的是 `_query_market_data([symbol], ...)`（单票），`get_quote` 走
    `get_market_snapshot(stock_code=symbol)`（单票）——单票空是**正常业务结果**
    （停牌/退市/代码写错/当天无数据，或 .so 的 ErrorId≠0 被 _clean 转成 None）。
    把它计成通道级失败 = 用一次「查了个停牌股」把整条通道封 5 分钟，而且熔断文件里
    写的诊断是「空结果（限流/无数据）」——把参数错说成了限流。
    """

    def test_single_symbol_empty_does_not_trip_the_channel_breaker(self, env):
        assert TA._call_with_retry(_empty, empty_is_channel=False) is None
        assert TA.read_breaker().get("fail_streak") in (0, None)

    def test_batch_empty_still_counts(self, env):
        assert TA._call_with_retry(_empty, empty_is_channel=True) is None
        assert TA.read_breaker()["fail_streak"] == 1

    def test_single_symbol_exception_still_counts(self, env):
        """抛异常与「单票空」不同：异常说明调用本身没走通，与票无关。"""
        with pytest.raises(RuntimeError):
            TA._call_with_retry(_boom, empty_is_channel=False)
        assert TA.read_breaker()["fail_streak"] == 1


# ---------- 熔断状态自身坏掉时必须可见 ----------

class TestUnreadableStateIsVisible:
    """「读不出熔断状态」不能与「没熔断」同形。

    2026-09-21 盘满把状态文件截成 0 字节是**先例**（不是假设）。旧 `read_breaker`
    把 (OSError, ValueError) 一并吞成 `{}`，于是：熔断机制静默失效（fail-open，
    调用照发）、看板显示健康、`rt_probe` 里那条「熔断状态不可读 → down」的响路
    对最可能的损坏形态根本不可达。
    """

    def _corrupt(self, env, junk: str) -> None:
        TA.BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TA.BREAKER_FILE.write_text(junk, encoding="utf-8")

    @pytest.mark.parametrize("junk", ["", "{", "[]", "null", "不是 JSON"])
    def test_corruption_is_reported(self, env, junk):
        self._corrupt(env, junk)
        assert TA.read_breaker().get("_unreadable") is True, "损坏被当成了「没熔断」"

    def test_missing_file_is_not_corruption(self, env):
        """从没跑过 ≠ 坏了：文件不存在是正常初态，不该当成故障。"""
        assert not TA.BREAKER_FILE.exists()
        assert TA.read_breaker().get("_unreadable") is None

    def test_corruption_does_not_block_calls(self, env):
        """判据是 fail-open（不因为状态文件坏了就挡住调用），但要说得出坏了。"""
        self._corrupt(env, "")
        assert TA.breaker_open() is None
        assert TA._call_with_retry(lambda **k: {"ok": 1}) == {"ok": 1}

    def test_probe_reports_it_as_degraded_not_as_ok_and_not_as_down(self, env):
        """既不能报健康（假绿），也不能报 down（把状态文件的错说成通道的错）。"""
        import rt_probe as RP

        self._corrupt(env, "")
        st = RP.probe_aidata()
        assert st["state"] == "degraded", f"熔断状态坏了却没如实说：{st}"
        assert st["ok"] is False
        assert "熔断" in st["error"] and "状态" in st["error"]

    def test_non_numeric_open_until_does_not_crash(self, env):
        self._corrupt(env, json.dumps({"open_until": "明天", "fail_streak": 2}))
        assert TA.breaker_open() is None          # 不抛就好
        assert TA.read_breaker().get("open_until") == "明天"   # 原样保留供排查


class TestStaleOkIsNotClaimedAsHealthy:
    """`last_ok_ts` 没有时效上界 → 三天前成功过一次就永远渲染「OK」。

    「最后一次成功在三小时前」不是健康，只是**没有近期证据**——与「从没被调用过」
    同义（unproven），不该与真健康同形。
    """

    def test_old_last_ok_ts_is_unproven_not_ok(self, env):
        """年龄必须相对**真实此刻**算。

        `env.t` 是假时钟（约一年前），拿它当「三小时前」会让用例恒真：探针按真实
        时钟一算是一年前的证据，随便什么阈值都判 unproven——测不到边界，只测到
        「不是 ok」。
        """
        import rt_probe as RP

        three_hours_ago = datetime.now(timezone(timedelta(hours=8))) - timedelta(hours=3)
        TA.BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TA.BREAKER_FILE.write_text(json.dumps({
            "ok": True, "fail_streak": 0, "first_fail_ts": "", "open_until": 0,
            "last_ok_ts": three_hours_ago.isoformat(timespec="seconds"),
            "ts": three_hours_ago.timestamp(),
        }), encoding="utf-8")
        st = RP.probe_aidata()

        assert st["state"] == "unproven", f"三小时前的成功被当成现在的健康：{st}"
        assert "分钟前" in st["note"], f"走的不是时效判据（解析失败也会非 ok）：{st}"

    def test_recent_last_ok_ts_is_ok(self, env):
        import rt_probe as RP

        TA.BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TA.BREAKER_FILE.write_text(json.dumps({
            "ok": True, "fail_streak": 0, "first_fail_ts": "", "open_until": 0,
            "last_ok_ts": TA._iso(time.time() - 60), "ts": time.time(),
        }), encoding="utf-8")
        assert RP.probe_aidata()["state"] == "ok"
