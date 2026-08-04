import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from requests import ConnectionError as RequestsConnectionError
from sqlalchemy import select

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import BackgroundJobRecord, FileItem, PendingSubmission, StreamerInfo, UploadStreamer
from biliup.engine import StreamProbeResult
from biliup.integrations import bilibili as bili_service
from biliup.integrations.upload_state import UploadResult
from biliup.integrations.uploader import upload_files
from biliup.services.history import prune_history
from biliup.services.hooks import HookRunner
from biliup.services.jobs import BackgroundJobManager
from biliup.services.recorder import RecorderProcessError, RecorderStorageError
from biliup.services.scheduler import RecordingScheduler, WorkerState, recording_allowed


def test_history_retention_prunes_oldest_database_records(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()
    with database.session_factory.begin() as session:
        for index in range(5):
            info = StreamerInfo(
                name=f"streamer-{index}",
                url=f"https://example.invalid/{index}",
                title=f"title-{index}",
                date=datetime(2026, 1, 1),
                live_cover_path="",
            )
            info.files.append(FileItem(file=f"recording-{index}.mp4"))
            session.add(info)

    with database.session_factory.begin() as session:
        assert prune_history(session, keep=2) == (3, 3)

    with database.session_factory() as session:
        remaining = session.query(StreamerInfo).order_by(StreamerInfo.id.desc()).all()
        assert [row.name for row in remaining] == ["streamer-4", "streamer-3"]
        assert session.query(FileItem).count() == 2
    database.dispose()


def test_recording_allowed_filters_keywords_and_recurring_utc_ranges() -> None:
    assert not recording_allowed("private replay", ["private"], None)
    assert recording_allowed(
        "public",
        [],
        '["2026-01-01T08:00:00Z", "2026-01-01T10:00:00Z"]',
        now=datetime(2030, 5, 4, 9, tzinfo=timezone.utc),
    )
    assert not recording_allowed(
        "public",
        [],
        ["2026-01-01T08:00:00", "2026-01-01T10:00:00"],
        now=datetime(2030, 5, 4, 11),
    )
    assert recording_allowed(
        "public",
        [],
        ["2026-01-01T23:00:00Z", "2026-01-02T04:00:00Z"],
        now=datetime(2030, 5, 4, 1, tzinfo=timezone.utc),
    )
    assert recording_allowed("public", [], "invalid", now=datetime(2030, 5, 4, 1))


async def test_hook_runner_webui_move_and_remove_are_stable(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path / "home").ensure()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    video = paths.downloads / "sample.mp4"
    subtitle = video.with_suffix(".xml")
    video.write_bytes(b"video")
    subtitle.write_text("<i />", encoding="utf-8")
    runner = HookRunner(paths)

    await runner.run_postprocessors([{"cmd": "mv", "value": "archive"}], [video], {})

    moved = paths.home / "archive" / video.name
    assert moved.read_bytes() == b"video"
    assert moved.with_suffix(".xml").is_file()
    assert not (elsewhere / "archive").exists()

    await runner.run_postprocessors([{"cmd": "rm"}], [moved], {})

    assert not moved.exists()
    assert not moved.with_suffix(".xml").exists()


async def test_uploader_passes_python_biliweb_options(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    video = paths.downloads / "sample.mp4"
    video.write_bytes(b"video")
    captured: dict[str, Any] = {}

    class FakeBiliWeb:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def upload(self, files):
            captured["files"] = files

    monkeypatch.setattr("biliup.integrations.uploader.BiliWeb", FakeBiliWeb)

    await upload_files(
        ["sample.mp4"],
        {
            "template_name": "test",
            "uploader": "bili_web",
            "user_cookie": "data/account.json",
            "submit_api": "client",
            "lines": "bda2",
            "threads": 6,
            "user": {"access_token": "not-logged"},
        },
        paths,
    )

    assert captured["submit_api"] == "client"
    assert captured["lines"] == "bda2"
    assert captured["threads"] == 6
    assert captured["user"] == {"access_token": "not-logged"}
    assert captured["user_cookie"] == str(paths.home / "data" / "account.json")
    assert captured["files"][0].video == str(video)

    captured.clear()
    await upload_files(
        ["sample.mp4"],
        {"template_name": "legacy", "uploader": "bili_web_sync"},
        paths,
    )
    assert captured["files"][0].video == str(video)

    with pytest.raises(ValueError, match="Unknown uploader"):
        await upload_files(["sample.mp4"], {"uploader": "unsupported"}, paths)

    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"video")
    with pytest.raises(ValueError, match="outside the downloads directory"):
        await upload_files([str(outside)], {"uploader": "Noop"}, paths)


async def test_scheduler_retries_upload_and_keeps_files_after_final_failure(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    with database.session_factory.begin() as session:
        template = UploadStreamer(template_name="default", uploader="bili_web", tags=[])
        session.add(template)
        session.flush()
        template_id = template.id
    config = ConfigStore(
        {
            "filtering_threshold": 0,
            "max_upload_limit": 2,
            "delay": 0,
            "submit_api": "web",
            "lines": "AUTO",
            "threads": 3,
            "user": {"access_token": "global"},
        }
    )
    scheduler = RecordingScheduler(database, paths, config, enabled=False)
    attempts: list[dict[str, Any]] = []

    async def failing_upload(_files, params, _paths):
        attempts.append(params)
        raise RequestsConnectionError("upload unavailable")

    async def immediate_sleep(_delay):
        return None

    class FakeRecorder:
        def __init__(self, spec):
            self.spec = spec
            self.file = spec.output_dir / "stable-name.mp4"

        def prepare_stem(self):
            return "stable-name"

        async def run(self, callback):
            self.file.write_bytes(b"recording")
            await callback(self.file)
            return [self.file]

        async def stop(self):
            return None

    class FakeChecker:
        room_title = "Live title"
        raw_stream_url = "https://example.invalid/live.flv"
        stream_headers: dict[str, str] = {}
        live_cover_url = None
        danmaku = None

        async def aprobe_stream(self, is_check=False):
            return StreamProbeResult.offline()

        def danmaku_init(self, _filename_prefix=None):
            return None

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", FakeRecorder)
    monkeypatch.setattr("biliup.services.scheduler.upload_files", failing_upload)
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)
    state = WorkerState(streamer_id=7)
    payload = {
        "id": 7,
        "url": "https://example.invalid/room",
        "remark": "demo",
        "filename_prefix": "stable-name",
        "format": "mp4",
        "override": {"lines": "bda2", "threads": 5, "submit_api": "client", "user": {"mid": 1}},
        "preprocessor": None,
        "segment_processor": None,
        "downloaded_processor": None,
        "postprocessor": None,
        "opt_args": None,
        "upload_streamers_id": template_id,
    }

    await scheduler._record_active(state, payload, FakeChecker())

    assert len(attempts) == 2
    assert attempts[0]["lines"] == "bda2"
    assert attempts[0]["threads"] == 5
    assert attempts[0]["submit_api"] == "client"
    assert attempts[0]["user"] == {"mid": 1}
    assert state.upload_status == "Error"
    assert state.error == "upload unavailable"
    assert (paths.downloads / "stable-name.mp4").read_bytes() == b"recording"
    with database.session_factory() as session:
        assert session.query(FileItem).count() == 1
    database.dispose()


async def test_scheduler_blocks_new_recording_when_disk_space_is_low(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"min_free_disk_gb": 5, "event_loop_interval": 1}),
        enabled=False,
    )
    state = WorkerState(streamer_id=7)
    recording_started = False

    async def should_not_record(*_args):
        nonlocal recording_started
        recording_started = True

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr(
        "biliup.services.scheduler.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=10 * 1024**3, used=9 * 1024**3, free=1024**3),
    )
    monkeypatch.setattr(scheduler, "_record_active", should_not_record)
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)

    await scheduler._record(state, scheduler_payload(), SchedulerChecker())

    assert recording_started is False
    assert state.status == "Degraded"
    assert state.error == "Free disk space is 1.00 GB; at least 5 GB is required"
    database.dispose()


