#!/usr/bin/env python3
"""Pine 转写 / 回测队列 worker（宿主侧）。

界面在「策略库」点「转写并回测」时，API 只往 ``data/pine_transpile/queue/<id>.json``
写一个请求——容器里没有 bwrap，也不该执行模型产出的代码。本脚本在宿主上消费队列：

  1. 转写段：调模型把 .pine 翻成 Pyne-Python（要联网，普通子进程）
  2. 静态闸：meta.json 的 problems 非空 → 直接失败，候选永不进回测
  3. 回测段：bwrap 沙箱里跑（只读根 + 断网 + 只把该策略目录挂成可写）

沙箱不是可选项：候选是模型产物，静态检查只是速度闸不是沙箱。bwrap 缺失直接拒跑，
不允许静默降级成裸执行。

状态写在 ``data/pine_transpile/<id>/job.json``（界面轮询），回测结果写 report.json。

cron::

    * * * * * cd /home/zbox/quant-Trader && .venv/bin/python scripts/pine_transpile_worker.py >> logs/pine_transpile_worker.log 2>&1

手动跑一次：``.venv/bin/python scripts/pine_transpile_worker.py``
"""
from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data/pine_transpile"
QUEUE_DIR = OUT_DIR / "queue"
LOCK_PATH = OUT_DIR / "worker.lock"
CLI = ROOT / "scripts/pine_to_pyne.py"

TRANSPILE_TIMEOUT = 300
BACKTEST_TIMEOUT = 180
LOG_TAIL = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_job(item_id: str, **fields) -> dict:
    """写 job.json（原子替换）。界面只读这个文件判断进度。"""
    d = OUT_DIR / item_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / "job.json"
    job = {}
    if path.exists():
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            job = {}
    job.update(fields)
    job["updated"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return job


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text[-LOG_TAIL:] if len(text) > LOG_TAIL else text


def sandbox_cmd(item_dir: Path, argv: list[str]) -> list[str]:
    """把命令包进 bwrap：只读根、断网、仅 item_dir 可写、/tmp 用 tmpfs。

    ``--unshare-net`` 是重点——回测不需要网络，断掉之后候选就算绕过了静态检查
    也发不出数据。写路径只放行该策略目录（PyneCore 会在脚本旁删/写 .toml）。
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("没有 bwrap，拒绝在沙箱外执行模型产出的代码（apt install bubblewrap）")
    return [bwrap, "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
            "--proc", "/proc", "--tmpfs", "/tmp", "--unshare-net",
            "--chdir", str(ROOT),
            "--bind", str(item_dir), str(item_dir),
            sys.executable, str(CLI), *argv]


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, check=False)


def process(req: dict) -> dict:
    """处理一条队列请求。返回 job 字段。"""
    item_id = str(req.get("id") or "").strip()
    symbol = str(req.get("symbol") or "").strip()
    adj = str(req.get("adj") or "backward")
    model = str(req.get("model") or "")
    if not item_id or not symbol:
        raise ValueError("请求缺 id / symbol")
    item_dir = OUT_DIR / item_id
    item_dir.mkdir(parents=True, exist_ok=True)

    _write_job(item_id, status="running", stage="transpile", symbol=symbol, adj=adj,
               error="", started=_now())

    # ---- 1. 转写（联网；只生成文本，不执行候选）----
    argv = ["--id", item_id]
    if model:
        argv += ["--model", model]
    if req.get("force"):
        argv.append("--force")
    try:
        got = _run([sys.executable, str(CLI), *argv], TRANSPILE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _write_job(item_id, status="failed", stage="transpile",
                          error=f"转写超时（>{TRANSPILE_TIMEOUT}s）")
    if got.returncode not in (0, 2):
        return _write_job(item_id, status="failed", stage="transpile",
                          error=f"转写失败（exit {got.returncode}）", log=_tail(got.stderr))

    # ---- 2. 静态闸 ----
    meta = {}
    meta_path = item_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    problems = meta.get("problems") or []
    if problems:
        return _write_job(item_id, status="failed", stage="static",
                          error="静态检查未通过", problems=problems,
                          log=_tail(got.stdout + got.stderr))

    # ---- 3. 沙箱回测 ----
    _write_job(item_id, status="running", stage="backtest")
    try:
        argv = sandbox_cmd(item_dir, ["--id", item_id, "--no-transpile",
                                      "--validate", symbol, "--adj", adj])
    except RuntimeError as exc:
        return _write_job(item_id, status="failed", stage="backtest", error=str(exc))
    try:
        got = _run(argv, BACKTEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _write_job(item_id, status="failed", stage="backtest",
                          error=f"回测超时（>{BACKTEST_TIMEOUT}s）")
    if got.returncode != 0:
        return _write_job(item_id, status="failed", stage="backtest",
                          error=f"回测失败（exit {got.returncode}）",
                          log=_tail(got.stdout + got.stderr))
    return _write_job(item_id, status="done", stage="done", error="", problems=[],
                      log=_tail(got.stdout))


def _claim_lock():
    """独占锁：cron 每分钟起一次，上一轮没跑完就安静退出。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main() -> int:
    lock = _claim_lock()
    if lock is None:
        print(f"{_now()} 上一轮还在跑，跳过")
        return 0
    if not QUEUE_DIR.is_dir():
        return 0

    reqs = sorted(QUEUE_DIR.glob("*.json"))
    if not reqs:
        return 0
    print(f"{_now()} 待处理 {len(reqs)} 条")
    for path in reqs:
        t0 = time.time()
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{_now()} 请求文件损坏 {path.name}: {exc}")
            path.unlink(missing_ok=True)
            continue
        # 先摘牌再处理：处理中重复点按钮不会排两遍（API 也有一道查重）。
        path.unlink(missing_ok=True)
        try:
            job = process(req)
            print(f"{_now()} {req.get('id')} → {job.get('status')}/{job.get('stage')} "
                  f"（{time.time() - t0:.1f}s）{job.get('error') or ''}")
        except Exception as exc:  # noqa: BLE001
            print(f"{_now()} {req.get('id')} 处理异常: {exc}")
            _write_job(str(req.get("id") or "unknown"), status="failed", stage="worker",
                       error=f"worker 异常: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
