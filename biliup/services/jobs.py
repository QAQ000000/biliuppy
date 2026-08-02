from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

logger = logging.getLogger("biliup.jobs")


@dataclass(slots=True)
class BackgroundJob:
    id: str
    kind: str
    status: str = "Pending"
    error: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


class BackgroundJobManager:
    TERMINAL_STATUSES = {"Completed", "Error", "Cancelled"}

    def __init__(self, *, max_completed: int = 100) -> None:
        self.jobs: dict[str, BackgroundJob] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.max_completed = max(0, max_completed)

    def submit(self, kind: str, operation: Coroutine[Any, Any, None]) -> BackgroundJob:
        job = BackgroundJob(id=uuid4().hex, kind=kind)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, operation), name=f"{kind}-{job.id}")
        self.tasks[job.id] = task
        task.add_done_callback(lambda _task, job_id=job.id: self._task_done(job_id))
        return job

    def _task_done(self, job_id: str) -> None:
        self.tasks.pop(job_id, None)
        terminal_ids = [
            current_id
            for current_id, current_job in self.jobs.items()
            if current_job.status in self.TERMINAL_STATUSES
        ]
        for current_id in terminal_ids[:-self.max_completed] if self.max_completed else terminal_ids:
            self.jobs.pop(current_id, None)

    async def _run(self, job: BackgroundJob, operation: Coroutine[Any, Any, None]) -> None:
        job.status = "Running"
        started_at = time.perf_counter()
        logger.info("Background %s job %s started", job.kind, job.id)
        try:
            await operation
        except asyncio.CancelledError:
            job.status = "Cancelled"
            logger.info(
                "Background %s job %s cancelled after %.2fs",
                job.kind,
                job.id,
                time.perf_counter() - started_at,
            )
            raise
        except Exception as exc:
            job.status = "Error"
            job.error = str(exc)
            logger.exception("Background %s job %s failed", job.kind, job.id)
        else:
            job.status = "Completed"
            logger.info(
                "Background %s job %s completed in %.2fs",
                job.kind,
                job.id,
                time.perf_counter() - started_at,
            )

    def get(self, job_id: str) -> BackgroundJob | None:
        return self.jobs.get(job_id)

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
