"""Pine 策略库：读 scripts/pine_library_index.py 产出的索引，提供列表 / 源码 / 编辑保存。

索引与源码副本都在 ``data/pine_library/``（容器内 /app/data 可读）；桌面原语料容器看不到，
所以索引构建时已把生效源码复制到 ``data/pine_library/source/<id>.pine``。

编辑保存在 ``data/pine_library/edited/<id>.pine``，优先级高于重爬与原始语料，不会被索引重建覆盖。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = Path(os.environ.get("PINE_LIBRARY_DIR") or ROOT / "data/pine_library")
INDEX = LIB_DIR / "index.json"
SOURCE_DIR = LIB_DIR / "source"
EDITED_DIR = LIB_DIR / "edited"

MAX_SOURCE_BYTES = 512 * 1024
ID_RE = re.compile(r"^(?:\d{4}|x\d{3})$")


class LibraryError(ValueError):
    """入参不合法（无效 id / 源码过大）。"""


def _check_id(item_id: str) -> str:
    item_id = (item_id or "").strip()
    if not ID_RE.match(item_id):
        raise LibraryError(f"无效的策略 id: {item_id!r}")
    return item_id


def load_index() -> dict:
    if not INDEX.exists():
        return {"generated": "", "source_dir": "", "total": 0, "usable": 0,
                "categories": [], "items": []}
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _match(item: dict, q: str) -> bool:
    q = q.lower()
    return any(q in str(item.get(k, "")).lower()
               for k in ("id", "title", "title_en", "file", "category"))


def list_items(category: str = "", q: str = "", limit: int = 200, offset: int = 0) -> dict:
    """分类 + 关键词过滤后的列表（不带源码，界面列表用）。"""
    idx = load_index()
    items = idx.get("items", [])
    if category:
        items = [it for it in items if it.get("category") == category]
    if q:
        items = [it for it in items if _match(it, q)]
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "generated": idx.get("generated", ""),
        "total": idx.get("total", 0),
        "usable": idx.get("usable", 0),
        "filtered": total,
        "categories": idx.get("categories", []),
        "items": page,
    }


def _find(idx: dict, item_id: str) -> dict:
    for it in idx.get("items", []):
        if it.get("id") == item_id:
            return it
    raise LibraryError(f"策略不存在: {item_id}")


def get_source(item_id: str) -> dict:
    """单条策略元数据 + 源码（编辑版优先，与索引构建的来源优先级一致）。"""
    item_id = _check_id(item_id)
    idx = load_index()
    item = _find(idx, item_id)
    edited_path = EDITED_DIR / f"{item_id}.pine"
    path = edited_path if edited_path.exists() else SOURCE_DIR / f"{item_id}.pine"
    if not path.exists():
        raise LibraryError(f"策略源码缺失: {item_id}（重建索引试试）")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {**item, "edited": edited_path.exists(), "source": text}


def save_source(item_id: str, text: str) -> dict:
    """保存界面里的编辑（写入 edited/，不回写桌面语料）。"""
    item_id = _check_id(item_id)
    if not isinstance(text, str) or not text.strip():
        raise LibraryError("源码不能为空")
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise LibraryError("源码过大（>512KB）")
    get_source(item_id)  # 校验 id 存在
    EDITED_DIR.mkdir(parents=True, exist_ok=True)
    (EDITED_DIR / f"{item_id}.pine").write_text(text, encoding="utf-8")
    return {"id": item_id, "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bytes": len(text.encode("utf-8")),
            "lines": len(text.splitlines()),
            "indent_ok": any(ln[:1] in (" ", "\t") for ln in text.splitlines())}


def reset_source(item_id: str) -> dict:
    """丢弃编辑，回到索引构建时的生效版本。"""
    item_id = _check_id(item_id)
    path = EDITED_DIR / f"{item_id}.pine"
    existed = path.exists()
    path.unlink(missing_ok=True)
    return {"id": item_id, "removed": existed}


# ---------- AI 转写产物（scripts/pine_to_pyne.py 写的，页面只读） ----------

TRANSPILE_DIR = LIB_DIR.parent / "pine_transpile"


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def list_transpile() -> dict:
    """转写产物一览：{id: {title, problems, has_report, stats, job}}。

    转写在宿主上跑（要执行模型产出的代码，不进 API 进程），页面只读结果。
    """
    out: dict[str, dict] = {}
    if not TRANSPILE_DIR.is_dir():
        return out
    for d in sorted(TRANSPILE_DIR.iterdir()):
        if not d.is_dir() or not ID_RE.match(d.name):   # queue/ 之类的工作目录不算条目
            continue
        meta = _read_json(d / "meta.json")
        report = _read_json(d / "report.json")
        if not meta and not report:
            continue
        stats = report.get("stats") or {}
        job = _read_json(d / "job.json")
        out[d.name] = {
            "title": meta.get("title", ""),
            "model": meta.get("model", ""),
            "generated": meta.get("generated", ""),
            "problems": meta.get("problems") or [],
            "has_candidate": (d / "candidate.py").exists(),
            "has_report": bool(report),
            "symbol": report.get("symbol", ""),
            "trades": report.get("trades"),
            "net_pct": (stats.get("Net profit") or {}).get("pct"),
            "dd_pct": (stats.get("Max equity drawdown") or {}).get("pct"),
            "bh_pct": (stats.get("Buy & hold return") or {}).get("pct"),
            "job": {"status": job.get("status", ""), "stage": job.get("stage", ""),
                    "error": job.get("error", "")},
        }
    return out


# ---------- 转写 / 回测队列（宿主 worker 消费：scripts/pine_transpile_worker.py） ----------
# API 只写请求文件（容器里没有 bwrap，也不能执行模型产出的代码），
# worker 在宿主上转写 + 沙箱回测，进度写回 <id>/job.json。

QUEUE_DIR = TRANSPILE_DIR / "queue"
ADJS = ("backward", "forward", "unadjusted")


def _check_symbol(symbol: str) -> str:
    from backend.services import market_lab as ml

    code = ml.normalize_code(symbol)
    if not code or code not in ml._names():
        raise LibraryError(f"未知标的: {symbol!r}")
    return code


def job_status(item_id: str) -> dict:
    """该策略当前任务状态。队列里有请求但还没开跑 → queued。"""
    item_id = _check_id(item_id)
    job = _read_json(TRANSPILE_DIR / item_id / "job.json")
    if not job and (QUEUE_DIR / f"{item_id}.json").exists():
        return {"status": "queued", "stage": "queued", "error": ""}
    return job


def read_report(item_id: str) -> dict:
    """回测报告（stats + 逐笔 + 净值）。没跑过 → 空 dict。"""
    item_id = _check_id(item_id)
    return _read_json(TRANSPILE_DIR / item_id / "report.json")


def enqueue_backtest(item_id: str, symbol: str, adj: str = "backward",
                     model: str = "", force: bool = False) -> dict:
    """把「转写 + 回测」请求排进队列，由宿主 worker 消费。"""
    item_id = _check_id(item_id)
    get_source(item_id)                      # id 必须存在
    code = _check_symbol(symbol)
    if adj not in ADJS:
        raise LibraryError(f"未知复权口径: {adj}")
    qpath = QUEUE_DIR / f"{item_id}.json"
    if qpath.exists():
        raise LibraryError("该策略已在队列里")
    if job_status(item_id).get("status") == "running":
        raise LibraryError("该策略正在跑，等它结束")

    req = {"id": item_id, "symbol": code, "adj": adj, "model": model,
           "force": bool(force),
           "requested": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = qpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(qpath)                        # 原子落地，worker 不会读到半截
    return {"queued": True, "request": req}
