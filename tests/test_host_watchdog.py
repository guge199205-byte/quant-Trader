"""host_watchdog（主机存活看门狗，2026-09-18）单元测试：全程无网络。

背景：2026-09-18 主机盘中冻结/休眠数小时（哨兵/轮次/告警全停摆、恢复后零告警）。
覆盖：交易日白天/夜间阈值分流、重启/冻结判别、离线告警文案（含交易时段覆盖提示）、
首次运行建基线、推送失败不推进心跳（下分钟重试）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import host_watchdog as hw  # noqa: E402
import push_notify  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    hb = tmp_path / "host_heartbeat.json"
    gap = tmp_path / "host_gaps.jsonl"
    wf = tmp_path / "host_heartbeat.writefail.json"
    monkeypatch.setattr(hw, "HB_FILE", hb)
    monkeypatch.setattr(hw, "GAP_LOG", gap)
    monkeypatch.setattr(hw, "WF_FILE", wf)
    monkeypatch.setattr(hw, "boot_id", lambda: "BOOT-A")
    monkeypatch.setattr(sys, "argv", ["host_watchdog.py"])   # main() 解析 argv
    sent = []
    monkeypatch.setattr(push_notify, "notify",
                        lambda title, body: sent.append((title, body)))
    return {"hb": hb, "gap": gap, "wf": wf, "sent": sent, "monkeypatch": monkeypatch}


def _freeze_now(env, dt: datetime):
    env["monkeypatch"].setattr(hw, "now_bj", lambda: dt)


def _seed(env, ts: datetime, boot="BOOT-A"):
    env["hb"].write_text(json.dumps({"ts": ts.isoformat(), "boot_id": boot,
                                     "pid": 1}), encoding="utf-8")


DAY = datetime(2026, 9, 18, 10, 30, tzinfo=hw.BJ)      # 周五盘中
NIGHT = datetime(2026, 9, 18, 23, 30, tzinfo=hw.BJ)


def test_in_trading_daytime_boundaries():
    assert hw.in_trading_daytime(datetime(2026, 9, 18, 9, 0, tzinfo=hw.BJ)) is True
    assert hw.in_trading_daytime(datetime(2026, 9, 18, 15, 29, tzinfo=hw.BJ)) is True
    assert hw.in_trading_daytime(datetime(2026, 9, 18, 15, 30, tzinfo=hw.BJ)) is False
    assert hw.in_trading_daytime(datetime(2026, 9, 19, 10, 0, tzinfo=hw.BJ)) is False


def test_classify_freeze_vs_reboot_and_threshold():
    prev = {"ts": (DAY - timedelta(minutes=5)).isoformat(), "boot_id": "BOOT-A"}
    gap, mode, thresh = hw.classify_gap(prev, DAY, "BOOT-A")
    assert mode == "freeze" and gap == pytest.approx(5, abs=0.1) and thresh == hw.TRADING_GAP_MIN

    prev_same = {"ts": (DAY - timedelta(minutes=5)).isoformat(), "boot_id": "BOOT-A"}
    _, mode2, _ = hw.classify_gap(prev_same, DAY, "BOOT-B")
    assert mode2 == "reboot"

    prev_night = {"ts": (NIGHT - timedelta(minutes=40)).isoformat(), "boot_id": "BOOT-A"}
    _, _, thresh_night = hw.classify_gap(prev_night, NIGHT, "BOOT-A")
    assert thresh_night == hw.OFFHOURS_GAP_MIN


def test_render_flags_trading_coverage():
    prev = {"ts": (DAY - timedelta(minutes=200)).isoformat(), "boot_id": "BOOT-A"}
    msg = hw.render(200, "freeze", prev, DAY)
    assert "冻结" in msg and "200" in msg and "交易时段" in msg


def test_first_run_writes_baseline_no_alert(env):
    _freeze_now(env, DAY)
    assert hw.main() == 0
    assert env["sent"] == []
    doc = json.loads(env["hb"].read_text(encoding="utf-8"))
    assert doc["boot_id"] == "BOOT-A" and doc["ts"]


def test_gap_detected_alerts_once_then_quiet(env):
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=9))          # 9 分钟离线（盘中 → 阈值 3 分钟）

    assert hw.main() == 0
    assert len(env["sent"]) == 1
    assert "主机离线告警" in env["sent"][0][0] and "9" in env["sent"][0][1]
    gaps = env["gap"].read_text(encoding="utf-8").strip().splitlines()
    assert len(gaps) == 1 and json.loads(gaps[0])["gap_min"] == pytest.approx(9, abs=0.1)

    # 心跳已推进 → 下一分钟不再报
    assert hw.main() == 0
    assert len(env["sent"]) == 1


def test_night_short_gap_is_quiet(env):
    _freeze_now(env, NIGHT)
    _seed(env, NIGHT - timedelta(minutes=10))       # 夜间 10 分钟 < 30 分钟阈值

    assert hw.main() == 0
    assert env["sent"] == []
    assert not env["gap"].exists()


def test_push_failure_keeps_old_heartbeat_for_retry(env):
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=9))
    env["monkeypatch"].setattr(push_notify, "notify",
                               lambda *a, **k: (_ for _ in ()).throw(RuntimeError("QQ 挂")))
    old = env["hb"].read_text(encoding="utf-8")

    assert hw.main() == 0
    assert env["hb"].read_text(encoding="utf-8") == old      # 不推进 → 下分钟重试


# ---------- 心跳写失败留痕（2026-09-21 盘满事故） ----------
# 实录：ENOSPC 期间心跳（原子 tmp+replace）也写不进去 → gap 越涨越大，磁盘腾出后
# 17:45 推出「交易主机曾离线约 177 分钟」——而 journal 显示主机全程活着，是**心跳
# 自己**没写下去。留痕文件用「定长模板 + 同长原地覆写」：盘满到 100% 时新建/扩展
# 都会失败，但覆盖已分配的旧块可以成功。恢复后把 gap 归因为「心跳写入失败」，
# 不再谎报主机离线（也顺带纠正「覆盖了交易时段请核对止损」这类下游动作提示）。


def test_heartbeat_write_failure_leaves_marker_and_keeps_retry(env):
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=1))           # 无 gap，只走写失败路径
    env["monkeypatch"].setattr(
        hw, "write_heartbeat",
        lambda now, cur_boot: (_ for _ in ()).throw(
            OSError(28, "No space left on device")))
    old = env["hb"].read_text(encoding="utf-8")

    assert hw.main() == 0
    assert env["hb"].read_text(encoding="utf-8") == old      # 不推进 → 下分钟重试
    doc = json.loads(env["wf"].read_text(encoding="utf-8"))
    assert doc["ts"].startswith("2026-09-18T10:30") and "No space" in doc["err"]


def test_gap_with_writefail_marker_downgrades_verdict(env):
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=9))
    hw.write_fail_marker(OSError(28, "No space left on device"),
                         DAY - timedelta(minutes=5))     # 痕落窗口内

    assert hw.main() == 0
    assert len(env["sent"]) == 1
    title, body = env["sent"][0]
    assert "心跳写入失败" in title
    assert "无法据此判断" in body and "磁盘" in body
    rec = json.loads(env["gap"].read_text(encoding="utf-8").strip())
    assert rec["mode"] == "writefail" and rec["gap_min"] == pytest.approx(9, abs=0.1)

    # 心跳已推进 → 下一分钟安静（留痕不必清理：窗口外的旧痕不参与归因）
    assert hw.main() == 0
    assert len(env["sent"]) == 1


def test_stale_marker_outside_window_does_not_downgrade(env):
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=9))
    hw.write_fail_marker(OSError(28, "x"), DAY - timedelta(hours=3))   # 窗口外旧痕

    assert hw.main() == 0
    assert "主机离线告警" in env["sent"][0][0]
    rec = json.loads(env["gap"].read_text(encoding="utf-8").strip())
    assert rec["mode"] == "freeze"


def test_reboot_beats_writefail_marker(env):
    """重启判别优先：boot_id 变了就是重启，不因留痕改口径。"""
    _freeze_now(env, DAY)
    _seed(env, DAY - timedelta(minutes=9), boot="BOOT-OLD")
    hw.write_fail_marker(OSError(28, "x"), DAY - timedelta(minutes=5))

    assert hw.main() == 0
    assert "主机离线告警" in env["sent"][0][0]
    assert "重启" in env["sent"][0][1]
    rec = json.loads(env["gap"].read_text(encoding="utf-8").strip())
    assert rec["mode"] == "reboot"


def test_marker_template_preallocated_on_success(env):
    _freeze_now(env, DAY)
    assert hw.main() == 0
    assert env["wf"].stat().st_size == hw.WF_SIZE            # 定长：同长覆写才不占新块
    assert json.loads(env["wf"].read_text(encoding="utf-8"))["ts"] == ""
    # 失败留痕后长度不变（原地覆写的前提）
    hw.write_fail_marker(OSError(28, "No space left on device"), DAY)
    assert env["wf"].stat().st_size == hw.WF_SIZE
