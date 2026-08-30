import asyncio
import json
import logging
import os
import time
from datetime import datetime

from ..core.trade_plan import TradePlan
from ..executor.plan_executor import PlanExecutor

log = logging.getLogger(__name__)


class FileSyncChannel:
    """Windows 侧文件通道: 轮询共享目录 pending/ 新计划, 执行后写回报告并归档."""

    def __init__(self, shared_dir: str, executor: PlanExecutor,
                 poll_interval: float = 0.5):
        self.shared_dir = shared_dir
        self.pending_dir = os.path.join(shared_dir, "trade_plans", "pending")
        self.processed_dir = os.path.join(shared_dir, "trade_plans", "processed")
        self.failed_dir = os.path.join(shared_dir, "trade_plans", "failed")
        self.report_dir = os.path.join(shared_dir, "execution_reports")
        self.executor = executor
        self.poll_interval = poll_interval
        self._seen = set()
        for d in (self.pending_dir, self.processed_dir, self.failed_dir, self.report_dir):
            os.makedirs(d, exist_ok=True)

    def _list_pending(self) -> list:
        try:
            return [f for f in os.listdir(self.pending_dir)
                    if f.endswith(".json") and not f.startswith(".")]
        except OSError:
            return []

    async def _process(self, filename: str) -> None:
        path = os.path.join(self.pending_dir, filename)
        if path in self._seen:
            return
        self._seen.add(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            plan = TradePlan.from_dict(data)
        except (OSError, ValueError) as e:
            log.error(f"计划文件解析失败 {filename}: {e}")
            self._archive(path, self.failed_dir, {"error": str(e)})
            return

        try:
            report = await asyncio.to_thread(self.executor.execute_plan, plan)
            report["channel_used"] = "file_sync"
            report["plan"] = plan.to_dict()
            self._write_report(report)
            target = self.processed_dir if report["status"] != "rejected" else self.failed_dir
            self._archive(path, target)
            log.info(f"计划 {plan.plan_id} 执行完成: {report['status']}")
        except Exception as e:
            log.exception(f"执行计划 {filename} 异常")
            self._write_report({"plan_id": data.get("plan_id", filename),
                                "status": "error", "message": str(e),
                                "channel_used": "file_sync"})
            self._archive(path, self.failed_dir, {"error": str(e)})

    def _write_report(self, report: dict) -> None:
        name = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}.json"
        tmp = os.path.join(self.report_dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(self.report_dir, name))

    def _archive(self, path: str, target_dir: str, extra=None) -> None:
        try:
            if extra:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["archive_error"] = extra
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(path, os.path.join(target_dir, os.path.basename(path)))
        except OSError as e:
            log.error(f"归档失败 {path}: {e}")

    async def run(self):
        log.info(f"文件通道监听 {self.pending_dir}")
        while True:
            try:
                for fname in self._list_pending():
                    await self._process(fname)
            except Exception as e:
                log.error(f"文件通道循环错误: {e}")
            await asyncio.sleep(self.poll_interval)
