"""分钟特征唯一口径的契约（2026-09-22）。

被替换的旧实现 `live_hourly_analysis.self_minute_feats` 有两个线上缺陷：
  a. `vol5m` 用极差 `(max-min)*100`，与 AiData 路径的标准差不是同一个量，
     而两者喂的是**同一段提示词**的同名字段；
  b. `mom30m` 锚点只有下界（`t0 - t >= 25min`）、没有上界，而它读的是**最近两个
     日期文件** → 每个交易日开盘头半小时，锚点落在昨天 15:59，把**隔夜跳空**
     当成「30 分钟动量」报给模型。

本文件把 (b) 钉成反向用例：喂进「昨天一整天 + 今天头 3 根」，新实现必须判
样本不足返回 {}，而旧实现会算出一个跨夜的数。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import minute_feats as MF  # noqa: E402

BJ = timezone(timedelta(hours=8))
DAY1 = datetime(2026, 9, 21, tzinfo=BJ)      # 周一（前一交易日）
DAY2 = datetime(2026, 9, 22, tzinfo=BJ)      # 周二


def _row(day: datetime, hh: int, mm: int, px: float, vol: float, ss: int = 0) -> dict:
    t = day.replace(hour=hh, minute=mm, second=ss)
    return {"ts": t.isoformat(), "code": "600176.SH", "px": px, "vol": vol}


def _series(day: datetime, start_hhmm: tuple, n: int, px0: float = 10.0,
            step_px: float = 0.01, vol0: float = 10_000, step_vol: float = 1_000,
            every_sec: int = 300) -> list:
    """从 start 起每 every_sec 秒一根，共 n 根。"""
    hh, mm = start_hhmm
    t0 = day.replace(hour=hh, minute=mm)
    return [{"ts": (t0 + timedelta(seconds=every_sec * i)).isoformat(),
             "code": "600176.SH",
             "px": px0 + step_px * i,
             "vol": vol0 + step_vol * i}
            for i in range(n)]


# ---------- compute：与 AiData 公式逐位一致 ----------

def test_compute_matches_aidata_formula_verbatim():
    """期望值是照 live_l2_capture.py:470-478 手算的，含 round 位数与**总体**标准差。"""
    closes = [10.0, 10.1, 10.05, 10.2, 10.15, 10.3]
    vols = [100.0, 120.0, 90.0, 150.0, 110.0, 200.0]
    out = MF.compute(closes, vols)

    assert out["mom30m"] == round((10.3 / 10.0 - 1) * 100, 3)
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    avg = sum(rets) / len(rets)
    assert out["vol5m"] == round((sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 3)
    assert out["vol_ratio"] == round(200.0 / (sum(vols) / len(vols)), 2)


def test_vol5m_is_std_not_range():
    """旧实现用极差 = max-min。这里构造一个「极差/标准差」比值悬殊的序列钉死区别。"""
    closes = [10.0, 10.5, 10.0, 10.5, 10.0, 10.5, 10.0]
    out = MF.compute(closes, [1.0] * 7)
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    spread = (max(rets) - min(rets)) * 100
    assert abs(out["vol5m"] - spread) > 1.0, "vol5m 退化成了极差口径"


def test_mom30m_needs_six_bars_and_positive_base():
    assert "mom30m" not in MF.compute([10.0] * 5, [1.0] * 5)
    assert "mom30m" not in MF.compute([0.0] + [10.0] * 5, [1.0] * 6)


def test_vol_ratio_absent_when_average_volume_zero():
    out = MF.compute([10.0] * 6, [0.0] * 6)
    assert "vol_ratio" not in out, "均量为 0 时不该产出（更不该填 None 占位）"


# ---------- resample：重采样成 5 分钟 bar ----------

def test_resample_takes_last_sample_of_each_bucket():
    """9:30 与 9:34 同属 :30 那根 bar（交易所按 :00/:05/… 切），9:35 起是下一根。"""
    rows = [_row(DAY2, 9, 30, 10.0, 100), _row(DAY2, 9, 34, 10.5, 120),
            _row(DAY2, 9, 35, 11.0, 200)]
    bars = MF.resample(rows)
    assert [b["px"] for b in bars] == [10.5, 11.0], "同一 bar 内应取最后一口"


def test_resample_ignores_out_of_session_samples():
    """集合竞价/盘后的样本不得混进 bar（9:25 与 15:05 不在连续竞价时段内）。"""
    rows = [_row(DAY2, 9, 25, 99.0, 1), _row(DAY2, 9, 30, 10.0, 100),
            _row(DAY2, 15, 5, 88.0, 999)]
    assert [b["px"] for b in MF.resample(rows)] == [10.0]


def test_resample_ignores_nonpositive_price():
    rows = [_row(DAY2, 9, 30, 0.0, 100), _row(DAY2, 9, 30, 10.0, 100, ss=20)]
    assert [b["px"] for b in MF.resample(rows)] == [10.0]


# ---------- 反向用例：跨夜样本绝不参与 ----------

def test_overnight_samples_never_become_momentum():
    """核心回归钉：昨天满窗 + 今天只有 3 根 → 30 分钟口径必须整体缺失。

    旧实现在这个输入上会给出 mom30m ≈ (今天价/昨天 15:00 价 - 1)*100 —— 一个
    被标成「30 分钟动量」的隔夜跳空，且每天开盘头半小时必然发生（实测 7.41%）。
    """
    rows = _series(DAY1, (13, 0), 27, px0=10.0)          # 昨天 13:00-15:10 满窗
    rows += _series(DAY2, (9, 30), 3, px0=11.0)          # 今天只有 3 根
    out = MF.from_rows(rows)
    assert "mom30m" not in out and "vol5m" not in out, f"跨夜样本被算成了动量: {out}"
    # 短窗口字段仍可给（今天自己有 3 根连续 bar）—— 逐字段独立缺失，不是一刀切
    assert out.get("mom5m") is not None


def test_overnight_does_not_shift_the_window_when_today_is_full():
    """今天满窗时，昨天的样本也不得进入窗口（否则 mom30m 会被昨天拉长成隔日）。"""
    rows = _series(DAY1, (13, 0), 27, px0=10.0)
    rows += _series(DAY2, (9, 30), 8, px0=20.0)
    out = MF.from_rows(rows)
    assert out.get("bars") == 8
    closes = [20.0 + 0.01 * i for i in range(8)]
    assert out["mom30m"] == round((closes[-1] / closes[-6] - 1) * 100, 3)


def test_lunch_break_gap_is_rejected():
    """上午尾盘 + 下午开盘：间隔约 90 分钟 → 连续段断开，上午那段不参与。"""
    rows = _series(DAY2, (11, 0), 6)                       # 11:00-11:25
    rows += _series(DAY2, (13, 0), 4)                      # 13:00 起只有 4 根
    out = MF.from_rows(rows)
    assert "mom30m" not in out, "午休缺口被当成了连续 bar"


def test_ten_minute_cadence_is_rejected():
    """10 分钟间隔即便凑够 6 根也不是 30 分钟动量 —— 这正是「绝不凑数」。

    也一并证明回退链不留后门：b5 缺失时回退上一根 bar，同样被间隔闸挡住。
    拿到 6 行却只连成 1 根合规 bar，**必须留下显式标记**而不是空 dict ——
    「还没开盘」和「间隔违规」在下游长得一模一样。
    """
    out = MF.from_rows(_series(DAY2, (9, 30), 6, every_sec=600))
    assert out == {"ok": False, "reason": "insufficient_history", "have": 1, "need": 6}, (
        f"10 分钟节奏下不该产出任何因子，但必须报出降级原因：{out}")


def test_insufficient_history_is_marked_not_silently_empty():
    """5 根 → 长窗因子整体缺失，但结果里带着 have/need（可计数、可告警）。"""
    out = MF.from_rows(_series(DAY2, (9, 30), 5))
    assert out["ok"] is False and out["reason"] == "insufficient_history"
    assert out["have"] == 5 and out["need"] == MF.MIN_BARS


def test_no_rows_at_all_is_still_empty():
    """一行都没有 = 调用方没给数据（不是「数据不够」）→ 不编造标记。

    这一层缺失由轮级降级戳（capabilities.minute_k）表达，不在这里重复。
    """
    assert MF.from_bars([]) == {}
    assert MF.from_rows([]) == {}


def test_missing_bucket_breaks_the_run():
    """cron 漏一轮（少一根 bar，间隔 10 分钟）→ 连续段在缺口处截断。"""
    rows = _series(DAY2, (9, 30), 5)                       # 9:30-9:50
    rows += _series(DAY2, (10, 0), 5)                      # 跳过 9:55
    out = MF.from_rows(rows)
    assert "mom30m" not in out, "缺口两侧被连成了一段"
    assert out.get("bars") is None


# ---------- 短窗口字段（mom5m / inout） ----------

def test_mom5m_prefers_bridge_before5min():
    """b5 = 桥的 Before5MinNow。有它就用它 —— 单轮内即可得，不受漏轮影响。"""
    rows = _series(DAY2, (9, 30), 3, px0=10.0, step_px=0.10)   # 末根 10.20
    rows[-1]["b5"] = "10.00"
    assert MF.from_rows(rows)["mom5m"] == round((10.20 / 10.00 - 1) * 100, 3)


def test_mom5m_bar_fallback_uses_previous_bar():
    rows = _series(DAY2, (9, 30), 3, px0=10.0, step_px=0.10)
    out = MF.from_rows(rows)                                    # 无 b5 → 回退上一根 bar
    assert out["mom5m"] == round((10.20 / 10.10 - 1) * 100, 3)


def test_mom5m_absent_when_only_one_bar():
    assert "mom5m" not in MF.from_rows(_series(DAY2, (9, 30), 1))


def test_inout_from_latest_sample():
    rows = _series(DAY2, (9, 30), 3)
    rows[-1]["inside"], rows[-1]["outside"] = 300, 700
    assert MF.from_rows(rows)["inout"] == round((300 - 700) / 1000, 3)


def test_inout_absent_when_no_depth():
    assert "inout" not in MF.from_rows(_series(DAY2, (9, 30), 3))


# ---------- 显式缺失 ----------

def test_insufficient_history_returns_no_long_window_fields():
    """不足 6 根 → 30 分钟口径整体缺失，不能给半个窗口的值（短窗口字段不受影响）。"""
    out = MF.from_rows(_series(DAY2, (9, 30), 5))
    assert "mom30m" not in out and "vol5m" not in out and "vol_ratio" not in out


def test_no_rows_returns_empty():
    assert MF.from_rows([]) == {}


# ---------- 量比：累计量差分 ----------

def test_volume_ratio_comes_from_cumulative_differencing():
    """per-bar 量由累计量差分得到；末根 bar 的量与预期逐位一致。"""
    bars = _series(DAY2, (9, 30), 7, vol0=10_000, step_vol=1_000)
    out = MF.from_bars(MF.resample(bars))
    per_bar = [1_000] * 6                       # 6 个差分，恒定 1000
    assert out["vol_ratio"] == round(per_bar[-1] / (sum(per_bar) / len(per_bar)), 2) == 1.0


def test_volume_rollback_never_fabricates_ratio():
    """累计量倒退（跨日重置/抓取错位）→ vol_ratio 显式不出现，而不是当 0 算。"""
    bars = _series(DAY2, (9, 30), 7, vol0=50_000, step_vol=1_000)
    bars[4]["vol"] = 10_000                     # 倒退
    out = MF.from_bars(MF.resample(bars))
    assert "vol_ratio" not in out
    assert "mom30m" in out, "量序列坏掉不该连累价序列的因子"


def test_no_bars_when_resample_empty():
    assert MF.from_bars([]) == {}


# ---------- 读盘 ----------

def test_load_code_rows_filters_code_and_survives_bad_json(tmp_path):
    f = tmp_path / "2026-09-22.jsonl"
    f.write_text("\n".join([
        "{ 坏行",
        json.dumps({"ts": DAY2.replace(hour=9, minute=30).isoformat(),
                    "code": "600176.SH", "px": "10.0", "vol": "100"}),
        json.dumps({"ts": DAY2.replace(hour=9, minute=30).isoformat(),
                    "code": "000028.SZ", "px": "20.0", "vol": "100"}),
        json.dumps({"ts": DAY2.replace(hour=9, minute=31).isoformat(),
                    "code": "600176.SH", "px": "0", "vol": "100"}),
    ]), encoding="utf-8")
    rows = MF.load_code_rows("600176.SH", tmp_path)
    assert len(rows) == 1, "应只留本票有效价的行"


def test_load_code_rows_missing_dir_is_empty(tmp_path):
    assert MF.load_code_rows("600176.SH", tmp_path / "nope") == []


@pytest.mark.parametrize("every_sec", [20, 60, 300])
def test_sampling_density_does_not_change_the_result(every_sec):
    """20 秒 / 1 分钟 / 5 分钟采样喂的是同一口径：同一条价格轨迹 → 同一个值。

    这是「换数据源不换口径」的机械证据 —— 采样更密只是让锚点更贴近真实 bar 收口。
    """
    bars = _series(DAY2, (9, 30), 7, every_sec=300)
    reference = MF.from_bars(MF.resample(bars))
    dense = []
    for b in bars:
        t = datetime.fromisoformat(b["ts"])
        for k in range(0, 300, every_sec):
            dense.append({"ts": (t + timedelta(seconds=k)).isoformat(),
                          "code": b["code"], "px": b["px"], "vol": b["vol"]})
    out = MF.from_rows(dense)
    # 只比 5 分钟 bar 口径那几项：mom5m/inout 只有原始样本才有，from_bars 本就不产；
    # last_ts 是**元数据不是因子**，它本就该随采样密度变 —— 一个 5 分钟桶里采得越密，
    # 桶内最后一口越靠近收口（20s 采到 :04:40，5min 只采到 :00:00）。它记录的是
    # 「最新的证据是什么时候」，正是新鲜度判据要的那个量，不该被压成桶起点。
    skip = ("mom5m", "inout", "last_ts")
    bar_derived = {k: v for k, v in out.items() if k not in skip}
    assert bar_derived == {k: v for k, v in reference.items() if k not in skip}


# ---------- 窗口：固定 12 根，不是「自开盘以来」 ----------

def _bars(start_min: int, n: int, px0: float = 10.0, step_px: float = 0.01,
          cum0: float = 10_000.0, step_vol: float = 1_000.0) -> list:
    """直接造 bar（跳过 resample），用于只考 `from_bars` 的窗口口径。"""
    return [{"start": start_min + 5 * i,
             "ts": DAY2 + timedelta(minutes=start_min + 5 * i),
             "px": px0 + step_px * i,
             "cum_vol": cum0 + step_vol * i}
            for i in range(n)]


class TestWindowIsFixedNotSinceOpen:
    """`vol5m`/`vol_ratio` 此前是把**自开盘以来所有合规 bar** 全喂进 compute：
    `_trailing_run` 从最新往回走，只在间隔超限时停 —— 交易日里那等于「一路到开盘」。

    于是同名字段的含义随**当日已过去的时长**漂移（实测同一段尾部 6 根 bar：
    18 根窗 vol5m=2.641、6 根窗 vol5m=4.782，差 1.8 倍），而提示词把它渲染成
    「5min波动」。AiData 原路径是 `count=12` 的定窗（11 个收益率）。本类钉死定窗。
    """

    def test_extra_session_history_does_not_change_bar_derived_fields(self):
        long_run = _bars(540, 18)                                  # 09:00 起 18 根
        tail = _bars(600, 6, px0=10.0 + 0.01 * 12)                 # 同一条轨迹的最后 6 根
        full, short = MF.from_bars(long_run), MF.from_bars(tail)
        for k in ("mom30m", "vol5m", "vol_ratio"):
            assert full.get(k) == short.get(k), (
                f"{k} 随当日已过去的时长漂移：18根窗给 {full.get(k)}、6根窗给 {short.get(k)}"
                "——同名字段不是同一个量")

    def test_window_is_capped_at_the_aidata_bar_count(self):
        out = MF.from_bars(_bars(540, 18))
        assert out["bars"] == MF.AIDATA_BARS, (
            f"窗口没封顶：bars={out['bars']}（AiData 原路径是 count=12）")

    def test_shorter_session_still_reports_its_actual_window(self):
        """开盘不久只有 7 根时，窗口就是 7 根（不足 12 不补齐，但也不是全历史）。"""
        assert MF.from_bars(_bars(570, 7))["bars"] == 7

    def test_six_bars_still_yield_a_volume_ratio(self):
        """量序列天然比价序列短一根（首根 bar 的量不可知），但门槛应跟**bar 数**走。

        旧写法 `len(vols) >= MIN_BARS` 实际要求 7 根 bar，于是 6 根时 vol_ratio
        静默消失，而同一份产物里 mom30m/vol5m 与「基于 6 根5min」都在。
        """
        out = MF.from_bars(_bars(600, 6))
        assert "mom30m" in out and "vol5m" in out
        assert "vol_ratio" in out, f"6 根 bar 时量比静默消失：{out}"


def test_a_missing_bucket_is_not_stitched_by_a_short_timestamp_gap():
    """整根 5 分钟桶没采到，也可能被 ts 闸放过去 —— 桶号才是硬判据。

    构造：09:30 那根桶的最后一个样本落在 09:34:50，**09:35 整根桶没有样本**，
    09:40 起恢复。相邻两根 bar 的 ts 差 = 310s ≤ 450 → 旧闸判「相邻」，于是窗口
    把缺口两侧缝成一段「连续」bar。后果不是少一根，而是 `vol5m`/`vol_ratio`
    这两个**全窗**统计量被缝进一段实际跨 10 分钟的市场时间（12 根窗最多可跨 ~82
    分钟），而提示词把它渲染成「5min波动」。
    """
    t0 = DAY2.replace(hour=9, minute=40)
    bars = [{"start": 570, "ts": DAY2.replace(hour=9, minute=34, second=50),
             "px": 10.0, "cum_vol": 10_000.0}]
    bars += [{"start": 580 + 5 * i, "ts": t0 + timedelta(minutes=5 * i),
              "px": 10.0 + 0.01 * i, "cum_vol": 11_000.0 + 1_000 * i}
             for i in range(6)]                       # 09:40-10:05
    out = MF.from_bars(bars)
    assert out["bars"] == 6, f"缺口被缝成了连续窗口：{out}"
    assert MF._parse_ts(out["last_ts"]) == DAY2.replace(hour=10, minute=5)


class TestPerSymbolFreshness:
    """新鲜度的颗粒度必须是**单票**，不是「这个目录有人在写」。

    `latest_sample_ts` 是全局探针：它扫最近两个日期文件、**不按 code 过滤**。所以
    只要还有任何一只票在采，它就报「20 秒新鲜」——而某只票可能早就停了。
    2026-09-22 实测（不是我构造的）：`logs/min_snapshots/2026-09-22.jsonl` 里
    `688591.SH` 最后一条 13:56:42，而全文件最后一条 14:59:42（来自别的票），
    单票停了 **63 分钟**，全局探针全程新鲜。
    """

    def test_from_bars_reports_the_sample_time_it_used(self):
        out = MF.from_bars(_bars(600, 6))
        assert MF._parse_ts(out["last_ts"]) == DAY2 + timedelta(minutes=625), (
            "产物里必须带「这个窗口的最后一个样本是什么时候」")

    def test_window_older_than_the_threshold_is_not_current(self):
        out = MF.from_bars(_bars(540, 12))                 # 窗口止于 09:55
        assert MF.fresh_or_stale(out, DAY2.replace(hour=10, minute=30))["ok"] is False, (
            "35 分钟前的窗口被当成了此刻")

    def test_fresh_window_passes_through(self):
        out = MF.from_bars(_bars(600, 6))                  # 止于 10:25
        assert MF.fresh_or_stale(out, DAY2.replace(hour=10, minute=26)) == out

    def test_off_session_does_not_invent_staleness(self):
        """盘后/周末：最近的一段 bar 本来就该是「上一场」，拿年龄判它会全是假降级。"""
        out = MF.from_bars(_bars(600, 6))
        for t in (DAY2.replace(hour=20, minute=0), DAY2.replace(hour=9, minute=0)):
            assert MF.fresh_or_stale(out, t) == out, f"{t} 被当成了降级"

    def test_a_window_without_a_sample_time_cannot_prove_currency(self):
        out = dict(MF.from_bars(_bars(600, 6)), last_ts="不是时间")
        got = MF.fresh_or_stale(out, DAY2.replace(hour=10, minute=26))
        assert got["ok"] is False and got["age_sec"] is None

    def test_marker_is_returned_unchanged(self):
        mark = {"ok": False, "reason": "insufficient_history", "have": 1, "need": 6}
        assert MF.fresh_or_stale(mark, DAY2.replace(hour=10, minute=30)) == mark
