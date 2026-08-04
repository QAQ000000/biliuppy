from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from biliup.database.models import UploadAccountState, UploadPartCache
from biliup.database.session import Database

T = TypeVar("T")
_submit_locks: dict[str, threading.Lock] = {}
_submit_locks_guard = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class UploadResult:
    aid: int | None
    bvid: str | None
    account_key: str
    cookie_path: str


class SubmitDelayError(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(f"Bilibili submission is rate limited for another {self.retry_after:.1f} seconds")


def account_lock_for(account_key: str) -> threading.Lock:
    with _submit_locks_guard:
        return _submit_locks.setdefault(account_key, threading.Lock())


def _cookie_account_id(cookie_path: Path) -> str | None:
    try:
        with cookie_path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    cookies = payload.get("cookie_info", {}).get("cookies", []) if isinstance(payload, dict) else []
    if not isinstance(cookies, list):
        return None
    for cookie in cookies:
        if not isinstance(cookie, dict) or cookie.get("name") != "DedeUserID":
            continue
        value = str(cookie.get("value") or "").strip()
        if value:
            return value
    return None


def account_key_for(cookie_path: Path, user: dict[str, Any]) -> str:
    mid = user.get("mid") or user.get("uid") or _cookie_account_id(cookie_path)
    if mid:
        return f"mid:{mid}"
    digest = hashlib.sha256(str(cookie_path.resolve()).casefold().encode("utf-8")).hexdigest()
    return f"cookie:{digest[:64]}"


def quick_file_identity(path: str | Path) -> tuple[str, int]:
    target = Path(path)
    size = target.stat().st_size
    digest = hashlib.sha256(str(size).encode("ascii"))
    sample_size = 1024 * 1024
    with target.open("rb") as stream:
        digest.update(stream.read(sample_size))
        if size > sample_size:
            stream.seek(max(sample_size, size - sample_size))
            digest.update(stream.read(sample_size))
    return digest.hexdigest(), size


class UploadStateStore:
    CACHE_TTL = timedelta(days=3)

    def __init__(self, database: Database, account_key: str) -> None:
        self.database = database
        self.account_key = account_key

    @contextmanager
    def account_guard(self):
        with account_lock_for(self.account_key):
            yield

    def find_part(self, path: str | Path) -> dict[str, str] | None:
        file_hash, file_size = quick_file_identity(path)
        now = _utc_now()

        def lookup(session):
            session.execute(delete(UploadPartCache).where(UploadPartCache.expires_at <= now))
            row = session.scalar(
                select(UploadPartCache).where(
                    UploadPartCache.file_hash == file_hash,
                    UploadPartCache.file_size == file_size,
                    UploadPartCache.account_key == self.account_key,
                )
            )
            if row is None:
                return None
            return {"title": row.title, "filename": row.filename, "desc": row.description}

        return self.database.run_write(lookup)

    def save_part(self, path: str | Path, part: dict[str, Any]) -> None:
        file_hash, file_size = quick_file_identity(path)
        values = {
            "file_hash": file_hash,
            "file_size": file_size,
            "account_key": self.account_key,
            "filename": str(part["filename"]),
            "title": str(part.get("title") or Path(path).stem),
            "description": str(part.get("desc") or ""),
            "expires_at": _utc_now() + self.CACHE_TTL,
        }
        statement = insert(UploadPartCache).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["file_hash", "file_size", "account_key"],
            set_={key: value for key, value in values.items() if key not in {"file_hash", "file_size", "account_key"}},
        )
        def save(session) -> None:
            session.execute(statement)

        self.database.run_write(save)

    def remove_parts(self, paths: list[str | Path]) -> None:
        identities = [quick_file_identity(path) for path in paths]
        def remove(session) -> None:
            for file_hash, file_size in identities:
                session.execute(
                    delete(UploadPartCache).where(
                        UploadPartCache.file_hash == file_hash,
                        UploadPartCache.file_size == file_size,
                        UploadPartCache.account_key == self.account_key,
                    )
                )

        self.database.run_write(remove)

    def submit(self, callback: Callable[[], T], minimum_interval: int) -> T:
        with account_lock_for(self.account_key):
            with self.database.session_factory() as session:
                state = session.scalar(
                    select(UploadAccountState).where(UploadAccountState.account_key == self.account_key)
                )
                last_submitted_at = state.last_submitted_at if state else None
            if last_submitted_at and minimum_interval > 0:
                elapsed = (_utc_now() - last_submitted_at).total_seconds()
                if elapsed < minimum_interval:
                    raise SubmitDelayError(minimum_interval - elapsed)
            result = callback()
            now = _utc_now()
            statement = insert(UploadAccountState).values(
                account_key=self.account_key,
                last_submitted_at=now,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["account_key"],
                set_={"last_submitted_at": now},
            )
            def save_submission_time(session) -> None:
                session.execute(statement)
            self.database.run_write(save_submission_time)
            return result
