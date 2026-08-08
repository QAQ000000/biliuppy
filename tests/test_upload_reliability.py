import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, current_thread
from time import sleep

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import PendingSubmission, UploadAccountState
from biliup.integrations import uploader as uploader_module
from biliup.integrations.upload_errors import (
    TransientUploadError,
    UploadCancelledError,
    UploadOutcomeUnknownError,
    UploadRejectedError,
    is_transient_upload_error,
)
from biliup.integrations.upload_state import (
    SubmitDelayError,
    UploadResult,
    UploadStateStore,
    account_key_for,
)
from biliup.integrations.uploader import upload_files
from biliup.services.jobs import BackgroundJobManager
from biliup.services.submission_review import SubmissionReviewService


def test_uploaded_part_is_reused_and_removed_after_submit(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video-data")
    store = UploadStateStore(database, "mid:1")

    assert store.find_part(video) is None
    store.save_part(video, {"filename": "server-file", "title": "part", "desc": ""})
    assert store.find_part(video) == {"filename": "server-file", "title": "part", "desc": ""}

    store.remove_parts([video])
    assert store.find_part(video) is None
    database.dispose()


def test_account_submit_gate_serializes_callbacks(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    store = UploadStateStore(database, "mid:1")
    state_lock = Lock()
    active = 0
    max_active = 0

    def submit() -> str:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        sleep(0.03)
        with state_lock:
            active -= 1
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: store.submit(submit, 0), range(2)))

    assert results == ["ok", "ok"]
    assert max_active == 1
    with database.session_factory() as session:
        assert session.scalar(select(UploadAccountState).where(UploadAccountState.account_key == "mid:1"))
    database.dispose()