async def test_scheduler_keeps_partial_recording_when_disk_becomes_low(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"filtering_threshold": 0, "min_free_disk_gb": 5}),
        enabled=False,
    )

    class LowDiskRecorder:
        def __init__(self, spec):
            self.file = spec.output_dir / "partial.mp4"

        def prepare_stem(self):
            return "partial"

        async def run(self, _callback):
            self.file.write_bytes(b"partial")
            raise RecorderStorageError(1024**3, 5 * 1024**3)

        def output_files(self):
            return [self.file]

        async def stop(self):
            return None

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", LowDiskRecorder)
    monkeypatch.setattr(
        "biliup.services.scheduler.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=10 * 1024**3, used=9 * 1024**3, free=1024**3),
    )
    state = WorkerState(streamer_id=7)

    await scheduler._record_active(state, scheduler_payload(), SchedulerChecker())

    assert state.status == "Degraded"
    assert state.error == "Free disk space is 1.00 GB; at least 5 GB is required"
    assert (paths.downloads / "partial.mp4").read_bytes() == b"partial"
    with database.session_factory() as session:
        assert session.query(StreamerInfo).count() == 1
        assert session.query(FileItem).count() == 1
    database.dispose()


async def test_scheduler_clears_transient_upload_error_after_retry_success(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    with database.session_factory.begin() as session:
        template = UploadStreamer(template_name="default", uploader="bili_web", tags=[])
        session.add(template)
        session.flush()
        template_id = template.id
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"max_upload_limit": 3, "delay": 0, "submit_api": "web", "lines": "AUTO", "threads": 3}),
        enabled=False,
    )
    attempts = 0

    async def eventually_succeeds(_files, _params, _paths):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RequestsConnectionError("temporary failure")
        return UploadResult(123, "BV123", "mid:1", "cookies.json")

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr("biliup.services.scheduler.upload_files", eventually_succeeds)
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)
    state = WorkerState(streamer_id=9)
    payload = {"upload_streamers_id": template_id, "override": {}}
    context = {"url": "https://example.invalid/room", "room_title": "title", "name": "demo", "start_time": 0}

    succeeded = await scheduler._upload(state, payload, context, [paths.downloads / "sample.mp4"])

    assert succeeded == UploadResult(123, "BV123", "mid:1", "cookies.json")
    assert attempts == 3
    assert state.upload_status == "Idle"
    assert state.error is None
    database.dispose()


