from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from biliup.core import AppPaths
from biliup.core.redaction import redact_sensitive_text
from biliup.database.models import PendingSubmission
from biliup.database.session import Database
from biliup.integrations.upload_state import UploadResult, account_key_for, account_lock_for
from biliup.integrations.uploaders.bili_web import BiliBili, Data

logger = logging.getLogger("biliup.submission_review")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SubmissionReviewService:
    PENDING_STATES = {-30, -6, -60, -1}
    APPROVED_STATES = {0, -40, -50}
    MAX_PENDING_AGE = timedelta(hours=24)
    TERMINAL_RETENTION = timedelta(days=30)

    def __init__(
        self,
        database: Database,
        paths: AppPaths,
        *,
        check_interval: float = 600,
    ) -> None:
        self.database = database
        self.paths = paths
        self.check_interval = max(60.0, float(check_interval))
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="submission-review")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def enqueue(self, result: UploadResult, source_files: list[Path]) -> bool:
        if result.aid is None:
            logger.warning("投稿成功但未返回 aid，保留源文件且不加入审核队列")
            return False
        now = _utc_now()
        values = {
            "aid": result.aid,
            "bvid": result.bvid,
            "account_key": result.account_key,
            "cookie_path": result.cookie_path,
            "source_files": [str(path.resolve()) for path in source_files],
            "status": "pending",
            "archive_state": None,
            "state_description": None,
            "last_error": None,
            "created_at": now,
            "checked_at": None,
            "updated_at": now,
        }
        statement = insert(PendingSubmission).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["aid"],
            set_={key: value for key, value in values.items() if key not in {"aid", "created_at"}},
        )
        def save(session) -> None:
            session.execute(statement)
        self.database.run_write(save)
        logger.info("已加入投稿审核队列 aid=%s bvid=%s", result.aid, result.bvid)
        return True

    async def check_once(self) -> None:
        now = _utc_now()
        def load_pending(session) -> list[int]:
            session.execute(
                delete(PendingSubmission).where(
                    PendingSubmission.status != "pending",
                    PendingSubmission.updated_at < now - self.TERMINAL_RETENTION,
                )
            )
            return list(
                session.scalars(
                    select(PendingSubmission.id)
                    .where(PendingSubmission.status == "pending")
                    .order_by(PendingSubmission.created_at)
                )
            )
        pending_ids = self.database.run_write(load_pending)

        for index, submission_id in enumerate(pending_ids):
            await self._check_submission(submission_id)
            if index + 1 < len(pending_ids):
                await asyncio.sleep(1)

    async def _check_submission(self, submission_id: int) -> None:
        with self.database.session_factory() as session:
            row = session.get(PendingSubmission, submission_id)
            if row is None or row.status != "pending":
                return
            aid = row.aid
            cookie_path = row.cookie_path
            created_at = row.created_at
        now = _utc_now()
        if now - created_at >= self.MAX_PENDING_AGE:
            self._update_status(submission_id, "expired", error="审核状态在24小时内未确认")
            logger.warning("稿件审核检查已超时，保留源文件 aid=%s", aid)
            return

        try:
            state, description = await asyncio.to_thread(self._fetch_archive_state, aid, cookie_path)
        except Exception as exc:
            message = redact_sensitive_text(str(exc))[:2000]
            self._update_status(submission_id, "pending", error=message, checked_at=now)
            logger.warning("查询稿件审核状态失败 aid=%s error=%s", aid, message)
            return

        if state in self.PENDING_STATES:
            self._update_status(
                submission_id,
                "pending",
                archive_state=state,
                description=description,
                checked_at=now,
            )
            return
        if state in self.APPROVED_STATES:
            with self.database.session_factory() as session:
                row = session.get(PendingSubmission, submission_id)
                source_files = list(row.source_files) if row else []
            self._remove_sources(source_files)
            self._update_status(
                submission_id,
                "approved",
                archive_state=state,
                description=description,
                checked_at=now,
            )
            logger.info("稿件审核通过并已清理源文件 aid=%s state=%s", aid, state)
            return
        self._update_status(
            submission_id,
            "rejected",
            archive_state=state,
            description=description,
            checked_at=now,
        )
        logger.warning("稿件审核未通过，保留源文件 aid=%s state=%s description=%s", aid, state, description)

    @staticmethod
    def _fetch_archive_state(aid: int, cookie_path: str) -> tuple[int, str]:
        account_key = account_key_for(Path(cookie_path), {})
        with account_lock_for(account_key):
            with BiliBili(Data()) as bili:
                bili.login(cookie_path, cookie_path, persist=False)
                payload = bili.get_archive(aid)
        archive = payload.get("archive") or {}
        if "state" not in archive:
            raise RuntimeError(f"Archive state is missing for aid={aid}")
        return int(archive["state"]), str(archive.get("state_desc") or "")

    def _remove_sources(self, source_files: list[str]) -> None:
        downloads = self.paths.downloads.resolve()
        for value in source_files:
            target = Path(value).resolve()
            if target != downloads and downloads not in target.parents:
                logger.error("拒绝删除下载目录之外的审核源文件: %s", target)
                continue
            target.unlink(missing_ok=True)
            target.with_suffix(".xml").unlink(missing_ok=True)

    def _update_status(
        self,
        submission_id: int,
        status: str,
        *,
        archive_state: int | None = None,
        description: str | None = None,
        error: str | None = None,
        checked_at: datetime | None = None,
    ) -> None:
        def update(session) -> None:
            row = session.get(PendingSubmission, submission_id)
            if row is None:
                return
            row.status = status
            row.archive_state = archive_state
            row.state_description = description
            row.last_error = error
            row.checked_at = checked_at
            row.updated_at = _utc_now()
        self.database.run_write(update)

    async def _run(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("投稿审核队列检查失败")
            await asyncio.sleep(self.check_interval)
