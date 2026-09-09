#!/usr/bin/env python3
"""Pine 策略对话 worker（宿主侧）。

界面在右栏「对话」发消息时，API 只往 ``data/pine_library/chat/queue/<job_id>.json``
写一个请求——容器里没有 bwrap，也不该执行模型产出的代码。本脚本在宿主上消费队列：

  1. 读上下文：生效源码 + 备注 + 最近一次沙箱回测报告 + 最近若干轮对话
  2. 调模型（复用 ``scripts/pine_to_pyne.py`` 的 ``_chat``，关思考）出回答
  3. 写 ``chat/<id>/thread.jsonl``（界面回放）与 ``chat/jobs/<job_id>.json``（界面轮询）

``mode=ask`` 只出文本。``mode=edit``（P3）产出候选 Pine，先过静态闸、再进 bwrap 沙箱回测，
未点「采用」前不碰正式源码——本文件目前对 edit 一律拒答。

cron::

    * * * * * cd /home/zbox/quant-Trader && .venv/bin/python scripts/pine_chat_worker.py >> logs/pine_chat_worker.log 2>&1

手动跑一次：``.venv/bin/python scripts/pine_chat_worker.py``
"""
from __future__ import annotations

import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import pine_library as pl          # noqa: E402
from scripts.pine_to_pyne import _chat                   # noqa: E402

MAX_SOURCE_CHARS = 12000     # 源码进上下文的上限（语料最大 512KB，不能整份塞）
THREAD_TURNS = 12            # 带进上下文的历史轮数
SUPPORTED_MODES = ("ask",)