async def test_bilibili_qrcode_polling_uses_async_client(monkeypatch) -> None:
    responses = [
        {"code": 86039, "message": "not scanned"},
        {"code": 0, "data": {"token_info": {"mid": 123}}},
    ]
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    async def fake_post(url, *, data, timeout):
        calls.append((url, data))
        return FakeResponse(responses.pop(0))

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr(bili_service.client, "post", fake_post)
    monkeypatch.setattr(bili_service.asyncio, "sleep", immediate_sleep)

    result = await bili_service.login_by_qrcode({"data": {"auth_code": "secret-code"}})

    assert result["data"]["token_info"]["mid"] == 123
    assert len(calls) == 2
    assert all(call[1]["auth_code"] == "secret-code" for call in calls)
    assert all("sign" in call[1] for call in calls)
    assert "secret-code" not in json.dumps(result)


async def test_background_jobs_keep_only_recent_terminal_entries() -> None:
    manager = BackgroundJobManager(max_completed=5)
    submitted = [manager.submit("test", lambda: asyncio.sleep(0)) for _ in range(20)]
    await asyncio.gather(*tuple(manager.tasks.values()))
    await asyncio.sleep(0)

    assert list(manager.jobs) == [job.id for job in submitted[-5:]]
    assert not manager.tasks


async def test_background_job_logs_start_and_completion(caplog) -> None:
    manager = BackgroundJobManager()

    with caplog.at_level("INFO", logger="biliup.jobs"):
        job = manager.submit("upload", lambda: asyncio.sleep(0))
        await manager.tasks[job.id]

    messages = [record.getMessage() for record in caplog.records]
    assert any(f"Background upload job {job.id} started" in message for message in messages)
    assert any(f"Background upload job {job.id} completed in" in message for message in messages)


