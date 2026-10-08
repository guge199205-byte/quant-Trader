"""采集器的「你还在跑吗」——这条判据此前**根本不存在**（2026-09-22 实录）。

事实链（全部实测，非推断）：

1. 09-21 14:35 起盘满（ENOSPC）。`live_l2_capture.run_pass` 在
   `_atomic_write(STATE_FILE, …)` 抛 OSError(28)，**异常无人接**，一路穿到
   `sys.exit(main())` → 该轮在打印「L2 采集完成」之前就死了。
2. 于是 `logs/live_l2_capture.log` 里 09-21 14:35 → 09-22 14:45 之间**没有一行
   带时间戳的输出**，只有 3492 行无时间戳的原始输出、其中 **158 个 traceback**。
3. `logs/min_snapshots/2026-09-22.jsonl` 首行 13:45:42 —— 上午四个半小时的快照
   全缺。原因同源：`min_snapshot.sample_once` 的写盘在
   `except Exception: continue` 里，**吞掉且不记**，而该文件从头到尾没有一行 print。
4. 13:45 恢复，只是因为当天停掉 test7 实例释放了 24G。
5. **告警面全程静默**：`alert_checks.l2_minute_line` 读的是
   `capabilities.minute_k` 的**自称** `ok`；轮次崩了就不写盘，盘上留着**上一次
   成功**的戳（ok=true）→ 读出来一切正常。`last_cycle_at` 写进了产物，
   **全仓无人读**（唯一引用是另一份陈旧副本 configs/patches/）。

所以本文件钉两条独立判据：
  * **写侧**：一轮崩了必须留下带时间戳的一行（否则日志里只剩无主的 traceback）；
  * **读侧**：产物**停更**本身就是降级——不能只信产物自称的 ok。

两者缺一：只有写侧 → 仍要靠人翻日志；只有读侧 → 报警了但查不出为什么。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BJ = ZoneInfo("Asia/Shanghai")

import alert_checks as AC  # noqa: E402
import live_l2_capture as L2  # noqa: E402
import min_snapshot as MS  # noqa: E402

# 2026-09-22 周二 = 交易日（同 test_quote_freshness_anchor 的已核对日历）
TRADING_DAY = (2026, 9, 22)


def _bj(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=BJ)


def _cap(**over) -> dict:
    """`live_l2_capture.minute_capability` 的产物形状（只取本文件用到的字段）。"""
    cap = {"ok": True, "state": "ok", "error": None, "fail_streak": 0,
           "first_fail_ts": None}
    cap.update(over)
    return cap


class TestRoundCrashLeavesATrace:
    """写侧：一轮崩了，日志里必须有一行带时间戳的中止原因。"""

    def _prep(self, monkeypatch, exc: Exception, broker=None):
        monkeypatch.setattr(sys, "argv", ["live_l2_capture.py"])
        monkeypatch.setattr(L2, "now_cn", lambda: _bj(*TRADING_DAY, 10, 0))
        monkeypatch.setattr(L2, "in_window", lambda now: True)
        monkeypatch.setattr(L2, "_load_broker", lambda: broker or object())

        def _boom(_broker):
            raise exc

        monkeypatch.setattr(L2, "run_pass", _boom)

    def test_enospc_round_is_reported_not_silent(self, monkeypatch, capsys):
        """盘满：这是 09-22 那 158 个 traceback 的真实起因。"""
        self._prep(monkeypatch, OSError(28, "No space left on device"))
        rc = L2.main()
        out = capsys.readouterr().out
        assert "No space left on device" in out, f"本轮崩溃没留下原因：{out!r}"
        assert "[2026-09-22 10:00:00]" in out, (
            f"崩溃行没有时间戳——日志里又会是一堆无主 traceback：{out!r}")
        assert rc != 0, "崩溃轮不该返回 0（退出码是 cron 侧唯一的非人读信号）"

    def test_arbitrary_exception_is_reported(self, monkeypatch, capsys):
        """不挑异常类型：任何未预期异常都不许静默带走一整轮。"""
        self._prep(monkeypatch, RuntimeError("broker 探活失败"))
        L2.main()
        assert "broker 探活失败" in capsys.readouterr().out

    def test_successful_round_keeps_its_existing_line(self, monkeypatch, capsys):
        """回归：正常轮仍打原来那行，且返回 0（别把成功路径也改了）。"""
        self._prep(monkeypatch, RuntimeError("unused"))
        monkeypatch.setattr(L2, "run_pass", lambda broker: 7)
        rc = L2.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "L2 采集完成：7/" in out


class TestStaleProductIsItselfADegrade:
    """读侧：产物停更 = 降级，哪怕产物自称 ok。"""

    def _line(self, doc, when=None):
        return AC.l2_minute_line(doc, when or _bj(*TRADING_DAY, 10, 30))

    def test_fresh_stamp_is_silent(self):
        doc = {"last_cycle_at": _bj(*TRADING_DAY, 10, 28).isoformat(),
               "capabilities": {"minute_k": _cap()}}
        assert self._line(doc) is None

    def test_frozen_stamp_fires_even_though_it_claims_ok(self):
        """核心：崩溃循环里盘上留的是**上一次成功**的戳，自称 ok=true。"""
        doc = {"last_cycle_at": _bj(*TRADING_DAY, 9, 50).isoformat(),
               "capabilities": {"minute_k": _cap(ok=True, state="ok")}}
        line = self._line(doc)
        assert line, "停更 40 分钟、自称 ok=true 的产物被判成了健康"
        assert "40" in line or "停更" in line, f"没说清停更了多久：{line!r}"

    def test_threshold_leaves_room_for_a_slow_round(self):
        """单轮最坏 80 次桥调用 × 1.5s 节奏 ≈ 2 分钟，阈值必须留足余量。"""
        doc = {"last_cycle_at": _bj(*TRADING_DAY, 10, 25).isoformat(),
               "capabilities": {"minute_k": _cap()}}
        assert self._line(doc) is None, "5 分钟的正常节奏被判成停更（假告警）"

    def test_unparseable_cycle_time_is_not_proof_of_life(self):
        """产物在、但读不出轮次时刻 → 证明不了活着，按停更处理。"""
        doc = {"last_cycle_at": "???", "capabilities": {"minute_k": _cap()}}
        assert self._line(doc) is not None

    def test_missing_file_stays_silent(self):
        """部署瞬间产物可能还不存在 → 不报（沿用既有的部署友好约定）。"""
        assert self._line(None) is None

    @pytest.mark.parametrize("when,why", [
        (_bj(2026, 9, 26, 10, 30), "周六休市"),
        (_bj(*TRADING_DAY, 12, 0), "午休空档"),
        (_bj(*TRADING_DAY, 16, 0), "收盘后"),
    ])
    def test_outside_the_session_stays_silent(self, when, why):
        """采集本就不跑的时候「停更」是设计，不是故障。"""
        doc = {"last_cycle_at": _bj(*TRADING_DAY, 9, 0).isoformat(),
               "capabilities": {"minute_k": _cap()}}
        assert self._line(doc, when) is None, f"{why} 误报停更"

    def test_existing_capability_judgement_still_works(self):
        """回归：真降级（ok=false 且连续 ≥2 轮）仍按老路径报。"""
        doc = {"last_cycle_at": _bj(*TRADING_DAY, 10, 28).isoformat(),
               "capabilities": {"minute_k": _cap(ok=False, state="no_factors",
                                                fail_streak=3,
                                                first_fail_ts="2026-09-22T10:05:00")}}
        line = self._line(doc)
        assert line and "no_factors" in line


class TestSnapshotSamplerIsNotSilent:
    """min_snapshot：写盘失败此前被 `except Exception: continue` 吞掉且不记。"""

    class _Broker:
        def tdx_call(self, method, params):
            return {"Now": "10.5", "Volume": "100", "Before5MinNow": "10.4",
                    "Inside": "60", "Outside": "40"}

    def _prep(self, monkeypatch, tmp_path, *, break_write: bool):
        snap = tmp_path / "logs" / "min_snapshots"
        snap.mkdir(parents=True)
        if break_write:                        # 让 append 打开就失败（真 ENOSPC 的形状）
            (snap / "2026-09-22.jsonl").mkdir()
        monkeypatch.setattr(MS, "ROOT", tmp_path)
        monkeypatch.setattr(MS, "codes", lambda: ["600519.SH"])
        monkeypatch.setattr(MS, "_bj", lambda: _bj(*TRADING_DAY, 10, 0))
        import agent_tools.brokers.tdx_bridge as TB
        monkeypatch.setattr(TB, "TdxBridgeBroker", lambda *a, **k: self._Broker())

    def test_write_failure_is_reported(self, monkeypatch, tmp_path, capsys):
        self._prep(monkeypatch, tmp_path, break_write=True)
        MS.sample_once()
        out = capsys.readouterr().out
        assert out.strip(), "写盘失败被吞了——正是 09-22 上午快照整段缺失的成因"
        assert "600519.SH" in out or "min_snapshot" in out, (
            f"报了但定位不到失败对象：{out!r}")

    def test_success_stays_quiet(self, monkeypatch, tmp_path, capsys):
        """cron 每分钟跑 —— 成功路径不许刷屏（原设计意图保留）。"""
        self._prep(monkeypatch, tmp_path, break_write=False)
        MS.sample_once()
        assert capsys.readouterr().out == ""
        written = (tmp_path / "logs" / "min_snapshots" / "2026-09-22.jsonl")
        assert written.exists() and "600519.SH" in written.read_text(encoding="utf-8")
