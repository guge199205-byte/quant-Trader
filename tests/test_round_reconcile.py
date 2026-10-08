"""round_reconcile（整点轮死进程对账）单元测试 —— 2026-09-21 磁盘满事故后加固。

事故实录（2026-09-21）：14:45 尾盘决策轮在 ENOSPC 中途中止——record_equity 与
finally 里的 failed 心跳**都**写不进去（原子 tmp+replace 失败 = 状态文件原样停在
running=true + 死 pid）。alert_checks.round_stuck 只按心跳新鲜度判定，于是 alerts.log
每 5 分钟一条、推送层每 2 小时重推一次，一直报到次日新轮覆盖；且文案「后续 cron 轮
会被锁跳过」对死进程是假陈述（flock 随进程终止自动释放，死轮并不阻塞下一轮）。

口径：死轮 → 报一次 + 自动标记 failed（幂等）；活进程 → 不动（真卡死交给
round_stuck）；pid 无从判定 → 保守不动；文件坏/空（磁盘满时 truncate 写坏的
签名）→ 告警 + 隔离 + 重置。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import round_reconcile as rc  # noqa: E402

NOW = datetime(2026, 9, 21, 22, 55, tzinfo=rc.BJ)
DOC = {"pid": 1613420, "start_ts": "2026-09-21T14:45:02.118504+08:00",
       "updated_ts": "2026-09-21T14:45:05.007484+08:00",
       "phase": "agent:deepseek-v4-flash", "running": True}


@pytest.fixture
def round_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "now_bj", lambda: NOW)
    return tmp_path / "live_analysis_round.json"


def _seed(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_dead_pid_alerts_once_then_finalizes(round_file, monkeypatch, capsys):
    _seed(round_file, DOC)
    monkeypatch.setattr(rc, "pid_state", lambda pid: "dead")

    assert rc.main([str(round_file)]) == 0
    out = capsys.readouterr().out
    assert "进程已死" in out and "1613420" in out and "只发一次" in out

    doc = json.loads(round_file.read_text(encoding="utf-8"))
    assert doc["running"] is False and doc["phase"] == "failed"
    assert doc["pid"] == 1613420                                    # 追溯字段保留
    assert doc["start_ts"] == DOC["start_ts"]
    assert doc["updated_ts"].startswith("2026-09-21T22:55")

    # 幂等：下一次对账（下一轮 alert.sh）看到 running=false → 静默
    assert rc.main([str(round_file)]) == 0
    assert capsys.readouterr().out == ""
    assert "failed" in round_file.read_text(encoding="utf-8")


def test_alive_pid_left_to_round_stuck(round_file, monkeypatch, capsys):
    _seed(round_file, DOC)
    monkeypatch.setattr(rc, "pid_state", lambda pid: "alive")

    assert rc.main([str(round_file)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(round_file.read_text(encoding="utf-8")) == DOC


def test_unknown_pid_state_is_not_touched(round_file, monkeypatch, capsys):
    """pid 缺档/不可判（hidepid、内核线程）→ 保守不动，让 round_stuck 照旧报卡死。"""
    _seed(round_file, DOC)
    monkeypatch.setattr(rc, "pid_state", lambda pid: "unknown")

    assert rc.main([str(round_file)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(round_file.read_text(encoding="utf-8")) == DOC


def test_not_running_is_silent(round_file, monkeypatch, capsys):
    _seed(round_file, {**DOC, "running": False, "phase": "done"})
    monkeypatch.setattr(rc, "pid_state",
                        lambda pid: (_ for _ in ()).throw(AssertionError("不该探活")))

    assert rc.main([str(round_file)]) == 0
    assert capsys.readouterr().out == ""


def test_missing_file_silent_and_not_created(round_file, capsys):
    assert rc.main([str(round_file)]) == 0
    assert capsys.readouterr().out == ""
    assert not round_file.exists()


@pytest.mark.parametrize("bad", ["", "{not json", "[]", '"x"'])
def test_corrupt_file_quarantined_and_reset(round_file, monkeypatch, capsys, bad):
    """0 字节/坏 JSON/非对象 = 上次中断写坏的签名（今天 premarket_probe_state.json
    就是被 ENOSPC 截成 0 字节的）→ 隔离原件 + 重置 failed，绝不静默假装无事。"""
    monkeypatch.setattr(rc, "pid_state", lambda pid: "dead")
    round_file.write_text(bad, encoding="utf-8")

    assert rc.main([str(round_file)]) == 0
    out = capsys.readouterr().out
    assert "损坏" in out and "已隔离" in out

    q = list(round_file.parent.glob("live_analysis_round.json.corrupt.*"))
    assert len(q) == 1 and q[0].read_text(encoding="utf-8") == bad   # 原件留证

    doc = json.loads(round_file.read_text(encoding="utf-8"))
    assert doc["running"] is False and doc["phase"] == "failed"
    assert rc.main([str(round_file)]) == 0                           # 幂等
    assert capsys.readouterr().out == ""


def test_pid_state_real_proc_semantics(monkeypatch):
    """/proc 语义：cmdline 含特征串才算我们的轮；pid 被复用/不存在都判 dead。"""
    assert rc.pid_state("not-a-pid") == "unknown"      # 非数字 → unknown
    # 活着的非轮进程（pid 复用场景）：cmdline 不含 live_hourly_analysis → dead。
    # 注意 fork→exec 窗口内 cmdline 瞬时为空（实测首读约半数为空）→ 先轮询等 exec。
    child = subprocess.Popen(["sleep", "30"])
    try:
        for _ in range(300):
            if rc.pid_state(child.pid) != "unknown":
                break
            time.sleep(0.01)
        assert rc.pid_state(child.pid) == "dead"
        assert rc.pid_state(child.pid, marker="sleep") == "alive"
    finally:
        child.kill()
        child.wait()
    # 已死的 pid（本进程的子进程，wait 后必不存在）
    gone = subprocess.Popen(["true"])
    gone.wait()
    assert rc.pid_state(gone.pid) == "dead"
    assert rc.pid_state(None) == "unknown"
    assert rc.pid_state(0) == "unknown"
    assert rc.pid_state(sys.maxsize) == "dead"         # 远超 pid_max → /proc 无此目录