async def test_background_jobs_persist_across_manager_restarts(tmp_path: Path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.migrate()
    manager = BackgroundJobManager(database)

    completed = manager.submit("upload", lambda: asyncio.sleep(0))
    await manager.tasks[completed.id]
    await asyncio.sleep(0)

    restored = BackgroundJobManager(database)
    assert restored.get(completed.id) == completed


async def test_background_job_errors_are_redacted_before_persistence(tmp_path: Path) -> None:
    async def fail_with_secret() -> None:
        raise RuntimeError("signature=upload-signature Cookie=session-secret")

    database = Database(tmp_path / "jobs.sqlite3")
    database.migrate()
    manager = BackgroundJobManager(database)
    failed = manager.submit("upload", fail_with_secret)
    await manager.tasks[failed.id]

    restored = BackgroundJobManager(database).get(failed.id)
    assert restored is not None
    assert restored.error == "signature=<redacted> Cookie=<redacted>"


async def test_background_jobs_bound_active_work_and_release_idempotency_keys() -> None:
    manager = BackgroundJobManager(active_limits={"upload": 1})
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_upload() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    first = manager.submit("upload", blocked_upload, idempotency_key="same-upload")
    await started.wait()
    duplicate = manager.submit("upload", blocked_upload, idempotency_key="same-upload")

    assert duplicate.id == first.id
    assert calls == 1
    with pytest.raises(RuntimeError, match="Too many active upload jobs"):
        manager.submit("upload", blocked_upload, idempotency_key="different-upload")

    release.set()
    await manager.tasks[first.id]
    second = manager.submit("upload", blocked_upload, idempotency_key="same-upload")
    assert second.id != first.id
    await manager.tasks[second.id]
    assert calls == 2


async def test_scheduler_stop_cancels_recording_before_waiting_for_review(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    scheduler = RecordingScheduler(database, paths, ConfigStore({}), enabled=False)
    stopped = False

    class FakeReview:
        async def stop(self) -> None:
            return None

    class FakeRecorder:
        async def stop(self) -> None:
            nonlocal stopped
            stopped = True

    scheduler.submission_reviews = FakeReview()
    state = WorkerState(streamer_id=1)
    state.recorder = FakeRecorder()
    state.recording_task = asyncio.create_task(asyncio.sleep(60))
    state.task = asyncio.create_task(asyncio.sleep(60))
    scheduler.workers[1] = state

    await scheduler.stop()

    assert scheduler._closing is True
    assert stopped is True
    assert state.recording_task.cancelled()
    assert state.task.cancelled()
    assert scheduler.workers == {}
    database.dispose()


def test_background_jobs_mark_interrupted_work_and_prune_old_records(tmp_path: Path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.migrate()
    with database.session_factory() as session:
        session.add(BackgroundJobRecord(job_id="running-job", kind="upload", status="Running"))
        session.add_all(
            BackgroundJobRecord(job_id=f"finished-{index}", kind="upload", status="Completed")
            for index in range(5)
        )
        session.commit()

    manager = BackgroundJobManager(database, max_completed=3)

    interrupted = manager.get("running-job")
    assert interrupted is not None
    assert interrupted.status == "Cancelled"
    assert interrupted.error == "Application restarted before the job completed"
    assert manager.get("finished-0") is None
    with database.session_factory() as session:
        records = list(
            session.scalars(select(BackgroundJobRecord).order_by(BackgroundJobRecord.id))
        )
    assert {record.job_id for record in records} == {"finished-3", "finished-4", "running-job"}


async def test_hook_timeout_terminates_child_process(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    marker = tmp_path / "orphaned.txt"
    command = (
        f'"{sys.executable}" -c "import pathlib,time; time.sleep(0.5); '
        f"pathlib.Path(r'{marker}').write_text('orphaned')\""
    )
    runner = HookRunner(paths, timeout=0.1)

    with pytest.raises(TimeoutError, match="Hook timed out"):
        await runner.run_commands([{"run": command}], {})
    await asyncio.sleep(0.6)

    assert not marker.exists()


def scheduler_payload(*, upload_streamers_id: int | None = None, parallel: bool = False) -> dict[str, Any]:
    return {
        "id": 7,
        "url": "https://example.invalid/room",
        "remark": "demo",
        "filename_prefix": "stable-name",
        "format": "mp4",
        "override": {"segment_processor_parallel": parallel},
        "preprocessor": None,
        "segment_processor": [{"run": "ignored"}] if parallel else None,
        "downloaded_processor": None,
        "postprocessor": None,
        "opt_args": None,
        "upload_streamers_id": upload_streamers_id,
    }


class SchedulerChecker:
    room_title = "Live title"
    raw_stream_url = "https://example.invalid/live.flv"
    stream_headers: dict[str, str] = {}
    live_cover_url = None
    danmaku = None

    async def aprobe_stream(self, is_check=False):
        return StreamProbeResult.offline()

    def danmaku_init(self, _filename_prefix=None):
        return None


async def test_scheduler_pause_cancels_recording_without_history_or_upload(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(database, paths, ConfigStore({"filtering_threshold": 0}), enabled=False)
    started = asyncio.Event()
    stopped = asyncio.Event()
    uploaded = False

    class BlockingRecorder:
        def __init__(self, spec):
            self.file = spec.output_dir / "partial.mp4"

        def prepare_stem(self):
            return "partial"

        async def run(self, callback):
            self.file.write_bytes(b"partial")
            await callback(self.file)
            started.set()
            await asyncio.Event().wait()

        async def stop(self):
            stopped.set()

    async def should_not_upload(*_args):
        nonlocal uploaded
        uploaded = True
        return True

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", BlockingRecorder)
    monkeypatch.setattr(scheduler, "_upload", should_not_upload)
    state = WorkerState(streamer_id=7)
    task = asyncio.create_task(
        scheduler._record_active(state, scheduler_payload(upload_streamers_id=1), SchedulerChecker())
    )
    state.recording_task = task
    scheduler.workers[state.streamer_id] = state
    await asyncio.wait_for(started.wait(), timeout=1)

    await scheduler.toggle_pause(state.streamer_id)

    assert stopped.is_set()
    assert task.cancelled()
    assert state.status == "Paused"
    assert not uploaded
    with database.session_factory() as session:
        assert session.query(StreamerInfo).count() == 0
        assert session.query(FileItem).count() == 0
    database.dispose()


async def test_download_slot_is_released_before_upload(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    with database.session_factory.begin() as session:
        template = UploadStreamer(template_name="default", uploader="Noop", tags=[])
        session.add(template)
        session.flush()
        template_id = template.id
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"pool1_size": 1, "filtering_threshold": 0, "delay": 0}),
        enabled=False,
    )

    class FastRecorder:
        def __init__(self, spec):
            self.file = spec.output_dir / "complete.mp4"

        def prepare_stem(self):
            return "complete"

        async def run(self, callback):
            self.file.write_bytes(b"complete")
            await callback(self.file)

        async def stop(self):
            return None

    async def assert_slot_released(*_args):
        assert not scheduler.download_semaphore.locked()
        return UploadResult(123, "BV123", "mid:1", "cookies.json")

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", FastRecorder)
    monkeypatch.setattr(scheduler, "_upload", assert_slot_released)
    payload = scheduler_payload(upload_streamers_id=template_id)
    payload["postprocessor"] = ["rm"]
    await scheduler._record_active(
        WorkerState(streamer_id=7),
        payload,
        SchedulerChecker(),
    )
    assert (paths.downloads / "complete.mp4").exists()
    with database.session_factory() as session:
        assert session.query(PendingSubmission).count() == 1
    database.dispose()


async def test_ffmpeg_failure_does_not_create_empty_history(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(database, paths, ConfigStore({"filtering_threshold": 0}), enabled=False)

    class FailingRecorder:
        def __init__(self, _spec):
            pass

        def prepare_stem(self):
            return "failed"

        async def run(self, _callback):
            raise RuntimeError("ffmpeg failed")

        async def stop(self):
            return None

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", FailingRecorder)
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        await scheduler._record_active(WorkerState(streamer_id=7), scheduler_payload(), SchedulerChecker())

    with database.session_factory() as session:
        assert session.query(StreamerInfo).count() == 0
    database.dispose()


async def test_ffmpeg_process_failure_keeps_completed_file_in_history(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"filtering_threshold": 0, "delay": 0}),
        enabled=False,
    )

    class InterruptedRecorder:
        def __init__(self, spec):
            self.file = spec.output_dir / "interrupted.mp4"

        def prepare_stem(self):
            return "interrupted"

        async def run(self, _callback):
            self.file.write_bytes(b"recoverable recording")
            raise RecorderProcessError(1)

        def output_files(self):
            return [self.file]

        async def stop(self):
            return None

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", InterruptedRecorder)
    await scheduler._record_active(WorkerState(streamer_id=7), scheduler_payload(), SchedulerChecker())

    with database.session_factory() as session:
        history = session.query(StreamerInfo).one()
        assert Path(history.files[0].file).read_bytes() == b"recoverable recording"
    database.dispose()


async def test_parallel_segment_hooks_are_bounded(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"filtering_threshold": 0, "segment_processor_concurrency": 2, "delay": 0}),
        enabled=False,
    )
    active = 0
    peak = 0

    class SegmentedRecorder:
        def __init__(self, spec):
            self.output_dir = spec.output_dir

        def prepare_stem(self):
            return "segment"

        async def run(self, callback):
            for index in range(8):
                file = self.output_dir / f"segment-{index}.mp4"
                file.write_bytes(b"segment")
                await callback(file)

        async def stop(self):
            return None

    async def bounded_hook(_steps, _payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", SegmentedRecorder)
    monkeypatch.setattr(scheduler.hooks, "run_commands", bounded_hook)
    await scheduler._record_active(WorkerState(streamer_id=7), scheduler_payload(parallel=True), SchedulerChecker())

    assert peak == 2
    database.dispose()


async def test_scheduler_resumes_interrupted_stream_as_one_history_record(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"filtering_threshold": 0, "delay": 0}),
        enabled=False,
    )
    stream_urls: list[str] = []

    class RecoveringRecorder:
        count = 0

        def __init__(self, spec):
            self.spec = spec
            self.index = RecoveringRecorder.count
            RecoveringRecorder.count += 1
            self.file = spec.output_dir / f"part-{self.index}.mp4"

        def prepare_stem(self):
            return f"part-{self.index}"

        async def run(self, callback):
            stream_urls.append(self.spec.stream_url)
            self.file.write_bytes(f"part-{self.index}".encode())
            await callback(self.file)

        async def stop(self):
            return None

    class RecoveringChecker(SchedulerChecker):
        def __init__(self):
            self.probes = 0
            self.raw_stream_url = "https://example.invalid/live-1.flv"

        async def aprobe_stream(self, is_check=False):
            self.probes += 1
            if self.probes == 1:
                self.raw_stream_url = "https://example.invalid/live-2.flv"
                return StreamProbeResult.live()
            return StreamProbeResult.offline()

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", RecoveringRecorder)
    await scheduler._record_active(WorkerState(streamer_id=7), scheduler_payload(), RecoveringChecker())

    assert stream_urls == [
        "https://example.invalid/live-1.flv",
        "https://example.invalid/live-2.flv",
    ]
    with database.session_factory() as session:
        history = session.query(StreamerInfo).all()
        assert len(history) == 1
        assert [Path(item.file).name for item in history[0].files] == ["part-0.mp4", "part-1.mp4"]
    database.dispose()