def test_account_key_uses_cookie_user_id(tmp_path: Path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(
        json.dumps(
            {
                "cookie_info": {
                    "cookies": [
                        {"name": "SESSDATA", "value": "secret"},
                        {"name": "DedeUserID", "value": "123456"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert account_key_for(cookie_path, {}) == "mid:123456"


def test_account_key_rejects_configured_user_mismatch(tmp_path: Path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(
        json.dumps(
            {
                "cookie_info": {
                    "cookies": [
                        {"name": "SESSDATA", "value": "secret"},
                        {"name": "DedeUserID", "value": "123456"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match cookie account"):
        account_key_for(cookie_path, {"mid": "654321"})


def test_account_submit_gate_defers_without_sleeping(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    store = UploadStateStore(database, "mid:1")

    assert store.submit(lambda: "first", 60) == "first"
    with pytest.raises(SubmitDelayError) as exc_info:
        store.submit(lambda: "second", 60)

    assert 0 < exc_info.value.retry_after <= 60
    database.dispose()


def test_submission_success_survives_timestamp_persistence_failure(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    store = UploadStateStore(database, "mid:1")
    submitted = 0

    def submit() -> str:
        nonlocal submitted
        submitted += 1
        return "remote-success"

    monkeypatch.setattr(database, "run_write", lambda _operation: (_ for _ in ()).throw(OSError("disk full")))

    with caplog.at_level("ERROR", logger="biliup.upload_state"):
        result = store.submit(submit, 0)

    assert result == "remote-success"
    assert submitted == 1
    assert "Submission succeeded but its local timestamp could not be saved" in caplog.text
    database.dispose()


def test_database_uses_wal_and_retries_busy_writes(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000

    attempts = 0

    def transiently_locked(_session) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("insert", {}, sqlite3.OperationalError("database is locked"))
        return "ok"

    assert database.run_write(transiently_locked) == "ok"
    assert attempts == 2
    database.dispose()


@pytest.mark.asyncio
async def test_upload_waits_asynchronously_and_retries(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    attempts = 0
    thread_names: list[str] = []

    def deferred_once(_files, _params, _paths, _database):
        nonlocal attempts
        attempts += 1
        thread_names.append(current_thread().name)
        if attempts == 1:
            raise SubmitDelayError(0)
        return UploadResult(1, "BV1", "mid:1", "cookies.json")

    monkeypatch.setattr(uploader_module, "_upload_sync", deferred_once)

    result = await upload_files([video.name], {}, paths)

    assert result == UploadResult(1, "BV1", "mid:1", "cookies.json")
    assert attempts == 2
    assert all(name.startswith("biliup-upload") for name in thread_names)
    await uploader_module.shutdown_upload_executor()


@pytest.mark.asyncio
async def test_manual_upload_retries_transient_failure_with_same_params(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    attempts = 0
    params: dict = {}

    def fail_one_line_then_succeed(_files, current_params, _paths, _database):
        nonlocal attempts
        attempts += 1
        excluded = current_params.setdefault("_excluded_upload_lines", [])
        if attempts == 1:
            excluded.append("upos:bda2")
            raise requests.ConnectionError("line unavailable")
        assert excluded == ["upos:bda2"]
        return UploadResult(1, "BV1", "mid:1", "cookies.json")

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr(uploader_module, "_upload_sync", fail_one_line_then_succeed)
    monkeypatch.setattr("biliup.integrations.uploader.asyncio.sleep", immediate_sleep)

    result = await upload_files([video.name], params, paths, max_attempts=2)

    assert result == UploadResult(1, "BV1", "mid:1", "cookies.json")
    assert attempts == 2
    await uploader_module.shutdown_upload_executor()


@pytest.mark.asyncio
async def test_upload_does_not_retry_permanent_failure(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    attempts = 0

    def invalid_upload(_files, _params, _paths, _database):
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid title")

    monkeypatch.setattr(uploader_module, "_upload_sync", invalid_upload)

    with pytest.raises(ValueError, match="invalid title"):
        await upload_files([video.name], {}, paths, max_attempts=3)

    assert attempts == 1
    await uploader_module.shutdown_upload_executor()


@pytest.mark.asyncio
async def test_upload_does_not_retry_unknown_submission_outcome(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    attempts = 0

    def uncertain_upload(_files, _params, _paths, _database):
        nonlocal attempts
        attempts += 1
        raise UploadOutcomeUnknownError("submit response was lost")

    monkeypatch.setattr(uploader_module, "_upload_sync", uncertain_upload)

    with pytest.raises(UploadOutcomeUnknownError, match="response was lost"):
        await upload_files([video.name], {}, paths, max_attempts=3)

    assert attempts == 1
    await uploader_module.shutdown_upload_executor()


@pytest.mark.asyncio
async def test_running_upload_success_is_preserved_during_shutdown(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    started = Event()
    release = Event()

    def blocking_upload(_files, _params, _paths, _database):
        started.set()
        release.wait(2)
        return UploadResult(1, "BV1", "mid:1", "cookies.json")

    monkeypatch.setattr(uploader_module, "_upload_sync", blocking_upload)
    manager = BackgroundJobManager()
    job = manager.submit("upload", lambda: upload_files([video.name], {}, paths))
    for _attempt in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    await asyncio.wait_for(shutdown, 2)

    assert job.status == "Completed"
    assert job.error is None
    await uploader_module.shutdown_upload_executor()


@pytest.mark.asyncio
async def test_queued_upload_is_cancelled_before_it_starts(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    first_video = paths.downloads / "first.mp4"
    queued_video = paths.downloads / "queued.mp4"
    first_video.write_bytes(b"first")
    queued_video.write_bytes(b"queued")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-upload")
    first_started = Event()
    release_first = Event()
    calls: list[str] = []

    def controlled_upload(files, _params, _paths, _database):
        name = files[0].name
        calls.append(name)
        if name == first_video.name:
            first_started.set()
            release_first.wait(2)
        return UploadResult(1, "BV1", "mid:1", "cookies.json")

    monkeypatch.setattr(uploader_module, "_get_upload_executor", lambda: executor)
    monkeypatch.setattr(uploader_module, "_upload_sync", controlled_upload)
    first = asyncio.create_task(upload_files([first_video.name], {}, paths))
    try:
        assert await asyncio.to_thread(first_started.wait, 1)
        queued = asyncio.create_task(upload_files([queued_video.name], {}, paths))
        await asyncio.sleep(0)
        queued.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(queued, 0.5)
        assert calls == [first_video.name]

        release_first.set()
        await asyncio.wait_for(first, 2)
        await asyncio.sleep(0.05)
        assert calls == [first_video.name]
    finally:
        release_first.set()
        await asyncio.gather(first, return_exceptions=True)
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_running_upload_cancellation_reaches_worker_thread(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    started = Event()
    stopped = Event()

    def cancellable_upload(_files, params, _paths, _database):
        started.set()
        assert params["_cancel_event"].wait(1)
        stopped.set()
        raise UploadCancelledError("cancelled")

    monkeypatch.setattr(uploader_module, "_upload_sync", cancellable_upload)
    task = asyncio.create_task(upload_files([video.name], {}, paths))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert stopped.is_set()
    finally:
        await uploader_module.shutdown_upload_executor()


def test_only_transient_upload_errors_are_retried() -> None:
    assert is_transient_upload_error(requests.ConnectionError("connection reset")) is True
    assert is_transient_upload_error(TransientUploadError("browser navigation timed out")) is True
    assert is_transient_upload_error(UploadOutcomeUnknownError("submit response was lost")) is False
    assert is_transient_upload_error(UploadRejectedError({"code": -412, "message": "risk control"})) is False
    assert is_transient_upload_error(ValueError("invalid title")) is False


@pytest.mark.asyncio
async def test_approved_submission_removes_sources(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    video = paths.downloads / "approved.mp4"
    subtitle = video.with_suffix(".xml")
    video.write_bytes(b"video")
    subtitle.write_text("danmaku", encoding="utf-8")
    service = SubmissionReviewService(database, paths)
    service.enqueue(UploadResult(123, "BV123", "mid:1", "cookies.json"), [video])
    monkeypatch.setattr(service, "_fetch_archive_state", lambda _aid, _cookie: (0, "approved"))

    await service.check_once()

    assert not video.exists()
    assert not subtitle.exists()
    with database.session_factory() as session:
        row = session.scalar(select(PendingSubmission).where(PendingSubmission.aid == 123))
        assert row is not None
        assert row.status == "approved"
    database.dispose()


@pytest.mark.asyncio
async def test_rejected_submission_keeps_sources(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    video = paths.downloads / "rejected.mp4"
    video.write_bytes(b"video")
    service = SubmissionReviewService(database, paths)
    service.enqueue(UploadResult(456, "BV456", "mid:1", "cookies.json"), [video])
    monkeypatch.setattr(service, "_fetch_archive_state", lambda _aid, _cookie: (100, "rejected"))

    await service.check_once()

    assert video.exists()
    with database.session_factory() as session:
        row = session.scalar(select(PendingSubmission).where(PendingSubmission.aid == 456))
        assert row is not None
        assert row.status == "rejected"
    database.dispose()
