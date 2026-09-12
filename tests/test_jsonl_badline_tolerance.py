"""坏行容错：一行坏 JSON 只丢那一行，不得整段静默失效（2026-09-12 批 13）。

批 10 把「撕裂的**多字节**字符让整文件抛 UnicodeDecodeError」这一类收敛掉了（解码层）；
本文件收的是**同一族的第二层**：解码没问题、`json.loads` 逐行失败时，读取方用
「整表推导式 + 一个总 try」或「整个 for 循环套一个 try」的形态 → **一行坏 = 整段结果
变空**，而且多数地方连 print 都没有（静默）。两层的正确形状是同一条：**逐行 try，
坏行计数 + 留痕，其余行照常使用**。

盘上实测（2026-09-12）：`logs/analysis_jobs.jsonl`（1182 B / 466 个非 ASCII 字节）、
`logs/ops_cmds.jsonl`（404 B / 72 个非 ASCII 字节）都是**含中文 + 追加写（非原子）**的
队列文件 → 既可能留下合法 UTF-8 的坏行（写一半），也可能撕裂在多字节字符中间。

站点与后果：
  A. scripts/analysis_trigger_worker.py:33-38 `load()` 整表推导式
     → 一行坏 → 返回 [] → 「立即分析」按钮**永久静默失效**（cron 每分钟读，永远读不到
     pending；任务行还在文件里，人只会看到「点了没反应」）
  B. scripts/log_cleanup.py:45-56 连 try 都没有 → 一行坏 → **每周日 05:00 cron 必崩**
     （crontab 已核实：`0 5 * * 0`）
  C. scripts/ops_worker.py:22-26 整表推导式 → 一行坏 → **所有 ops 指令（含重启桥）
     集体不执行**且无输出
  D. scripts/post_review.py:82 轮次摘要整表推导式 → 一行坏 → 复盘当日「分析轮次摘要」
     整段变空（该文件与批 10 的 B 类同族：模型对话流，含中文、追加写）
  E. scripts/post_review.py:96 / daily_report_agent.py:143 / :157 三处净值读取
     整个 for 套一个 try → 一行坏 → 当日净值/首末/系统总净值**整段消失**
  F. scripts/daily_report_agent.py:125-133 成交循环的 try 在 for **外面**
     → 坏行之后的该文件剩余成交行**全部丢弃**（不只是坏行本身）

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_jsonl_badline_tolerance.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import analysis_trigger_worker as ATW  # noqa: E402
import daily_report_agent as D  # noqa: E402
import live_trade_picks as TP  # noqa: E402
import log_cleanup as LC  # noqa: E402
import ops_worker as O  # noqa: E402
import post_review as PR  # noqa: E402

# 合法 UTF-8 的坏行（写到一半被杀）+ 撕裂多字节字符的行（「止」的 UTF-8 是 3 字节）
BAD_UTF8 = b'{"id": "job-half", "note": "\xe6\x96\xad\xe5\xa4'
TORN = b'{"id": "job-torn", "note": "\xe6\xad'

TODAY = datetime.now().strftime("%Y-%m-%d")
TODAY8 = TODAY.replace("-", "")


def _write(path: Path, rows: list, garbage: bytes = b"", before: bool = False) -> Path:
    """合法行（ensure_ascii=False，与写入侧同格式）+ 坏行字节。

    before=True：坏行写在**合法行之前**——整表中止型读取方（try 在循环外）会因此
    丢掉后面所有合法行；这种位置才有区分度（坏行在末尾时，异常前收到的值仍会保留）。
    """
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    payload = garbage + body.encode("utf-8") if before else body.encode("utf-8") + garbage
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


# ------------------------------------------------------- 前提：坏行的两种形态

def test_premise_bad_lines_are_valid_utf8_but_unparseable():
    """前提钉住：两种坏行都能解码（不是批 10 的解码层问题），但 json.loads 失败。"""
    for raw in (BAD_UTF8, TORN):
        text = raw.decode("utf-8", errors="replace")      # 解码这关能过
        with pytest.raises(json.JSONDecodeError):
            json.loads(text.strip())                      # 解析这关过不去


# ------------------------------------------------------- A. 分析任务队列

def test_trigger_worker_load_keeps_other_rows(tmp_path, monkeypatch, capsys):
    """A 一行坏 → 「立即分析」永久失效；其余合法任务必须照常返回且不算静默。"""
    monkeypatch.setattr(ATW, "JOBS", _write(
        tmp_path / "analysis_jobs.jsonl",
        [{"id": "job-ok-1", "status": "pending", "note": "手动分析"},
         {"id": "job-ok-2", "status": "done", "note": "已完成"}],
        BAD_UTF8 + TORN + b"\n"))

    rows = ATW.load()

    assert [r["id"] for r in rows] == ["job-ok-1", "job-ok-2"]
    assert "无法解析" in capsys.readouterr().out            # 坏行必须留痕，不许静默


def test_trigger_worker_load_tolerates_torn_tail(tmp_path, monkeypatch):
    """撕裂尾巴（批 10 族）同样只丢那一行——队列不许被一个坏字节清空。"""
    monkeypatch.setattr(ATW, "JOBS", _write(
        tmp_path / "analysis_jobs.jsonl",
        [{"id": "job-ok-1", "status": "pending", "note": "手动分析"}], TORN))

    assert [r["id"] for r in ATW.load()] == ["job-ok-1"]


def test_trigger_worker_load_missing_file_is_empty(tmp_path, monkeypatch):
    """对照：文件不存在仍是空队列（不是坏行路径）。"""
    monkeypatch.setattr(ATW, "JOBS", tmp_path / "nope.jsonl")

    assert ATW.load() == []


# ------------------------------------------------------- B. 每周日志治理 cron

def test_log_cleanup_bad_line_does_not_crash(tmp_path, monkeypatch, capsys):
    """B 一行坏 → 周日 05:00 cron 每周必崩；现在必须照常跑完，且保留 pending 任务、
    把坏行从文件里剔除（自我修复）。"""
    jf = _write(tmp_path / "logs" / "analysis_jobs.jsonl",
                [{"id": "job-pending", "status": "pending"}],
                BAD_UTF8 + TORN + b"\n")
    monkeypatch.setattr(LC, "ROOT", tmp_path)
    monkeypatch.setattr(LC, "TARGETS", [])                 # 不碰真实产物目录
    monkeypatch.setattr(sys, "argv", ["log_cleanup.py"])

    assert LC.main() == 0                                  # 旧实现：JSONDecodeError 穿出

    left = [json.loads(l) for l in jf.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["id"] for r in left] == ["job-pending"]       # pending 保住、坏行剔除
    assert "无法解析" in capsys.readouterr().out


def test_log_cleanup_dry_run_keeps_file(tmp_path, monkeypatch):
    """--dry 只报不写：坏行也不许被剔除（试运行零落盘）。"""
    jf = _write(tmp_path / "logs" / "analysis_jobs.jsonl",
                [{"id": "job-pending", "status": "pending"}], BAD_UTF8 + b"\n")
    before = jf.read_bytes()
    monkeypatch.setattr(LC, "ROOT", tmp_path)
    monkeypatch.setattr(LC, "TARGETS", [])
    monkeypatch.setattr(sys, "argv", ["log_cleanup.py", "--dry"])

    assert LC.main() == 0
    assert jf.read_bytes() == before


# ------------------------------------------------------- C. ops 指令队列

def test_ops_worker_bad_line_does_not_block_other_cmds(tmp_path, monkeypatch, capsys):
    """C 一行坏 → 全部 ops 指令（含重启桥）集体不执行；现在坏行跳过、其余照跑。"""
    jobs = _write(tmp_path / "ops_cmds.jsonl",
                  [{"id": "ops-1", "type": "bridge", "status": "pending"}],
                  BAD_UTF8 + TORN + b"\n")
    share = tmp_path / "share"
    monkeypatch.setattr(O, "JOBS", jobs)
    monkeypatch.setattr(O, "SHARE", share)

    assert O.run() == 0

    assert (share / "restart_bridge.flag").exists()         # 合法指令照样执行
    assert "无法解析" in capsys.readouterr().out
    assert "job-half" not in jobs.read_text(encoding="utf-8")  # 坏行随重写清掉


def test_ops_worker_only_bad_lines_returns_quietly(tmp_path, monkeypatch):
    """全是坏行 → 不执行任何指令、不例外、不重写文件（下一条新指令仍能被读到）。"""
    jobs = _write(tmp_path / "ops_cmds.jsonl", [], BAD_UTF8 + b"\n")
    monkeypatch.setattr(O, "JOBS", jobs)
    monkeypatch.setattr(O, "SHARE", tmp_path / "share")

    assert O.run() == 0
    assert b"job-half" in jobs.read_bytes()          # 不重写（留着坏行也只是被跳过）


# ------------------------------------------------------- D/E. 复盘事实

def test_post_review_rounds_survive_bad_line(tmp_path, monkeypatch):
    """D 一行坏 → 复盘「当日分析轮次摘要」整段变空（LLM 复盘看不到当天发生了什么）。"""
    monkeypatch.setattr(PR, "ROOT", tmp_path)
    lf = _write(tmp_path / "data" / "agent_data_astock" / "a1" / "log" / TODAY / "log.jsonl",
                [{"timestamp": f"{TODAY}T09:40:00",
                  "new_messages": [{"role": "assistant", "content": "早盘观察：指数低开"}]}],
                BAD_UTF8 + TORN + b"\n")

    out = PR.collect_facts("a1", TODAY)

    assert "早盘观察：指数低开" in out
    assert lf.is_file()


def test_post_review_equity_survives_bad_line(tmp_path, monkeypatch):
    """E 净值整段 try → 坏行在**前面**时后续合法行全丢，当日净值首末/高低/采样点
    整段消失（整表中止）。"""
    monkeypatch.setattr(PR, "ROOT", tmp_path)
    _write(tmp_path / "logs" / "live_equity.jsonl",
           [{"agent": "a1", "date": TODAY, "value": 100000.0},
            {"agent": "a1", "date": TODAY, "value": 101000.0}],
           b'{"agent": "a1", "date": "' + TODAY.encode() + b'", "value"\n', before=True)

    out = PR.collect_facts("a1", TODAY)

    assert "【当日净值】开盘后首 100000.0 · 末 101000.0" in out


# ------------------------------------------------------- F. 系统日报

def _agent_daily(monkeypatch, tmp_path, rows: list, garbage: bytes,
                 before: bool = False) -> dict:
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(TP, "enabled_agents", lambda: ["a1"])
    _write(tmp_path / "logs" / f"live_trade_{TODAY8}.jsonl", rows, garbage, before=before)
    return D.collect(TODAY)["agents"][0]


def test_daily_report_fills_after_bad_line_still_counted(tmp_path, monkeypatch):
    """F 成交循环的 try 在 for 外面 → 坏行**之后**的成交行全丢（不只是坏行）。
    单测钉的是位置：坏行夹在两笔真成交中间，两笔都要入账。"""
    rec = _agent_daily(monkeypatch, tmp_path, [
        {"agent": "a1", "ts": f"{TODAY}T10:00:00", "side": "sell", "code": "600000.SH",
         "mode": "fill_confirm", "fill": {"filled_volume": 100}},
    ], b'not-a-json-line\n' + json.dumps(
        {"agent": "a1", "ts": f"{TODAY}T10:30:00", "side": "sell", "code": "600000.SH",
         "mode": "fill_confirm", "fill": {"filled_volume": 200}}).encode() + b"\n")

    assert rec["fills"] == 2


def test_daily_report_nav_survives_bad_line(tmp_path, monkeypatch):
    """E 单 agent 净值整段 try → 一行坏 → 首末净值双双为 None（日报显示运行异常）。"""
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(TP, "enabled_agents", lambda: ["a1"])
    _write(tmp_path / "logs" / "live_equity.jsonl",
           [{"agent": "a1", "date": TODAY, "value": 100000.0},
            {"agent": "a1", "date": TODAY, "value": 99000.0}],
           b'{"agent": "a1", "date": "' + TODAY.encode() + b'", "va\n', before=True)

    rec = D.collect(TODAY)["agents"][0]

    assert (rec["nav_first"], rec["nav_last"]) == (100000.0, 99000.0)


def test_daily_report_system_nav_survives_bad_line(tmp_path, monkeypatch):
    """E 系统总净值同一形态（agent 为 None 的行）。"""
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(TP, "enabled_agents", lambda: [])
    _write(tmp_path / "logs" / "live_equity.jsonl",
           [{"agent": None, "date": TODAY, "value": 300000.0},
            {"agent": None, "date": TODAY, "value": 299000.0}],
           b'{"agent": null, "date": "' + TODAY.encode() + b'", "valu\n', before=True)

    nav = D.collect(TODAY)["system"]["total_nav"]

    assert nav == {"first": 300000.0, "last": 299000.0}
