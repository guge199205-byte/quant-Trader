"""L2 采集的能力降级戳（P0-2）。

为什么要有它：`data/l2_factors_live.json` 的 `minute_feats` 自 2026-09-02 起出现
0 次，而**没有任何东西报警**——产物的「键不存在」既可能是「今天刚开盘还没攒够
样本」，也可能是「数据源死了三周」。两者在产物里长得一模一样。

这个戳的职责就是把两种状态分开，并沿用仓内既有约定（rt_probe.apply_streaks 的
fail_streak/first_fail_ts + live_account_cache 的 ts/ts_cn 显式 UTC+8）：

  ok=True  + state="ok"      —— 因子在产出
  ok=True  + state="warmup"  —— 开盘头 30 分钟，样本本就不够，**不该报警**
  ok=True  + state="idle"    —— 非交易时段/盘后，**不该报警**
  ok=False + state="..."     —— 真降级：快照源停更 / 时段内却算不出因子

关键约束：warmup 与 idle 必须是 ok=True。否则每天开盘/收盘各刷一轮假告警，
运维会学会忽略这块板——那正是三周没人发现的原因。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_l2_capture as L2  # noqa: E402
import minute_feats as MF  # noqa: E402

BJ = MF  # 复用 minute_feats 的时段口径，避免测试自造第二套
DAY = datetime(2026, 9, 22, tzinfo=__import__("datetime").timezone(timedelta(hours=8)))


def _at(hh: int, mm: int, ss: int = 0) -> datetime:
    return DAY.replace(hour=hh, minute=mm, second=ss)


class TestSnapshotFreshness:
    """快照源新鲜度：这是「因子产不出」的第一因（cron 死了 / 目录没了）。"""

    def test_missing_dir_reads_as_none(self, tmp_path):
        assert MF.latest_sample_ts(tmp_path / "nope") is None

    def test_empty_dir_reads_as_none(self, tmp_path):
        assert MF.latest_sample_ts(tmp_path) is None

    def test_returns_newest_valid_sample_across_files(self, tmp_path):
        (tmp_path / "2026-09-21.jsonl").write_text(
            '{"ts": "2026-09-21T15:00:00+08:00", "code": "600176.SH", "px": 10.0}\n',
            encoding="utf-8")
        (tmp_path / "2026-09-22.jsonl").write_text("\n".join([
            "{ 坏行",
            '{"ts": "2026-09-22T10:00:00+08:00", "code": "600176.SH", "px": 10.0}',
            '{"ts": "2026-09-22T10:01:00+08:00", "code": "600176.SH", "px": 11.0}',
        ]), encoding="utf-8")
        assert MF.latest_sample_ts(tmp_path) == datetime.fromisoformat(
            "2026-09-22T10:01:00+08:00")


class TestCapabilityStamp:
    def test_produced_factors_is_ok(self):
        cap = L2.minute_capability(_at(10, 30), produced=3, watch=3,
                                   last_sample_ts=_at(10, 30))
        assert cap["ok"] is True and cap["state"] == "ok"
        assert cap["source"] == "self" and cap["produced"] == 3
        assert cap["fail_streak"] == 0

    def test_warmup_is_not_a_degradation(self):
        """开盘头 30 分钟样本不够是**预期**，不能报警——否则每天两轮假警报。"""
        cap = L2.minute_capability(_at(9, 40), produced=0, watch=3,
                                   last_sample_ts=_at(9, 40))
        assert cap["ok"] is True and cap["state"] == "warmup"

    def test_idle_outside_session_is_not_a_degradation(self):
        for hh, mm in ((9, 0), (12, 0), (15, 30), (20, 0)):
            cap = L2.minute_capability(_at(hh, mm), produced=0, watch=3,
                                       last_sample_ts=_at(hh, mm))
            assert cap["ok"] is True and cap["state"] == "idle", f"{hh}:{mm} 被当成降级"

    def test_stale_snapshot_source_is_degradation(self):
        """盘中快照 10 分钟没更新 → 采集器死了，这是真降级。"""
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3,
                                   last_sample_ts=_at(10, 20))
        assert cap["ok"] is False
        assert cap["state"] == "stale_source"
        assert cap["snapshot_age_sec"] == 600
        assert "停更" in cap["error"]

    def test_no_factors_after_warmup_is_degradation(self):
        """时段已过 30 分钟、快照新鲜，却一个因子都算不出 → 口径链断了。"""
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3,
                                   last_sample_ts=_at(10, 30))
        assert cap["ok"] is False and cap["state"] == "no_factors"

    def test_no_watch_codes_is_idle_not_degradation(self):
        """空仓时本来就没有持仓代码要算 —— 不是降级。"""
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=0,
                                   last_sample_ts=_at(10, 30))
        assert cap["ok"] is True and cap["state"] == "idle"

    def test_missing_snapshot_source_is_degradation_in_session(self):
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3, last_sample_ts=None)
        assert cap["ok"] is False and cap["state"] == "stale_source"
        assert cap["snapshot_age_sec"] is None

    def test_age_is_none_when_clock_is_before_sample(self):
        """样本时间晚于本机（时钟回拨/时区错）→ 不得当成负龄的「新鲜」。"""
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3,
                                   last_sample_ts=_at(10, 40))
        assert cap["ok"] is False, "未来样本不该被当成健康"


class TestStreakCarryover:
    """fail_streak 跨轮累加、first_fail_ts 不被覆盖（对齐 rt_probe.apply_streaks）。"""

    def test_first_failure_starts_streak(self):
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3,
                                   last_sample_ts=_at(10, 30), prev={})
        assert cap["fail_streak"] == 1
        assert cap["first_fail_ts"] == _at(10, 30).isoformat(timespec="seconds")

    def test_streak_accumulates_and_first_ts_is_pinned(self):
        prev = {"fail_streak": 4, "first_fail_ts": "2026-09-22T10:05:00+08:00"}
        cap = L2.minute_capability(_at(10, 30), produced=0, watch=3,
                                   last_sample_ts=_at(10, 30), prev=prev)
        assert cap["fail_streak"] == 5
        assert cap["first_fail_ts"] == "2026-09-22T10:05:00+08:00", "首败时刻被覆盖了"

    def test_recovery_clears_streak_and_first_ts(self):
        prev = {"fail_streak": 9, "first_fail_ts": "2026-09-22T10:05:00+08:00"}
        cap = L2.minute_capability(_at(10, 30), produced=2, watch=2,
                                   last_sample_ts=_at(10, 30), prev=prev)
        assert cap["fail_streak"] == 0 and cap["first_fail_ts"] is None

    def test_warmup_does_not_inflate_streak(self):
        """warmup 是 ok=True，不该把上一轮的失败计数继续推高（也不清零）。"""
        prev = {"fail_streak": 2, "first_fail_ts": "2026-09-22T09:35:00+08:00"}
        cap = L2.minute_capability(_at(9, 40), produced=0, watch=3,
                                   last_sample_ts=_at(9, 40), prev=prev)
        assert cap["ok"] is True and cap["fail_streak"] == 2

    def test_stamp_carries_cn_time_with_explicit_offset(self):
        """本机是 JST：裸 strftime 会得到北京时间 +1h 却被标成 +08:00。"""
        cap = L2.minute_capability(_at(10, 30), produced=1, watch=1,
                                   last_sample_ts=_at(10, 30))
        assert cap["ts_cn"].endswith("+08:00")
        assert cap["ts_cn"].startswith("2026-09-22T10:30:00")

    def test_degraded_round_does_not_clear_a_running_streak(self):
        """**降级中即便有产出，也不许清零 fail_streak。**

        这条是告警能不能响的关键。快照源 10 分钟前就断了，但断线前攒下的 bar 仍
        躺在文件里 —— `from_rows` 照样算得出 `mom30m`（days/session 过滤都过得去），
        于是 `produced > 0` 与 `state == "stale_source"` **同时成立**。旧写法无条件
        `if produced > 0: streak = 0`，于是每一轮都把计数抹掉，那条按 fail_streak
        防抖的告警**永远等不到第 N 轮**，静默照旧。
        """
        prev = {"fail_streak": 3, "first_fail_ts": "2026-09-22T10:20:00+08:00"}
        cap = L2.minute_capability(_at(10, 30), produced=3, watch=3,
                                   last_sample_ts=_at(10, 20), prev=prev)
        assert cap["ok"] is False and cap["state"] == "stale_source"
        assert cap["produced"] == 3, "产出数是事实，照报；但它是陈货"
        assert cap["fail_streak"] == 4, "降级中却被清零 → 告警永远不响"
        assert cap["first_fail_ts"] == "2026-09-22T10:20:00+08:00"

    def test_streak_clears_only_on_a_genuinely_ok_round(self):
        """清零的唯一条件：state == "ok"（快照新鲜**且**有产出）。"""
        prev = {"fail_streak": 9, "first_fail_ts": "2026-09-22T10:05:00+08:00"}
        cap = L2.minute_capability(_at(10, 30), produced=2, watch=2,
                                   last_sample_ts=_at(10, 30), prev=prev)
        assert cap["state"] == "ok"
        assert cap["fail_streak"] == 0 and cap["first_fail_ts"] is None


class TestNoAidataDependency:
    """退役断言：L2 采集**不得**再碰 AiData（三周静默的根因就是这条链）。"""

    def test_module_holds_no_aidata_symbols(self):
        src = (ROOT / "scripts" / "live_l2_capture.py").read_text(encoding="utf-8")
        assert "tdx_aidata" not in src, "L2 采集仍在引用 AiData"
        for dead in ("_MINUTE_OK", "_TICK_OK", "_timebox"):
            assert dead not in src, f"进程级标记 {dead} 未清除（注释说「本轮起跳过」实为「本进程起」）"

    def test_fetch_paths_are_bridge_only(self, tmp_path, monkeypatch):
        """分钟因子只从自采快照来：喂一份快照就能算出因子，且不触任何网络。"""
        monkeypatch.setattr(L2, "SNAPSHOT_DIR", tmp_path)
        # 冻结时钟到夹具样本时刻：self_minute_feats 内部走 fresh_or_stale(now_cn())，
        # 用墙上时钟的话，盘中跑（or 隔日跑）会把 2026-09-22 的样本判成 stale_source
        # 整份替换掉 → mom30m 键不存在。同文件其余用例（下面两处 now_cn）均为此冻结。
        monkeypatch.setattr(L2, "now_cn", lambda: _at(10, 26))
        rows = [{"ts": _at(10, 5 * i).isoformat(), "code": "600176.SH",
                 "px": 10.0 + 0.01 * i, "vol": 10_000 + 1_000 * i, "b5": 10.0}
                for i in range(6)]                       # 10:00-10:25，6 根 5 分钟 bar
        (tmp_path / "2026-09-22.jsonl").write_text(
            "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in rows),
            encoding="utf-8")
        out = L2.self_minute_feats("600176.SH")
        assert out["mom30m"] == round((10.05 / 10.0 - 1) * 100, 3)
        assert out["src"] == "self" and out["bars"] == 6


class TestFreshnessIsPerSymbolNotPerDirectory:
    """健康判据是**全局**的（`latest_sample_ts` 扫最近两个日期文件、不按 code 过滤），
    而产物是**单票**的。只要还有任何一只票在采，全局探针就报新鲜 —— 某只票停了
    63 分钟也没人知道（2026-09-22 实测 688591.SH，全局最后一条来自别的票）。
    """

    def test_one_symbol_stopping_is_not_masked_by_the_others(self, monkeypatch, tmp_path):
        """本票攒够 6 根后停采 35 分钟，别的票此刻还在采 —— 必须判降级。

        这是最危险的一格：窗口够长（`ok:True` 本该产出）、全局探针新鲜（别的票在
        写），于是产物里躺着一个 35 分钟前的「30min动量」，被当成此刻的。
        """
        rows = [{"ts": _at(13, 30 + 5 * i).isoformat(), "code": "600176.SH",
                 "px": 10.0 + 0.01 * i, "vol": 10_000 + 1_000 * i, "b5": 10.0}
                for i in range(6)]                                  # 本票止于 13:55
        assert rows[-1]["ts"] == _at(13, 55).isoformat()
        rows += [{"ts": _at(14, 29, 40).isoformat(), "code": "688591.SH",
                  "px": 20.0, "vol": 5_000, "b5": 20.0}]            # 别的票此刻在采
        (tmp_path / "2026-09-22.jsonl").write_text(
            "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in rows),
            encoding="utf-8")
        monkeypatch.setattr(L2, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(L2, "now_cn", lambda: _at(14, 30))
        # 先证明这一格确实是「本该产出」：同样的行、把时钟拨到窗口收口处就是 ok
        monkeypatch.setattr(L2, "now_cn", lambda: _at(13, 55, 40))
        assert L2.self_minute_feats("600176.SH")["ok"] is True, "前置条件不成立"
        monkeypatch.setattr(L2, "now_cn", lambda: _at(14, 30))
        out = L2.self_minute_feats("600176.SH")
        assert out.get("ok") is False, f"单票停采被别的票盖住了：{out}"
        assert out["reason"] == "stale_source" and out["age_sec"] == 2100

    def test_the_same_symbol_fresh_still_produces(self, monkeypatch, tmp_path):
        rows = [{"ts": _at(14, 25 + 5 * i).isoformat(), "code": "600176.SH",
                 "px": 10.0 + 0.01 * i, "vol": 10_000 + 1_000 * i, "b5": 10.0}
                for i in range(7)]                                   # 14:25-14:55
        (tmp_path / "2026-09-22.jsonl").write_text(
            "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in rows),
            encoding="utf-8")
        monkeypatch.setattr(L2, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(L2, "now_cn", lambda: _at(14, 55, 40))
        out = L2.self_minute_feats("600176.SH")
        assert out["ok"] is True and "mom30m" in out


class TestPromptPathRefusesStaleMicrostructure:
    """提示词路径（live_hourly_analysis）读快照**不看时钟** —— 它的过滤只到「日期
    与时段」，不看「几点写的」。于是快照下午 13:00 断线、14:00 生成提示词时，
    13:00 那批 bar 算出的「30min动量/5min波动」被原样渲染成**当前**微观结构，
    模型据此下单。同一条链昨天刚断过 4.5 小时（盘满事故），这不是假设。
    """

    def _run(self, monkeypatch, tmp_path, *, now, rows):
        import json as _json

        import live_hourly_analysis as LHA

        d = tmp_path / "logs" / "min_snapshots"
        d.mkdir(parents=True)
        (d / "2026-09-22.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        monkeypatch.setattr(LHA, "ROOT", tmp_path)
        monkeypatch.setattr(LHA, "now_cn", lambda: now)
        return LHA.self_minute_feats("600176.SH")

    @staticmethod
    def _bars(start_hhmm, n, px0: float = 10.0) -> list:
        h, m = start_hhmm
        t0 = DAY.replace(hour=h, minute=m)
        return [{"ts": (t0 + timedelta(minutes=5 * i)).isoformat(),
                 "code": "600176.SH", "px": px0 + 0.01 * i,
                 "vol": 10_000 + 1_000 * i, "b5": px0}
                for i in range(n)]

    def test_stale_snapshots_are_not_rendered_as_current(self, monkeypatch, tmp_path):
        """快照停在 13:25，轮次在 14:00 跑 → 必须判过期，绝不把陈货当现况。"""
        out = self._run(monkeypatch, tmp_path, now=_at(14, 0),
                        rows=self._bars((13, 0), 6))
        assert out.get("ok") is False, f"陈旧的微观结构被当成现况：{out}"
        assert out["reason"] == "stale_source" and out["age_sec"] == 2100
        assert "mom30m" not in out, "过期数据不该带着因子值出门"

    def test_fresh_snapshots_pass_through(self, monkeypatch, tmp_path):
        out = self._run(monkeypatch, tmp_path, now=_at(13, 26),
                        rows=self._bars((13, 0), 6))
        assert out["ok"] is True and out["mom30m"] == round((10.05 / 10.0 - 1) * 100, 3)

    def test_off_session_does_not_invent_staleness(self, monkeypatch, tmp_path):
        """盘后重放/复盘时最近样本本就「很旧」，那不是降级——与轮级戳的 idle 同义。"""
        out = self._run(monkeypatch, tmp_path, now=_at(20, 0),
                        rows=self._bars((13, 0), 6))
        assert out["ok"] is True and "mom30m" in out


class TestMinuteFeatsRendering:
    """渲染层：模型读到的是**这段文字**，降级必须是文字，不能是空条目或 0。"""

    def _lines(self, monkeypatch, *, stored, live):
        import live_hourly_analysis as LHA

        monkeypatch.setattr(LHA, "self_minute_feats", lambda code: dict(live))
        rows = [{"code": "600176.SH"}]
        return LHA._minute_feats_lines(rows, {"600176.SH": {"minute_feats": stored}})

    def test_degradation_is_stated_in_words(self, monkeypatch):
        out = self._lines(monkeypatch,
                          stored={"ok": False, "reason": "insufficient_history",
                                  "have": 1, "need": 6},
                          live={"ok": False, "reason": "insufficient_history",
                                "have": 1, "need": 6})
        assert out == ["- 600176.SH: 分钟特征不可用（连续 5 分钟 bar 只有 1/6 根）"], (
            f"降级没变成人话：{out}")

    def test_stale_source_names_the_age(self, monkeypatch):
        out = self._lines(monkeypatch, stored={},
                          live={"ok": False, "reason": "stale_source", "age_sec": 2100})
        assert "停更 2100 秒" in out[0]

    def test_marker_in_the_product_falls_through_to_a_fresh_read(self, monkeypatch):
        """产物里存的是几分钟前的降级标记 → 现读已经恢复，就该报数而不是报旧标记。"""
        out = self._lines(monkeypatch,
                          stored={"ok": False, "reason": "insufficient_history",
                                  "have": 1, "need": 6},
                          live={"ok": True, "mom30m": 0.5, "bars": 12})
        assert "30min动量 +0.50%" in out[0], f"现读被旧标记挡住了：{out}"

    def test_fresh_product_is_rendered_with_its_window(self, monkeypatch):
        import live_hourly_analysis as LHA

        monkeypatch.setattr(LHA, "now_cn", lambda: _at(10, 26))
        out = self._lines(monkeypatch, stored={"ok": True, "mom30m": -0.25,
                                               "vol5m": 0.31, "bars": 6,
                                               "last_ts": _at(10, 25).isoformat()},
                          live={})
        assert out == ["- 600176.SH: 30min动量 -0.25% · 5min波动 0.31% · 基于 6 根5min"]

    def test_product_claiming_ok_is_still_checked_for_age(self, monkeypatch):
        """**产物自称 ok:True 不能绕过新鲜度闸。**

        这是全局探针的最后一个洞：别的票今天还在采（全局探针 20 秒新鲜），于是
        该票的产物 dict 被直接信任 —— 而它可能是几十分钟前、甚至昨天的窗口。
        """
        import live_hourly_analysis as LHA

        monkeypatch.setattr(LHA, "now_cn", lambda: _at(10, 30))
        out = self._lines(monkeypatch,
                          stored={"ok": True, "mom30m": 1.2, "bars": 12,
                                  "last_ts": _at(9, 55).isoformat()},
                          live={})
        assert "不可用" in out[0], f"35 分钟前的窗口被渲染成了当前：{out}"
        assert "1.20" not in out[0]

    def test_yesterdays_window_is_never_rendered_as_current(self, monkeypatch):
        """只剩上一日的文件时，resample 的日界取的是**昨天** → 昨天的 bar 被算出
        并标 ok:True。盘中看到它必须是降级。"""
        import live_hourly_analysis as LHA

        monkeypatch.setattr(LHA, "now_cn", lambda: _at(10, 30))
        out = self._lines(monkeypatch,
                          stored={"ok": True, "mom30m": 0.9, "bars": 12,
                                  "last_ts": (DAY - timedelta(days=1)).replace(
                                      hour=15).isoformat()},
                          live={})
        assert "不可用" in out[0]

    def test_stale_source_without_samples_says_so(self, monkeypatch):
        out = self._lines(monkeypatch, stored={},
                          live={"ok": False, "reason": "stale_source", "age_sec": None})
        assert out == ["- 600176.SH: 分钟特征不可用（快照源无数据（采集器未运行））"]

    def test_nothing_anywhere_emits_no_line_at_all(self, monkeypatch):
        """产物是标记、现读又什么也没读到 → **不出一行**。

        标记是非空 dict，漏掉分支就会渲染成「- 600176.SH: 」这种空条目 ——
        一个没有内容的条目在提示词里既占位又暗示「算过了」。
        """
        assert self._lines(monkeypatch,
                           stored={"ok": False, "reason": "stale_source", "age_sec": 5},
                           live={}) == []


class TestStampClockIsTakenAtTheCallSite:
    """戳的时钟必须是**判定的那一刻**，不是轮初。

    一轮最坏约 2 分钟（MAX_CALLS_PER_PASS × CALL_INTERVAL_SEC），而快照采集器
    每 20 秒就写一行 —— 也就是说**正常情况下快照必然在轮内被写新**。用轮初的
    now 去比，样本的 ts 恒大于 now → 年龄为负 → 被判「样本来自未来」→
    stale_source。每一轮正常的采集都会误报降级，等于把这块板变成噪声。
    """

    def _run(self, monkeypatch, tmp_path, *, feats, sample_ts, clocks):
        """跑一轮 run_pass，返回落盘的 STATUS_FILE 载荷（不碰真文件、不触网）。"""
        import json as _json

        import live_hourly_analysis as LHA
        import live_llm_trade as LLT
        import news_brief as NB

        clock = list(clocks)
        monkeypatch.setattr(L2, "now_cn", lambda: clock.pop(0) if len(clock) > 1 else clock[0])
        monkeypatch.setattr(L2, "load_state", lambda: {})
        monkeypatch.setattr(L2, "load_factors", lambda: {})
        monkeypatch.setattr(L2, "fetch_l2", lambda b, c: ({"r": 1}, {"s": 1}))
        monkeypatch.setattr(L2, "parse_exday_row", lambda r: {"ok": 1})
        monkeypatch.setattr(L2, "parse_snapshot", lambda s: {"now": 10.0})
        monkeypatch.setattr(L2, "compute_l2_factors", lambda d, s, st: {})
        monkeypatch.setattr(L2, "build_signal_score", lambda f: 0)
        monkeypatch.setattr(L2, "fetch_market_context", lambda b, c: None)
        monkeypatch.setattr(L2, "self_minute_feats", lambda code: dict(feats))
        monkeypatch.setattr(MF, "latest_sample_ts", lambda d, last_n=2: sample_ts)
        monkeypatch.setattr(LHA, "l2_prune_stale", lambda f, c: f)
        monkeypatch.setattr(LLT, "load_pool", lambda n: ([], None))
        monkeypatch.setattr(NB, "load_intraday_watch", lambda: [])
        monkeypatch.setattr(L2.time, "sleep", lambda s: None)

        written = {}
        monkeypatch.setattr(L2, "_atomic_write",
                            lambda p, obj: written.update(obj if p == L2.STATUS_FILE else {}))
        monkeypatch.setattr(L2, "_read_json", lambda p: {})
        monkeypatch.setattr(L2, "STATUS_FILE", tmp_path / "l2_status.json")
        monkeypatch.setattr(L2, "STATE_FILE", tmp_path / "l2_state.json")
        monkeypatch.setattr(L2, "FACTORS_FILE", tmp_path / "l2_factors.json")

        class _B:
            def _account_query(self):
                return {"positions": [{"stock_code": "600176.SH"}]}

        L2.run_pass(_B())
        return _json.loads(_json.dumps(written))

    def test_partial_production_is_not_reported_as_ok(self, monkeypatch, tmp_path):
        """10 只里 8 只有数 → **不是 ok**。

        `produced > 0` 就报 ok 等于「any 当 all」：少的那两只票的分钟特征从产物和
        提示词里整块消失，而戳是绿的、告警不响。降级标记也算在内（标记不是产出）。
        """
        cap = L2.minute_capability(_at(10, 31, 30), produced=8, watch=10,
                                   last_sample_ts=_at(10, 31, 30),
                                   degraded={"688591.SH": "stale_source",
                                             "600519.SH": "insufficient_history"})
        assert cap["ok"] is False and cap["state"] == "partial"
        assert "688591.SH" in cap["error"] and "2/10" in cap["error"]

    def test_full_production_is_ok(self, monkeypatch, tmp_path):
        cap = L2.minute_capability(_at(10, 31, 30), produced=10, watch=10,
                                   last_sample_ts=_at(10, 31, 30), degraded={})
        assert cap["ok"] is True and cap["state"] == "ok"

    def test_sample_written_during_the_round_is_not_from_the_future(self, monkeypatch, tmp_path):
        """快照在本轮**进行中**写入（10:31:00），轮初是 10:30:00 → 不得判 stale。"""
        cap = self._run(monkeypatch, tmp_path,
                        feats={"ok": True, "mom30m": 0.5, "bars": 12},
                        sample_ts=_at(10, 31),
                        clocks=[_at(10, 30), _at(10, 31, 30)])["capabilities"]["minute_k"]
        assert cap["state"] == "ok", f"轮内的新样本被判成了降级：{cap['error']}"
        assert cap["snapshot_age_sec"] == 30
        assert cap["ts_cn"].startswith("2026-09-22T10:31:30"), "戳用的还是轮初时钟"

    def test_marker_dict_does_not_count_as_production(self, monkeypatch, tmp_path):
        """降级标记 `{"ok": false, ...}` 不是产出 —— 否则「10 只里 0 只有数」会报成 ok。"""
        cap = self._run(monkeypatch, tmp_path,
                        feats={"ok": False, "reason": "insufficient_history",
                               "have": 1, "need": 6},
                        sample_ts=_at(10, 31, 30),
                        clocks=[_at(10, 31, 30)])["capabilities"]["minute_k"]
        assert cap["produced"] == 0 and cap["watch"] == 1
        assert cap["ok"] is False and cap["state"] == "no_factors"

class TestHostilePrevStamp:
    """降级戳的 `fail_streak` 由**上一轮的产物文件**读回 +1（run_pass:662 → 520）。

    裸 `int()` 一旦抛出去，run_pass 每轮都死在同一行、**产物永远写不回去** →
    prev 永远是那个坏值 → 自我维持的死循环（不是「一轮失败」，是永久停摆）。
    落盘 JSON 的形状不受本进程控制，读回时必须容忍。
    """

    def test_corrupt_prev_streak_does_not_kill_the_round(self):
        cap = L2.minute_capability(_at(10, 30), 3, 10, _at(10, 29, 30),
                                   {"ok": False, "state": "no_factors", "fail_streak": "abc"})

        # 本轮状态只看本轮事实（3/10 → partial），**不继承 prev 的 state 字段**：
        # prev 里只有 fail_streak / first_fail_ts 是跨轮连续性状态，其余都是上一轮的
        # 快照，照抄等于把旧结论贴在今天上。
        assert cap["ok"] is False and cap["state"] == "partial"
        assert cap["fail_streak"] == 1, "数不出来按 0 起算，但降级本身照报"
        assert isinstance(cap["fail_streak"], int), "写回的必须是能读的值（这是不复发的保证）"

    def test_writer_heals_the_corrupt_counter_on_the_next_round(self):
        """坏值只该影响一轮：下一轮写回的必须是好值（这是「不会永久停摆」的保证）。"""
        first = L2.minute_capability(_at(10, 30), 0, 10, _at(10, 30), {"fail_streak": [1]})
        second = L2.minute_capability(_at(10, 30), 0, 10, _at(10, 30), first)

        assert second["fail_streak"] == first["fail_streak"] + 1
        assert isinstance(second["fail_streak"], int)
