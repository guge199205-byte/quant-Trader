"""AiData 探针说真话（P0-2）+ 分钟因子降级进告警面。

旧探针的判据是**文件存在性**（`/opt/tdx-aidata/TdxAiData.ini` 在不在）：
2026-09-02 起通道级挂死三周，客户端文件一直在位 → `aidata: {ok: true,
fail_streak: 0}` 一直全绿，告警永不触发。这是「假绿」的教科书形态 ——
探针测的是「装了没」，不是「通不通」。

新判据改用跨进程熔断状态（agent_tools/datasources/tdx_aidata.BREAKER_FILE）：
那是「最近真的调用过、结果是怎样」的唯一记录。人工闸门（.env 的
LIVE_QUOTES_DISABLE_AIDATA=1）既不是故障也不是健康，单列「关闭」——
否则要么每天假告警，要么把「人为关闭」粉饰成 OK。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import alert_checks as AC  # noqa: E402
import rt_probe as RP  # noqa: E402

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 22, 10, 30, tzinfo=BJ)


@pytest.fixture
def breaker(tmp_path, monkeypatch):
    """熔断状态文件（路径由 conftest 兜底网重定向到 tmp）——断言它真被隔离了。

    探针与 tdx_aidata 必须读**同一份**状态：探针自己存一份就是把「通道现在
    什么样」变成第二个真相来源，那正是本文件要消灭的东西。
    """
    from agent_tools.datasources import tdx_aidata as ta

    assert str(ta.BREAKER_FILE).startswith(str(tmp_path)), \
        "熔断文件没被隔离——用例会读生产 logs/aidata_breaker.json"
    monkeypatch.delenv("TDX_AIDATA_DISABLE", raising=False)
    monkeypatch.delenv("LIVE_QUOTES_DISABLE_AIDATA", raising=False)
    return ta.BREAKER_FILE


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch):
    """本文件的用例一律**不许读真实 .env**。

    生产 `.env` 里 `LIVE_QUOTES_DISABLE_AIDATA=1` 是常态（2026-09-21 的止血开关），
    而探针的 disabled 判定现在也会直接读 .env —— 不隔离的话每条用例都会被真实
    .env 判成「关闭」，断言的 breaker 状态机永远跑不到（本文件刚踩过一次：
    16 条用例同时变红）。要专门测「探针自己读 .env 也能判出禁用」的用例
    自行覆盖这个 patch（见 test_disabled_is_detected_from_the_probes_own_env_read）。
    """
    monkeypatch.setattr(RP, "_env_kv", lambda prefix="": {})


def _write(p: Path, **fields) -> None:
    p.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


def _recent(minutes_ago: float = 1.0) -> str:
    """相对**真实此刻**的成功时刻。

    这里不能写死日期：探针对「成功证据」有时效上界（`rt_probe.OK_EVIDENCE_TTL_SEC`
    = 15 分钟），写死的「最近成功」过一刻钟就成了「很久以前」——用例随日历腐烂，
    而这正是本文件要消灭的那类静默失效（假绿的近亲：绿着，但绿得不是那回事）。
    """
    return (datetime.now(BJ) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class TestProbeAidata:
    def test_manual_gate_reads_as_disabled_not_ok(self, breaker, monkeypatch):
        """人为关闭既不是故障也不是健康 —— 单列「关闭」。"""
        monkeypatch.setenv("LIVE_QUOTES_DISABLE_AIDATA", "1")
        v = RP.probe_aidata()
        assert v["ok"] is True and v["state"] == "disabled"
        assert "关闭" in RP._cell({"aidata": v}, "aidata", "AI数据")

    def test_gate_also_honours_the_primary_switch(self, breaker, monkeypatch):
        monkeypatch.setenv("TDX_AIDATA_DISABLE", "true")
        assert RP.probe_aidata()["state"] == "disabled"

    def test_open_breaker_reads_as_down(self, breaker):
        _write(breaker, ok=False, error="错误码 11 连接失败", fail_streak=7,
               first_fail_ts="2026-09-22T09:00:00+08:00",
               open_until=9e9, open_until_cn="2026-09-22T11:00:00+08:00")
        v = RP.probe_aidata()
        assert v["ok"] is False and v["state"] == "down"
        assert v["channel_fail_streak"] == 7
        assert "AI数据=DOWN" in RP._cell({"aidata": v}, "aidata", "AI数据")

    def test_expired_window_with_live_streak_still_reads_as_down(self, breaker):
        """窗口过期但失败计数没清零 → 通道仍是坏的（窗口只是不再短路）。"""
        _write(breaker, ok=False, error="空结果（限流/无数据）", fail_streak=4,
               first_fail_ts="2026-09-22T09:00:00+08:00", open_until=0)
        v = RP.probe_aidata()
        assert v["ok"] is False and v["channel_fail_streak"] == 4

    def test_recovered_channel_reads_ok(self, breaker):
        _write(breaker, ok=True, error=None, fail_streak=0,
               last_ok_ts=_recent(), open_until=0)
        v = RP.probe_aidata()
        assert v["ok"] is True and v["state"] == "ok"

    def test_stale_success_is_unproven_not_ok(self, breaker):
        """三小时前的成功 ≠ 现在的健康（旧实现没有时效上界：通道死了也永远 OK）。"""
        _write(breaker, ok=True, error=None, fail_streak=0,
               last_ok_ts=_recent(180), open_until=0)
        v = RP.probe_aidata()
        assert v["ok"] is True and v["state"] == "unproven"
        assert "分钟前" in v["note"] and "AI数据=未用" in RP._cell({"aidata": v}, "aidata", "AI数据")

    def test_unparseable_success_time_cannot_prove_currency(self, breaker):
        _write(breaker, ok=True, error=None, fail_streak=0,
               last_ok_ts="昨天下午", open_until=0)
        assert RP.probe_aidata()["state"] == "unproven"

    def test_no_evidence_is_not_claimed_as_ok_or_down(self, breaker):
        """从没被调用过 = 没有证据。既不能说健康（那是旧假绿），也不该报故障。"""
        v = RP.probe_aidata()
        assert v["ok"] is True and v["state"] == "unproven"
        assert "AI数据=未用" in RP._cell({"aidata": v}, "aidata", "AI数据")

    def test_corrupt_breaker_is_degraded_not_a_channel_failure(self, breaker):
        """写坏的熔断文件（2026-09-21 盘满截成 0 字节）。

        **契约变更（2026-09-22）**：旧契约是「报 unproven」，理由是「不得伪造成
        故障」——方向对，但只做了一半：unproven 是 `ok: True`，**看板因此全绿**。
        而「熔断状态读不出」意味着两件确定的事：熔断保护当前失效（fail-open 照
        放行），且通道是死是活无从判断。假绿正是本文件要消灭的东西。

        新契约：单列 `degraded`（ok=False → 走既有告警管线，aidata 按天去重报一次），
        文案指控**状态文件**而不是通道——把状态文件的错说成通道的错，是另一种误诊。
        """
        for junk in ("", "{", "[]", "null"):
            breaker.write_text(junk, encoding="utf-8")
            v = RP.probe_aidata()
            assert v["ok"] is False and v["state"] == "degraded", v
            assert "AI数据=DEGRADED" in RP._cell({"aidata": v}, "aidata", "AI数据")

    def test_missing_breaker_file_is_not_degraded(self, breaker):
        """从没跑过 ≠ 坏了：正常初态仍是「未用」，不能升级成告警。"""
        assert not breaker.exists()
        assert RP.probe_aidata()["state"] == "unproven"

    def test_verdict_ignores_client_files_on_disk(self, breaker):
        """回归钉：判据不得再回到「文件存在性」——那正是三周假绿的成因。

        扫的是**代码**不是源码文本：docstring 里讲这段历史是好事，代码里出现
        任何 `.is_file()` / `.exists()` / 路径常量才是回归。
        """
        import ast

        tree = ast.parse((ROOT / "scripts" / "rt_probe.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "probe_aidata")
        calls = [ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)]
        assert not [c for c in calls if c.endswith((".is_file", ".exists", ".is_dir",
                                                    ".glob", ".stat"))], \
            f"probe_aidata 又依赖文件系统存在性: {calls}"
        doc_node = fn.body[0].value if (fn.body and isinstance(fn.body[0], ast.Expr)
                                        and isinstance(fn.body[0].value, ast.Constant)) else None
        lits = [n.value for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n is not doc_node]
        assert not [s for s in lits if "TdxAiData" in s or "tdx-aidata" in s], \
            f"probe_aidata 又在对客户端目录下结论: {lits}"


class TestRtDownWiring:
    def test_disabled_and_unproven_never_alert(self, breaker, monkeypatch):
        monkeypatch.setenv("LIVE_QUOTES_DISABLE_AIDATA", "1")
        board = RP.apply_streaks({"aidata": RP.probe_aidata()}, {}, "2026-09-22T10:30:00")
        assert AC.rt_down(board) == ("", "", "")

        monkeypatch.delenv("LIVE_QUOTES_DISABLE_AIDATA")
        board = RP.apply_streaks({"aidata": RP.probe_aidata()}, {}, "2026-09-22T10:30:00")
        assert AC.rt_down(board) == ("", "", "")

    def test_open_breaker_alerts_byday(self, breaker):
        _write(breaker, ok=False, error="错误码 11 连接失败", fail_streak=3,
               first_fail_ts="2026-09-22T09:00:00+08:00", open_until=9e9)
        board = RP.apply_streaks({"aidata": RP.probe_aidata()}, {}, "2026-09-22T10:30:00")
        immediate, byday, _key = AC.rt_down(board)
        assert immediate == "" and "aidata" in byday, "熔断打开必须进告警面"

    def test_degraded_alert_names_the_state_not_the_channel(self, breaker):
        """`degraded`（状态文件坏）与 `down`（通道坏）是两件事。

        文案上混成一条 = 另一种误诊：运维照「备胎降级」去查通道，查的是错的方向
        （2026-09-21 盘满把状态文件截成 0 字节正是这个形状）。
        """
        breaker.write_text("{", encoding="utf-8")
        board = RP.apply_streaks({"aidata": RP.probe_aidata()}, {}, "2026-09-22T10:30:00")

        _, byday, key = AC.rt_down(board)

        assert "degraded" in byday, byday
        assert key == "degraded"

    def test_alert_day_key_distinguishes_degraded_from_down(self):
        """两种状态用同一个按天去重键 = 先到的那条吃掉另一条。

        同一天先坏状态文件、后真断通道（或反过来）是两件事，都必须落地。
        """
        deg = {"aidata": {"ok": False, "state": "degraded", "fail_streak": 1}}
        dwn = {"aidata": {"ok": False, "state": "down", "fail_streak": 5,
                          "first_fail_ts": "2026-09-22T09:00:00"}}

        assert AC.rt_down(deg)[2] == "degraded"
        assert AC.rt_down(dwn)[2] == "down"

    def test_alert_day_key_is_path_safe(self):
        """第 3 行会被 alert.sh 拼进 /tmp 的文件名，而板子是跨进程落盘 JSON。"""
        hostile = {"aidata": {"ok": False, "state": "../../etc/passwd x", "fail_streak": 9}}

        key = AC.rt_down(hostile)[2]

        assert key and all(c.isalnum() or c == "_" for c in key), key

    def test_legacy_board_without_state_keeps_the_old_day_key(self):
        """老看板（无 state 字段）语义上一直是「通道 down」→ 键必须与原来一致。"""
        legacy = {"aidata": {"ok": False, "fail_streak": 40,
                             "first_fail_ts": "2026-09-08T09:00:00"}}

        assert AC.rt_down(legacy)[2] == "down"


class TestProbeSurvivesAHostileStateFile:
    """H-1（2026-09-22 审查）：`int("abc")` 抛 ValueError 会打死**整个探针进程**。

    六路探针在 main() 里顺序求值 → probe_aidata 抛异常 = 板子根本不写 →
    六个源的告警面全部冻结在上一份 rt_status.json 上（而且看不出来：板子只是
    「没更新」，由另一条检查兜，那条检查自己也可能已经死了）。

    熔断文件是**跨进程共享的落盘状态**：写入方有 bug、有人手改过、磁盘写坏一半，
    都会出现「合法 JSON + 怪类型」。契约：任何 JSON 形状都只能得出一个判定，
    不许抛。
    """

    def test_wrong_typed_counter_cannot_kill_the_probe(self, breaker):
        for junk in ("abc", [1], {"a": 1}, 1e309, True):
            _write(breaker, ok=False, error="错误码 11 连接失败", fail_streak=junk)

            v = RP.probe_aidata()          # 不许抛

            assert v["ok"] is False and v["state"] == "down", (junk, v)
            # 数不出来就报 0 并**说明**，不编一个数（失败本身是真的，必须照报）
            assert v["channel_fail_streak"] == 0, (junk, v)
            assert "fail_streak" in v["note"], (junk, v)

    def test_missing_counter_is_not_reported_as_a_type_error(self, breaker):
        """字段缺失是老产物的正常形状 → 照报 down，但不该说「类型异常」。"""
        _write(breaker, ok=False, error="错误码 11 连接失败")

        v = RP.probe_aidata()

        assert v["state"] == "down" and v["channel_fail_streak"] == 0
        assert "note" not in v

    def test_stale_failure_evidence_carries_its_age(self, breaker):
        """失败证据没有时效标注时，「自 09:00 起连续失败」在次日会被读成正在发生的
        故障——而 first_fail_ts 是 apply_streaks 按**本轮**补写的，失败本身可能
        已经一天前、之后再没人复测过。

        未复测 ≠ 已恢复（仍然是 down，不粉饰成 unproven），但必须能看出「旧」。
        """
        _write(breaker, ok=False, error="错误码 11 连接失败", fail_streak=3,
               ts=time.time() - 26 * 3600)

        v = RP.probe_aidata()

        assert v["ok"] is False and v["state"] == "down"
        assert v["fail_age_sec"] > 25 * 3600
        assert "小时" in v["note"], v

    def test_fresh_failure_has_no_staleness_note(self, breaker):
        _write(breaker, ok=False, error="错误码 11 连接失败", fail_streak=3,
               ts=time.time() - 60)

        v = RP.probe_aidata()

        assert v["state"] == "down" and v["fail_age_sec"] < 3600
        assert "note" not in v

    def test_wrong_typed_streak_in_the_previous_board_cannot_kill_the_writer(self):
        """H-1 的同类项，另一半：`apply_streaks` 读的是**上一份板子**（同样是落盘的
        JSON）。它抛 = main() 写不了新板子 = 六路告警面冻在上一份上——与 probe_aidata
        抛一样致命，只是位置不同。
        """
        for junk in ("abc", [1], 1e309, True):
            out = RP.apply_streaks({"aidata": {"ok": False, "state": "down"}},
                                   {"aidata": {"fail_streak": junk}},
                                   "2026-09-22T10:30:00")

            assert out["aidata"]["fail_streak"] == 1, (junk, out)
            assert out["aidata"]["first_fail_ts"] == "2026-09-22T10:30:00"

    def test_missing_streak_in_the_previous_board_starts_at_one(self):
        out = RP.apply_streaks({"aidata": {"ok": False}}, {"aidata": {}}, "T")

        assert out["aidata"]["fail_streak"] == 1

    def test_future_success_time_does_not_prove_health(self, breaker):
        """时钟回拨 / 写入方用错基准 → last_ok_ts 在未来。

        未来时刻对任何 TTL 都「永远新鲜」→ 一次错误写入就能把通道永久刷绿，
        比没有上界更坏。容忍小抖动（NTP 步进），大偏移一律不予采信。
        """
        _write(breaker, ok=True, error=None, fail_streak=0, open_until=0,
               last_ok_ts=(datetime.now(BJ) + timedelta(hours=3)).isoformat(timespec="seconds"))

        v = RP.probe_aidata()

        assert v["ok"] is True and v["state"] == "unproven"
        assert "未来" in v["note"], v

    def test_small_clock_skew_is_still_healthy(self, breaker):
        """未来 30 秒内是正常的对时误差，不该把健康翻成未用。"""
        _write(breaker, ok=True, error=None, fail_streak=0, open_until=0,
               last_ok_ts=(datetime.now(BJ) + timedelta(seconds=30)).isoformat(timespec="seconds"))

        assert RP.probe_aidata()["state"] == "ok"


class TestCronLikeImportPath:
    """H-2（2026-09-22 审查）：`agent_tools` 是**仓库根**下的包，而 cron 跑的是
    `python /path/scripts/rt_probe.py` —— sys.path[0] 只有 scripts/。

    修复前 probe_aidata 能跑，纯属 main() 先调 probe_fuyao、后者 import live_quote、
    由 live_quote 顺手把仓库根插进 sys.path 的**顺序巧合**。调用顺序一改、或
    live_quote 因任何原因导入失败，探针立刻报
    「熔断状态不可读: No module named 'agent_tools'」→ AiData 明明健康却每天一条
    false channel-DOWN，还占掉当天的 aidata 告警额度。
    """

    def test_importing_rt_probe_makes_agent_tools_importable(self):
        import subprocess
        import textwrap

        driver = textwrap.dedent(f"""
            import json, sys, tempfile, pathlib, os
            os.environ.pop("LIVE_QUOTES_DISABLE_AIDATA", None)
            os.environ.pop("TDX_AIDATA_DISABLE", None)
            # cron 的 sys.path：只有 scripts/（没有仓库根，也没有 cwd 之外的任何东西）
            sys.path = [p for p in sys.path if p not in ("", {str(ROOT)!r})]
            sys.path.insert(0, {str(ROOT / "scripts")!r})
            out = {{}}
            try:
                import rt_probe
                from agent_tools.datasources import tdx_aidata as ta
            except Exception as e:
                out["import_error"] = f"{{type(e).__name__}}: {{e}}"
            else:
                ta.BREAKER_FILE = pathlib.Path(tempfile.mkdtemp()) / "b.json"
                out["probe"] = rt_probe.probe_aidata()
            print(json.dumps(out, ensure_ascii=False))
        """)

        r = subprocess.run([sys.executable, "-c", driver], capture_output=True,
                           text=True, cwd="/tmp", timeout=60)

        assert r.returncode == 0, r.stderr[-500:]
        got = json.loads(r.stdout)
        assert "import_error" not in got, got
        v = got["probe"]
        assert v["state"] != "down", f"cron 式 sys.path 下 AiData 被误判成通道故障：{v}"
        assert "No module named" not in str(v.get("error") or "")


class TestMinuteCapabilityAlert:
    """l2_status.json 的 capabilities.minute_k → 一行告警（新增检查）。"""

    def _doc(self, **cap):
        """真实产物形状：轮次时刻 + 能力戳（写侧 run_pass 两个键一起写）。

        轮次时刻是**新鲜**的（NOW 前 1 分钟）：不新鲜的形状由停更用例单独构造，
        否则每个用例都会先撞上停更判据，把被测的能力判据整个盖掉。
        """
        base = {"ok": False, "state": "no_factors", "error": "算不出因子",
                "fail_streak": 3, "first_fail_ts": "2026-09-22T10:00:00+08:00"}
        base.update(cap)
        return {"last_cycle_at": "2026-09-22T10:29:00+08:00",
                "capabilities": {"minute_k": base}}

    def test_healthy_states_do_not_alert(self):
        for state in ("ok", "warmup", "idle"):
            assert AC.l2_minute_line(self._doc(ok=True, state=state), NOW) is None

    def test_degraded_stamps_alert_after_debounce(self):
        line = AC.l2_minute_line(self._doc(), NOW)
        assert line and "10:00" in line and "算不出因子" in line

    def test_single_round_is_debounced(self):
        """单轮降级可能只是这一轮快照稀疏 —— 连续 ≥2 轮才报。"""
        assert AC.l2_minute_line(self._doc(fail_streak=1), NOW) is None

    def test_missing_stamp_is_silent(self):
        """老产物没有该戳（升级前的文件）→ 不报，避免升级瞬间全量假告警。

        「没有戳」= 无话可说（可能是升级前的产物），与「有戳但轮次时刻读不出来」
        （= 产物在自称健康却证明不了活着）是两回事，后者该报。
        """
        assert AC.l2_minute_line({}, NOW) is None
        assert AC.l2_minute_line(None, NOW) is None
        assert AC.l2_minute_line({"capabilities": {}}, NOW) is None

    def test_frozen_product_alerts_even_while_claiming_ok(self):
        """**停更优先于产物自称的 ok** —— 这正是那次 24 小时零告警的根因。

        轮次崩在写盘上（2026-09-22 盘满，每轮 OSError(28) 穿到 sys.exit）→ 产物
        根本不更新，盘上留着**上一次成功**的戳（ok=true）。读侧只看 ok 就会说
        「健康」，而它其实已经停更半小时。
        """
        doc = self._doc(ok=True, state="ok")
        doc["last_cycle_at"] = "2026-09-22T10:00:00+08:00"     # 停更 30 分钟
        line = AC.l2_minute_line(doc, NOW)
        assert line and "停更" in line and "10:00" in line, f"停更产物被当成健康：{line}"

    def test_stamp_without_a_readable_round_clock_alerts(self):
        """有戳却读不出轮次时刻 = 证明不了活着（写侧两个键是一起写的）。"""
        doc = self._doc()
        del doc["last_cycle_at"]
        assert AC.l2_minute_line(doc, NOW) is not None
        doc["last_cycle_at"] = "不是时间"
        assert AC.l2_minute_line(doc, NOW) is not None

    def test_recent_round_is_not_stale(self):
        """轮次时刻在阈值内 → 不报停更（5 分钟一轮，阈值要留出一轮半的余量）。"""
        doc = self._doc(ok=True, state="ok")
        doc["last_cycle_at"] = "2026-09-22T10:28:00+08:00"
        assert AC.l2_minute_line(doc, NOW) is None

    def test_outside_trading_window_is_silent(self):
        """非交易日/盘后不报：采集本来就不跑，那是设计不是故障。"""
        assert AC.l2_minute_line(self._doc(), datetime(2026, 9, 22, 20, 0, tzinfo=BJ)) is None
        assert AC.l2_minute_line(
            self._doc(), datetime(2026, 9, 26, 10, 30, tzinfo=BJ)) is None, "周六"

    def test_stale_source_state_is_named(self):
        line = AC.l2_minute_line(self._doc(state="stale_source", error="快照源停更 600 秒"), NOW)
        assert line and "快照源停更" in line


# ---------- 板子本身不许丢（2026-09-22 二轮审查 HIGH-1） ----------
#
# 六路探针 + 一次原子写，全裸在 main() 里：任何一处抛异常（盘满 ENOSPC 打在
# write_text 上、logs 权限变、probe_quantdb 无 try、_bridge_urls() 抛）→ 进程非零
# 退出、logs/rt_status.json **一字不写**，六路降级告警全部冻在上一份板上。
# 2026-09-21 盘满就是这个形态的预演（那天恰好板子还能写，所以看起来只是「误报
# 主机离线 177 分钟」）。配套的 mtime 哨兵在 alert_checks.rt_stale_line：
# 写侧保证「能写就写」，读侧保证「写不出会有人说」。

class TestTheBoardSurvivesABadProbeOrAFailedWrite:
    def _probes(self, monkeypatch, **over):
        """六路探针钉成固定字典（不触网），只替换要考的某一路。"""
        base = {"bridge": lambda: {"ok": True}, "fuyao": lambda: {"ok": True},
                "tencent": lambda: {"ok": True}, "aidata": lambda: {"ok": True},
                "quantdb": lambda: {"ok": True}, "account": lambda: {"ok": True}}
        for k, fn in over.items():
            base[k] = fn
        for k, fn in base.items():
            monkeypatch.setattr(RP, f"probe_{k}", fn)
        monkeypatch.setattr(RP, "_prev_board", lambda: {})

    def test_one_probe_raising_still_writes_the_board(self, tmp_path, monkeypatch):
        out = tmp_path / "rt_status.json"
        monkeypatch.setattr(RP, "OUT", out)
        self._probes(monkeypatch, quantdb=lambda: (_ for _ in ()).throw(
            RuntimeError("quantdb 客户端炸了")))

        rc = RP.main()

        assert rc == 0, "单路探针自身异常不是「板子写不出去」"
        board = json.loads(out.read_text(encoding="utf-8"))
        assert board["quantdb"]["ok"] is False, "坏掉的那一路必须显式 DOWN"
        assert "quantdb 客户端炸了" in board["quantdb"]["error"]
        assert board["bridge"]["ok"] is True, "其余五路照常落盘"

    def test_probe_error_is_its_own_state_not_a_channel_down(self, tmp_path, monkeypatch):
        """探针自己坏了 ≠ 通道坏了（同 degraded 的分野）：状态词必须自成一类，
        否则「探针代码有 bug」会被读成「桥挂了」，去排错的那天全在错误的地方。"""
        out = tmp_path / "rt_status.json"
        monkeypatch.setattr(RP, "OUT", out)
        self._probes(monkeypatch, bridge=lambda: 1 / 0)

        RP.main()

        board = json.loads(out.read_text(encoding="utf-8"))
        assert board["bridge"]["state"] == "probe_error"
        assert "ZeroDivisionError" in board["bridge"]["error"]

    def test_unwritable_board_fails_loudly(self, tmp_path, monkeypatch, capsys):
        """写不出去是**必须让人看见**的失败：留一行带时刻的错误 + 非 0 退出码
        （cron/看门狗的唯一非人读信号），而不是一个裸 traceback 淹在日志里。"""
        monkeypatch.setattr(RP, "OUT", tmp_path / "no_such_dir" / "rt_status.json")
        self._probes(monkeypatch)

        rc = RP.main()

        err = capsys.readouterr().out
        assert rc == 1, "写盘失败必须非 0 退出"
        assert "看板写不出去" in err and "FileNotFoundError" in err
        assert not (tmp_path / "no_such_dir" / "rt_status.json").exists()


# ---------- 陈旧证据的年龄必须进文案（二轮审查 MEDIUM-2） ----------
#
# rt_probe 给超期失败证据写了 `fail_age_sec` + note，但全仓核验：只有 rt_probe
# 自己和它的测试读这个字段——`_cell`、`rt_down`、alert.sh 的 QQ 文案都不读。
# 而 aidata 是按天去重的：一笔「首次失败在上上周」的证据**每天重新报一次**，
# 文案里 `first_fail_ts[11:16]` 只留时分 → 30 小时前的故障读起来像今天的。

class TestStaleEvidenceAgeReachesTheAlert:
    def test_byday_text_carries_the_age_of_a_stale_failure(self):
        doc = {"aidata": {"ok": False, "state": "down", "fail_streak": 3,
                          "first_fail_ts": "2026-09-20T10:30:00",
                          "fail_age_sec": 30 * 3600}}

        immediate, byday, _ = AC.rt_down(doc)

        assert "30 小时前" in byday, byday
        assert "未复测" in byday, "说清「此后没有调用记录」，不是正在发生"

    def test_fresh_failure_has_no_age_noise(self):
        doc = {"aidata": {"ok": False, "state": "down", "fail_streak": 3,
                          "first_fail_ts": "2026-09-22T10:30:00", "fail_age_sec": 120}}

        _, byday, _ = AC.rt_down(doc)

        assert "小时前" not in byday, "刚发生的故障不该带年龄噪音"

    def test_age_threshold_mirrors_the_probe(self):
        """阈值不许各写各的：写侧（rt_probe）与读侧（alert_checks）必须同一个数。

        alert_checks 跑在系统 python3（不能 import rt_probe）→ 只能复制常量，
        那就必须钉住复制值（同 EXEC_SWITCH_OFF_NOTE 的处理）。
        """
        assert AC.FAIL_EVIDENCE_STALE_SEC == RP.FAIL_EVIDENCE_STALE_SEC

    def test_bad_typed_age_does_not_kill_the_check(self):
        doc = {"aidata": {"ok": False, "state": "down", "fail_streak": 3,
                          "first_fail_ts": "2026-09-20T10:30:00",
                          "fail_age_sec": "很久"}}

        _, byday, _ = AC.rt_down(doc)

        assert "aidata" in byday and "小时前" not in byday


# ---------- disabled 判定不许依赖别人的副作用（二轮审查 MEDIUM-3） ----------

def test_disabled_is_detected_from_the_probes_own_env_read(monkeypatch, breaker):
    """cron 的 `env -i` 下 os.environ 本来是干净的：今天这条判定能工作，靠的是
    import 链上 `agent_tools.tools.price_tools` 的**模块级 load_dotenv()** 顺手把
    .env 灌了进来。谁把那次 load_dotenv 挪进函数，看板就从「AI数据=关闭」无声
    翻成一条陈旧的 DOWN（breaker 里还留着禁用前的失败记录 → 每天一条假故障）。

    所以 disabled 必须由探针**自己读 .env**（`_env_kv`）也能判出来。
    """
    from agent_tools.datasources import tdx_aidata as ta

    # os.environ 干净（= cron 的 env -i，或 load_dotenv 被挪走）+ gate_reason 说不
    # 出话（它只看 os.environ），只有 .env 里写着开关。
    monkeypatch.delenv("LIVE_QUOTES_DISABLE_AIDATA", raising=False)
    monkeypatch.delenv("TDX_AIDATA_DISABLE", raising=False)
    monkeypatch.setattr(ta, "gate_reason", lambda: "")
    monkeypatch.setattr(RP, "_env_kv", lambda prefix="": {"LIVE_QUOTES_DISABLE_AIDATA": "1"})

    out = RP.probe_aidata()

    assert out["state"] == "disabled", out
    assert out["ok"] is True
    assert "LIVE_QUOTES_DISABLE_AIDATA" in out["note"], "要指得出是哪个变量关的"


def test_inline_comment_in_env_does_not_hide_the_gate(monkeypatch):
    """`.env` 写成 `VAR=1  # 应急` 也算禁用：_env_kv 是手写解析器、不认行内注释，
    安全开关「没认出来」会把闸门静默当成没设——比误判成禁用坏得多。"""
    monkeypatch.setattr(RP, "_env_kv", lambda prefix="": {
        "LIVE_QUOTES_DISABLE_AIDATA": "1   # 2026-09-21 应急止血"})

    assert "LIVE_QUOTES_DISABLE_AIDATA=1" in RP._disabled_from_env_file()
