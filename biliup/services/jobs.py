from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from biliup.core.redaction import redact_sensitive_text
from biliup.database.models import BackgroundJobRecord
from biliup.database.session import Database

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
    ACTIVE_STATUSES = {"Pending", "Running"}

    def __init__(self, database: Database | None = None, *, max_completed: int = 100) -> None:
        self.database = database
        self.jobs: dict[str, BackgroundJob] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.max_completed = max(0, max_completed)
        if self.database is not None:
            self._restore_persisted()

    def submit(self, kind: str, operation: Coroutine[Any, Any, None]) -> BackgroundJob:
        job = BackgroundJob(id=uuid4().hex, kind=kind)
        self.jobs[job.id] = job
        self._save(job)
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
        self._prune_persisted()

    async def _run(self, job: BackgroundJob, operation: Coroutine[Any, Any, None]) -> None:
        job.status = "Running"
        self._save(job)
        started_at = time.perf_counter()
        logger.info("Background %s job %s started", job.kind, job.id)
        try:
            await operation
        except asyncio.CancelledError:
            job.status = "Cancelled"
            job.error = "Application shutdown interrupted the job"
            self._save(job)
            logger.info(
                "Background %s job %s cancelled after %.2fs",
                job.kind,
                job.id,
                time.perf_counter() - started_at,
            )
            raise
        except Exception as exc:
            job.status = "Error"
            job.error = redact_sensitive_text(str(exc))[:2000]
            self._save(job)
            logger.exception("Background %s job %s failed", job.kind, job.id)
        else:
            job.status = "Completed"
            self._save(job)
            logger.info(
                "Background %s job %s completed in %.2fs",
                job.kind,
                job.id,
                time.perf_counter() - started_at,
            )

    def get(self, job_id: str) -> BackgroundJob | None:
        job = self.jobs.get(job_id)
        if job is not None or self.database is None:
            return job
        with self.database.session_factory() as session:
            record = session.scalar(
                select(BackgroundJobRecord).where(BackgroundJobRecord.job_id == job_id)
            )
            if record is None:
                return None
            return BackgroundJob(record.job_id, record.kind, record.status, record.error)

    def _save(self, job: BackgroundJob) -> None:
        if self.database is None:
            return
        with self.database.session_factory() as session:
            record = session.scalar(
                select(BackgroundJobRecord).where(BackgroundJobRecord.job_id == job.id)
            )
            if record is None:
                record = BackgroundJobRecord(job_id=job.id, kind=job.kind, status=job.status)
                session.add(record)
            record.status = job.status
            record.error = job.error
            session.commit()

    def _restore_persisted(self) -> None:
        assert self.database is not None
        with self.database.session_factory() as session:
            interrupted = session.scalars(
                select(BackgroundJobRecord).where(
                    BackgroundJobRecord.status.in_(self.ACTIVE_STATUSES)
                ).order_by(BackgroundJobRecord.id.desc())
            ).all()
            for record in interrupted:
                record.status = "Cancelled"
                record.error = "Application restarted before the job completed"
            session.commit()
        preserved = {record.job_id for record in interrupted[: self.max_completed]}
        self._prune_persisted(preserved)
        if not self.max_completed:
            return
        with self.database.session_factory() as session:
            records = session.scalars(
                select(BackgroundJobRecord)
                .order_by(BackgroundJobRecord.id.desc())
                .limit(self.max_completed)
            ).all()
        for record in reversed(records):
            self.jobs[record.job_id] = BackgroundJob(
                record.job_id,
                record.kind,
                record.status,
                record.error,
            )

    def _prune_persisted(self, preserve_job_ids: set[str] | None = None) -> None:
        if self.database is None:
            return
        preserved = preserve_job_ids or set()
        keep = max(0, self.max_completed - len(preserved))
        with self.database.session_factory() as session:
            predicate = BackgroundJobRecord.status.in_(self.TERMINAL_STATUSES)
            if preserved:
                predicate &= BackgroundJobRecord.job_id.not_in(preserved)
            statement = select(BackgroundJobRecord).where(predicate).order_by(
                BackgroundJobRecord.id.desc()
            ).offset(keep)
            for record in session.scalars(statement):
                session.delete(record)
            session.commit()

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
