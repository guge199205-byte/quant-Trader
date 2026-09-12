"""状态文件（logs/live_analysis_state.json）的并发写护栏。

2026-09-11 事故：每分钟 `--record-only` 采样在收尾时把**分析前读到的整表**
（`st_prev`）写回状态文件 → 同一时段由触发轮写入的 `last_trigger` /
`last_news_key` / `last_ts` 被陈旧值覆盖 → 20 分钟节流、60 分钟同向冷却、
新闻 ts 去重**集体失效**：当天跑了 78 轮完整分析（≈每 3 分钟一轮）、
三 agent 约 215 万 tokens。

修法：状态只有一个写入口 `update_state(updates, mutate=None)` —— 锁内
读-改-写 + 原子替换，调用方**只提交自己拥有的键**；`save_state(整表)` 删除，
"陈旧快照覆盖"这一类 bug 结构上不再可能。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_live_analysis_state.py -q
"""
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_hourly_analysis as H  # noqa: E402


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    """状态与锁都落 tmp（conftest 的兜底网也管，这里显式钉住断言面）。"""
    p = tmp_path / "live_analysis_state.json"
    monkeypatch.setattr(H, "STATE_PATH", p)
    monkeypatch.setattr(H, "STATE_LOCK_PATH", p.with_name(p.name + ".lock"))
    return p


def _write(state: Path, doc: dict) -> None:
    state.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_stale_snapshot_cannot_clobber(state):
    """09-11 实录的精确复现：采样进程拿着旧快照收尾，不得抹掉触发轮的写入。"""
    _write(state, {"last_ts": "T0", "last_trigger": {"600309": {"dir": "up"}},
                   "last_news_key": "k1", "last_pnl": {"600309": 1.2}})
    st_prev = H.load_state()                      # 采样进程在分析前读的快照
    assert st_prev["last_ts"] == "T0"

    # 触发轮（另一进程/另一段代码）写下自己的键
    H.update_state({"last_ts": "T1", "last_reason": "波动触发",
                    "last_trigger": {"600309": {"dir": "down"}}})

    # 采样进程收尾：只提交自己拥有的采样键（旧实现是 save_state({**st_prev, ...})）
    H.update_state({"last_sample_asset": 123456.0, "last_sample_ts": "T2"})

    st = H.load_state()
    assert st["last_ts"] == "T1"                  # 没被抹回 T0
    assert st["last_trigger"] == {"600309": {"dir": "down"}}
    assert st["last_news_key"] == "k1"            # 别的写者/上一轮的键都在
    assert st["last_sample_asset"] == 123456.0
    assert st["last_sample_ts"] == "T2"


def test_update_state_only_touches_given_keys(state):
    _write(state, {"last_ts": "T0", "last_pnl": {"600309": 1.0}, "extra": 1})
    H.update_state({"last_ts": "T1"})
    assert H.load_state() == {"last_ts": "T1", "last_pnl": {"600309": 1.0},
                              "extra": 1}


def test_update_state_mutate_sees_latest_value(state):
    """嵌套字典的读-改-写必须在锁内做：两个 agent 各自写自己的 pool_sig，
    用陈旧副本合并会互相抹（整点轮里 agent 是依次跑的）。"""
    H.update_state({"last_pool_sig": {"a": "sig-a"}})
    H.update_state(mutate=lambda cur: {
        **cur,
        "last_pool_sig": {**(cur.get("last_pool_sig") or {}), "b": "sig-b"},
    })
    assert H.load_state()["last_pool_sig"] == {"a": "sig-a", "b": "sig-b"}


def test_update_state_is_atomic_on_replace_failure(state, monkeypatch):
    """替换失败 → 目标文件保持旧内容（不能出现半写文件）。"""
    _write(state, {"last_ts": "T0"})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(H.os, "replace", boom)
    H.update_state({"last_ts": "T1"})             # 不抛：状态写失败不能打断交易路径
    # 直接读文件断言（不能用 H.load_state：monkeypatch.undo() 会把路径也还原成生产的）
    assert json.loads(state.read_text(encoding="utf-8"))["last_ts"] == "T0"


def test_concurrent_updates_lose_nothing(state):
    """两线程各写各的键 50 次：读-改-写全程在锁内 → 两边的键都要在。"""
    errors = []

    def worker(keys):
        try:
            for i in range(50):
                H.update_state({k: i for k in keys})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(["last_pnl"],))
    t2 = threading.Thread(target=worker, args=(["last_trigger"],))
    for t in (t1, t2):
        t.start()
    for t in (t1, t2):
        t.join(timeout=30)
    assert not errors
    st = H.load_state()
    assert st["last_pnl"] == 49 and st["last_trigger"] == 49


def test_reader_never_sees_partial_file(state):
    """原子替换的可见性：并发读者永远读不到半写状态（旧实现是 write_text 截断写）。"""
    big = {"last_pnl": {f"6000{i:02d}": 1.5 for i in range(4000)}}
    _write(state, big)
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            try:
                json.loads(state.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                bad.append(str(exc))
            except OSError:
                pass

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(200):
            H.update_state({"last_ts": f"T{i}"})
    finally:
        stop.set()
        t.join(timeout=10)
    assert bad == []


def test_state_lock_is_reentrant(state):
    """同线程嵌套（update_state 里再 update_state）不能自死锁。"""
    done = []

    def nest():
        with H._state_lock() as got:
            assert got
            H.update_state({"outer": 1})
            H.update_state({"inner": 2})
        done.append(True)

    t = threading.Thread(target=nest)
    t.start()
    t.join(timeout=10)
    assert done == [True], "状态锁不可重入 → 同线程自死锁"
    assert H.load_state() == {"outer": 1, "inner": 2}


def test_no_save_state_left():
    """整表写入口必须消失（事故的结构性修复，不是"调用方记得别传旧表"）。"""
    assert not hasattr(H, "save_state")
