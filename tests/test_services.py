import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import FileItem, StreamerInfo, UploadStreamer
from biliup.integrations import bilibili as bili_service
from biliup.integrations.uploader import upload_files
from biliup.services.hooks import HookRunner
from biliup.services.jobs import BackgroundJobManager
from biliup.services.scheduler import RecordingScheduler, WorkerState, recording_allowed


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
        raise RuntimeError("upload unavailable")

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
            raise RuntimeError("temporary failure")

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr("biliup.services.scheduler.upload_files", eventually_succeeds)
    monkeypatch.setattr("biliup.services.scheduler.asyncio.sleep", immediate_sleep)
    state = WorkerState(streamer_id=9)
    payload = {"upload_streamers_id": template_id, "override": {}}
    context = {"url": "https://example.invalid/room", "room_title": "title", "name": "demo", "start_time": 0}

    succeeded = await scheduler._upload(state, payload, context, [paths.downloads / "sample.mp4"])

    assert succeeded is True
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
    submitted = [manager.submit("test", asyncio.sleep(0)) for _ in range(20)]
    await asyncio.gather(*tuple(manager.tasks.values()))
    await asyncio.sleep(0)

    assert list(manager.jobs) == [job.id for job in submitted[-5:]]
    assert not manager.tasks


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
        ConfigStore({"pool1_size": 1, "filtering_threshold": 0}),
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
        return True

    monkeypatch.setattr("biliup.services.scheduler.FFmpegRecorder", FastRecorder)
    monkeypatch.setattr(scheduler, "_upload", assert_slot_released)
    await scheduler._record_active(
        WorkerState(streamer_id=7),
        scheduler_payload(upload_streamers_id=template_id),
        SchedulerChecker(),
    )
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


async def test_parallel_segment_hooks_are_bounded(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    scheduler = RecordingScheduler(
        database,
        paths,
        ConfigStore({"filtering_threshold": 0, "segment_processor_concurrency": 2}),
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
    await scheduler._record_active(
        WorkerState(streamer_id=7), scheduler_payload(parallel=True), SchedulerChecker()
    )

    assert peak == 2
    database.dispose()