SYSTEM = """你是 A 股量化策略研究助手，帮用户理解并改进 TradingView Pine 策略。

上下文里会给你：策略元信息、Pine 源码、用户的备注、最近一次回测结果。

规则：
1. 只依据给出的源码与回测数据回答，**不要编造**没给出的数字或行情。
2. 本机没有 Pine 解释器：源码先被转写成 Pyne-Python，再在断网沙箱里回测。
   讨论收益时提醒这一点（转写可能有偏差，回测是历史数据、不含未来函数）。
3. 回答要具体：指到变量名/行号级的逻辑，别讲空泛套话。
4. 该说风险就说风险（过拟合、参数敏感、样本内外差异、A股涨跌停与 T+1 限制）。
5. 用中文，控制在 400 字以内，必要时用小标题或列表。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_job(job_id: str, **fields) -> dict:
    """写 jobs/<job_id>.json（原子替换）。界面只读这个文件判断进度。"""
    path = pl.CHAT_JOBS / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    job = {}
    if path.exists():
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            job = {}
    job.update(fields)
    job["job_id"] = job_id
    job["updated"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return job


def _drop_request(path: Path) -> None:
    """摘牌。目录属主不对时 unlink 会失败——把原因和修法说清楚，别只留个 errno。"""
    try:
        path.unlink(missing_ok=True)
    except PermissionError as exc:
        raise PermissionError(
            f"无权限删除队列文件 {path}：目录属主不是本用户（容器以 root 建的目录）。"
            f"修：docker exec baymax-api chown 1000:1000 {path.parent}"
        ) from exc


def _append_thread(item_id: str, row: dict) -> None:
    """追加一行对话（单 worker 串行，直接 append 即可）。"""
    path = pl.CHAT_DIR / item_id / "thread.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _pct(stats: dict, key: str) -> str:
    v = (stats.get(key) or {}).get("pct")
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


def build_messages(item_id: str, question: str, history: list[dict]) -> list[dict]:
    """system（策略上下文）+ 历史轮次 + 本轮问题。"""
    src = pl.get_source(item_id)
    note = pl.get_note(item_id)
    report = pl.read_report(item_id)

    ctx = [f"策略 #{item_id}《{src.get('title') or src.get('file')}》",
           f"分类：{src.get('category')} · Pine v{src.get('version')} · "
           f"{src.get('lines')} 行 · 语料来源 {src.get('source_kind')}"]
    if note.get("note") or note.get("tags"):
        line = f"用户备注：{note.get('note') or '（无）'}"
        if note.get("tags"):
            line += f"（标签：{'、'.join(note['tags'])}）"
        ctx.append(line)
    if report:
        st = report.get("stats") or {}
        ctx.append(f"最近一次沙箱回测（{report.get('symbol')} / {report.get('adj')}）："
                   f"净收益 {_pct(st, 'Net profit')} · 买入持有 {_pct(st, 'Buy & hold return')} · "
                   f"最大回撤 {_pct(st, 'Max equity drawdown')} · 成交 {report.get('trades')} 笔")
    else:
        ctx.append("最近一次回测：还没有（讨论收益时请说明无数据）")

    text = (src.get("source") or "")[:MAX_SOURCE_CHARS]
    if len(src.get("source") or "") > MAX_SOURCE_CHARS:
        text += "\n// …（源码过长已截断）"
    ctx.append("当前 Pine 源码：\n```pine\n" + text + "\n```")

    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + "\n\n".join(ctx)}]
    msgs += [{"role": m.get("role"), "content": str(m.get("content") or "")}
             for m in history if m.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": question})
    return msgs


def process(req: dict) -> dict:
    """处理一条对话请求，返回 job 字段。"""
    job_id = str(req.get("job_id") or "").strip()
    item_id = str(req.get("id") or "").strip()
    message = str(req.get("message") or "").strip()
    mode = str(req.get("mode") or "ask")
    model = str(req.get("model") or pl.CHAT_MODEL)
    if not job_id or not item_id or not message:
        raise ValueError("请求缺 job_id / id / message")

    _write_job(job_id, id=item_id, status="running", stage="context",
               mode=mode, message=message, error="", reply="")
    _drop_request(pl.CHAT_QUEUE / f"{job_id}.json")   # 先落 running 再摘牌

    if mode not in SUPPORTED_MODES:
        return _write_job(job_id, id=item_id, status="failed", stage="mode",
                          error=f"mode={mode} 还没上线（P3 才支持改策略）")

    try:
        history = pl.chat_thread(item_id, limit=THREAD_TURNS)["messages"]
        messages = build_messages(item_id, message, history)
    except ValueError as exc:
        return _write_job(job_id, id=item_id, status="failed", stage="context",
                          error=str(exc))

    _append_thread(item_id, {"timestamp": _now(), "role": "user", "content": message,
                             "job_id": job_id, "mode": mode})
    _write_job(job_id, id=item_id, status="running", stage="llm", error="")

    try:
        reply, usage, truncated = _chat(messages, model, think=False)
    except Exception as exc:  # noqa: BLE001
        _write_job(job_id, id=item_id, status="failed", stage="llm",
                   error=f"模型调用失败: {exc}")
        raise
    if not reply:
        return _write_job(job_id, id=item_id, status="failed", stage="llm",
                          error="模型返回空内容")

    _append_thread(item_id, {"timestamp": _now(), "role": "assistant", "content": reply,
                             "job_id": job_id, "mode": mode, "usage": usage})
    return _write_job(job_id, id=item_id, status="done", stage="done",
                      reply=reply, usage=usage, truncated=bool(truncated), error="")


def _claim_lock():
    """独占锁：cron 每分钟起一次，上一轮没跑完就安静退出（与转写 worker 各自独立）。"""
    pl.CHAT_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(pl.CHAT_DIR / "worker.lock", "w")
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
    if not pl.CHAT_QUEUE.is_dir():
        return 0

    reqs = sorted(pl.CHAT_QUEUE.glob("*.json"))
    if not reqs:
        return 0
    print(f"{_now()} 待处理 {len(reqs)} 条")
    for path in reqs:
        t0 = time.time()
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{_now()} 请求文件损坏 {path.name}: {exc}")
            _drop_request(path)
            continue
        try:
            job = process(req)
            print(f"{_now()} {req.get('id')} → {job.get('status')}/{job.get('stage')} "
                  f"（{time.time() - t0:.1f}s）{job.get('error') or ''}")
        except Exception as exc:  # noqa: BLE001
            print(f"{_now()} {req.get('id')} 处理异常: {exc}")
            try:
                _write_job(str(req.get("job_id") or "unknown"), id=str(req.get("id") or ""),
                           status="failed", stage="worker", error=f"worker 异常: {exc}")
                _drop_request(path)
            except Exception as exc2:  # noqa: BLE001
                # 连失败都写不下去（例如 jobs/ 目录也被容器建成 root）——至少要留在 cron 日志里
                print(f"{_now()} 写失败状态也失败: {exc2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
