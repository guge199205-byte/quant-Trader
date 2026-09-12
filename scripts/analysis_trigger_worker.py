#!/usr/bin/env python3
"""手动分析触发 worker：消费 logs/analysis_jobs.jsonl 的 pending 任务。

前端「对话 tab → 立即分析」→ api_server 写任务行 → 本 worker（cron 每
分钟）拉起 live_hourly_analysis.py 执行：
  - 交易时段内：与整点分析同权（闸门/执行开关一致，时段内会真下单）
  - 盘前/盘后/午休：--force 只出决策记录对话，不真下单（force 禁执行）
管线自身 _try_lock 防叠跑；管线忙时任务留 pending 下轮自动补跑。
"""
import fcntl
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

JOBS = ROOT / "logs" / "analysis_jobs.jsonl"
LOCK = ROOT / "logs" / ".analysis_worker.lock"
TIMEOUT_S = 900
BJ = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(BJ).isoformat(timespec="seconds")


def load() -> list:
    """读任务队列。**逐行容错**：一行坏（写一半 / 撕裂多字节）只丢那一行。

    旧实现用整表推导式 + `except ValueError: return []`：任意一行损坏 → 整个队列
    读成空 →「立即分析」按钮永久静默失效（任务行还在文件里，每分钟 cron 都读不到，
    人看到的只有「点了没反应」）。坏行必须留痕，否则事后无从查（2026-09-12 批 13）。
    """
    if not JOBS.is_file():
        return []
    try:
        # errors="replace"：本文件含中文且由 api_server 追加写（非原子），撕裂的多字节
        # 字符会让严格解码整文件抛 UnicodeDecodeError（批 10 同类）。
        text = JOBS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows, bad = [], 0
    for l in text.splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(r, dict):
            rows.append(r)
        else:
            bad += 1
    if bad:
        print(f"⚠️ analysis_jobs 有 {bad} 行无法解析，已跳过（其余 {len(rows)} 条照常）")
    return rows


def save(rows: list) -> None:
    JOBS.parent.mkdir(exist_ok=True)
    tmp = JOBS.with_name(JOBS.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(JOBS)


def main() -> int:
    rows = load()
    pend_all = [r for r in rows if r.get("status") == "pending"]
    # 新闻管线任务不受交易日闸门限制（只读新闻/行情，不下单，手动随时可触发）
    pend = [r for r in pend_all if r.get("type", "analysis") != "news"]
    news_pend = [r for r in pend_all if r.get("type") == "news"]
    if not pend and not news_pend:
        return 0

    # 交易日历闸门：法定节假日休市不分析——任务保持 pending，下个交易日自动补跑。
    # 有遗留任务时每分钟会进这里，打印做每日一次节流防刷屏。
    bj_today = datetime.now(BJ).date()
    if bj_today.weekday() < 5:
        try:
            from trading_cal import is_trading_day, why_not

            if not is_trading_day(bj_today):
                day_key = _now()[:10]
                note = ROOT / "logs" / ".worker_holiday_note"
                try:
                    seen = note.read_text(encoding="utf-8").strip() == day_key
                except OSError:
                    seen = False
                if not seen:
                    try:
                        note.write_text(day_key, encoding="utf-8")
                    except OSError:
                        pass
                    print(f"[{_now()}] {why_not(bj_today)}，{len(pend)} 个分析任务保持 "
                          f"pending（下个交易日补跑）")
                    pend = []  # 休市：交易分析延后；新闻任务照跑
                if not pend and not news_pend:
                    return 0
        except Exception:  # noqa: BLE001  日历不可用 → 维持原行为
            pass

    try:
        lock = LOCK.open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # 上一轮未结束，保持 pending 下轮再试

    try:
        from live_hourly_analysis import in_trading_window

        now = datetime.now(BJ)
        in_window = in_trading_window(now)
        for r in (news_pend + pend):
            if r.get("type") == "news":
                cmd = [sys.executable, str(ROOT / "scripts" / "news_brief.py")]
            else:
                agents = r.get("agents") or "all"
                names = "" if agents == "all" else ",".join(agents)
                cmd = [sys.executable, str(ROOT / "scripts" / "live_hourly_analysis.py")]
                if names:
                    cmd += ["--agents", names]
                if not in_window:
                    cmd.append("--force")  # 盘外：只出决策不下单
            r["status"] = "running"
            r["start_ts"] = _now()
            save(rows)

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=TIMEOUT_S)
                out = (proc.stdout or "") + (proc.stderr or "")
                tail = " | ".join(out.strip().splitlines()[-2:])[:200]
                if "已有分析实例在跑" in out:
                    r["status"] = "pending"
                    r["note"] = "分析实例正忙，留待下轮补跑"
                elif proc.returncode == 0:
                    r["status"] = "done"
                    r["done_ts"] = _now()
                    r["note"] = tail or "ok"
                else:
                    r["status"] = "failed"
                    r["done_ts"] = _now()
                    r["note"] = tail or f"rc={proc.returncode}"
                print(f"[{_now()}] {r['id']} → {r['status']}: {r.get('note', '')[:120]}")
            except subprocess.TimeoutExpired:
                r["status"] = "failed"
                r["done_ts"] = _now()
                r["note"] = f"超时（>{TIMEOUT_S}s）"
                print(f"[{_now()}] {r['id']} 超时")
            save(rows)
        # 清理 3 天前终态任务
        cutoff = (datetime.now(BJ) - timedelta(days=3)).isoformat()
        keep = [r for r in rows
                if r.get("status") == "pending" or (r.get("done_ts") or "") >= cutoff]
        if len(keep) != len(rows):
            save(keep)
        return 0
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
