"""rt_probe 连续失败计数单测（scripts/rt_probe.py）。

背景：fuyao 一天十几次单点 DOWN，每次 5-10 分钟自愈。alert.sh 若「一次失败就报」
会刷屏，若「只看当次」又不报——两个极端都错过真故障。这里给每路加
`fail_streak` / `first_fail_ts`，告警侧按「连续 ≥2 次」去抖。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_rt_probe_streak.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rt_probe as R  # noqa: E402


def _stub(monkeypatch, tmp_path, *, bridge_ok=True, fuyao_ok=True):
    """把 5 路探针与输出路径都换成假的（不打真网络、不写真看板）。"""
    monkeypatch.setattr(R, "OUT", tmp_path / "rt_status.json")
    monkeypatch.setattr(R, "probe_bridge", lambda: {
        "ok": bridge_ok, "error": None if bridge_ok else "Connection refused"})
    monkeypatch.setattr(R, "probe_fuyao", lambda: {
        "ok": fuyao_ok, "error": None if fuyao_ok else "上游 4001 限频"})
    monkeypatch.setattr(R, "probe_tencent", lambda: {"ok": True})
    monkeypatch.setattr(R, "probe_aidata", lambda: {"ok": True})
    monkeypatch.setattr(R, "probe_quantdb", lambda: {"ok": True})


def _board(tmp_path):
    return json.loads((tmp_path / "rt_status.json").read_text(encoding="utf-8"))


def test_fail_streak_increments_and_keeps_first_fail_ts(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fuyao_ok=False)

    R.main()
    first = _board(tmp_path)["fuyao"]
    assert first["fail_streak"] == 1
    ts0 = first["first_fail_ts"]

    R.main()
    R.main()
    fuyao = _board(tmp_path)["fuyao"]

    assert fuyao["fail_streak"] == 3
    assert fuyao["first_fail_ts"] == ts0          # 首次失败时刻不被后续覆盖


def test_recovery_resets_streak_and_clears_first_fail(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fuyao_ok=False)
    R.main()
    _stub(monkeypatch, tmp_path, fuyao_ok=True)
    R.main()
    recovered = _board(tmp_path)["fuyao"]

    assert recovered["fail_streak"] == 0
    assert "first_fail_ts" not in recovered

    _stub(monkeypatch, tmp_path, fuyao_ok=False)   # 再次掉线从 1 重新计
    R.main()

    assert _board(tmp_path)["fuyao"]["fail_streak"] == 1


def test_summary_line_carries_error_reason(tmp_path, monkeypatch, capsys):
    _stub(monkeypatch, tmp_path, bridge_ok=False, fuyao_ok=False)

    R.main()
    out = capsys.readouterr().out

    assert "桥=DOWN(1次/Connection refused)" in out
    assert "Fuyao=DOWN(1次/上游 4001 限频)" in out
    assert "降级: bridge,fuyao" in out


def test_write_is_atomic_and_survives_corrupt_previous(tmp_path, monkeypatch):
    (tmp_path / "rt_status.json").write_text("{半截 JSON", encoding="utf-8")
    _stub(monkeypatch, tmp_path, fuyao_ok=False)

    R.main()

    assert _board(tmp_path)["fuyao"]["fail_streak"] == 1     # 旧文件坏 → 从头计
    assert not list(tmp_path.glob("*.tmp"))                  # 临时文件已 replace
