import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import FileItem, UploadStreamer
from biliup.services import bilibili as bili_service
from biliup.services.hooks import HookRunner
from biliup.services.scheduler import RecordingScheduler, WorkerState, recording_allowed
from biliup.services.uploader import upload_files


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

    monkeypatch.setattr("biliup.services.uploader.BiliWeb", FakeBiliWeb)

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
