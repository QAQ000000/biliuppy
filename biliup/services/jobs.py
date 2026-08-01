from __future__ import annotations

import asyncio
import logging
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
    def __init__(self) -> None:
        self.jobs: dict[str, BackgroundJob] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def submit(self, kind: str, operation: Coroutine[Any, Any, None]) -> BackgroundJob:
        job = BackgroundJob(id=uuid4().hex, kind=kind)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, operation), name=f"{kind}-{job.id}")
        self.tasks[job.id] = task
        task.add_done_callback(lambda _task, job_id=job.id: self.tasks.pop(job_id, None))
        return job

    async def _run(self, job: BackgroundJob, operation: Coroutine[Any, Any, None]) -> None:
        job.status = "Running"
        try:
            await operation
        except asyncio.CancelledError:
            job.status = "Cancelled"
            raise
        except Exception as exc:
            job.status = "Error"
            job.error = str(exc)
            logger.exception("Background %s job %s failed", job.kind, job.id)
        else:
            job.status = "Completed"

    def get(self, job_id: str) -> BackgroundJob | None:
        return self.jobs.get(job_id)

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