async def test_offline_confirmation_requires_consecutive_results(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"delay": 1, "checker_sleep": 1}),
        enabled=False,
    )
    probes = [
        StreamProbeResult.offline(),
        StreamProbeResult.unknown("temporary failure"),
        StreamProbeResult.offline(),
        StreamProbeResult.offline(),
        StreamProbeResult.offline(),
    ]

    class ConfirmingChecker(SchedulerChecker):
        async def aprobe_stream(self, is_check=False):
            return probes.pop(0)

    ticks = iter([0.0, 2.0])

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr(scheduler, "_clock", lambda: next(ticks))
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)
    recovered = await scheduler._wait_for_stream_recovery(WorkerState(streamer_id=7), ConfirmingChecker())

    assert recovered is False
    assert probes == []
    database.dispose()


async def test_recorder_failures_back_off_and_open_circuit(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore(
            {
                "filtering_threshold": 0,
                "delay": 0,
                "recorder_retry_limit": 2,
                "recorder_retry_backoff": 1,
            }
        ),
        enabled=False,
    )
    sleeps: list[float] = []

    class FailingThenEndingRecorder:
        count = 0

        def __init__(self, spec):
            self.spec = spec
            self.index = FailingThenEndingRecorder.count
            FailingThenEndingRecorder.count += 1
            self.file = spec.output_dir / f"retry-{self.index}.mp4"

        def prepare_stem(self):
            return f"retry-{self.index}"

        async def run(self, callback):
            self.file.write_bytes(f"retry-{self.index}".encode())
            await callback(self.file)
            if self.index < 2:
                raise RecorderProcessError(1)

        def output_files(self):
            return [self.file]

        async def stop(self):
            return None

    class RetryChecker(SchedulerChecker):
        def __init__(self):
            self.probes = 0
            self.raw_stream_url = "https://example.invalid/live.flv"

        async def aprobe_stream(self, is_check=False):
            self.probes += 1
            if self.probes <= 4:
                return StreamProbeResult.live()
            return StreamProbeResult.offline()

    async def immediate_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", FailingThenEndingRecorder)
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)
    monkeypatch.setattr("biliup.services.scheduler.random.uniform", lambda _start, _end: 0)
    await scheduler._record_active(WorkerState(streamer_id=7), scheduler_payload(), RetryChecker())

    assert sleeps == [1, 300]
    with database.session_factory() as session:
        history = session.query(StreamerInfo).one()
        assert [Path(item.file).name for item in history.files] == [
            "retry-0.mp4",
            "retry-1.mp4",
            "retry-2.mp4",
        ]
    database.dispose()
