import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from biliup.api import create_app
from biliup.api.app import RedactingFilter, redact_log_message
from biliup.api.routers.logs import TAIL_MAX_BYTES, filter_log_lines, tail_lines
from biliup.core import AppSettings
from biliup.database import Database
from biliup.database.models import Configuration, FileItem, StreamerInfo
from biliup.services import HomeInstanceLockError
from biliup.services.scheduler import WorkerState


def make_client(tmp_path: Path, *, auth: bool = False) -> TestClient:
    settings = AppSettings(
        home=tmp_path,
        auth_enabled=auth,
        scheduler_enabled=False,
        session_secret="test-session-secret",
    )
    return TestClient(create_app(settings))


def test_same_home_allows_only_one_running_server(tmp_path: Path) -> None:
    with make_client(tmp_path) as first:
        assert first.get("/healthz").status_code == 200
        with pytest.raises(HomeInstanceLockError, match="already using BILIUP_HOME"):
            with make_client(tmp_path):
                pass

    with make_client(tmp_path) as restarted:
        assert restarted.get("/healthz").status_code == 200


def test_log_redaction_hides_credentials_and_signatures() -> None:
    message = (
        "request csrf=secret&access_key=mobile-token "
        "X-Amz-Signature=upload-signature upload_id: multipart-id "
        "{'refresh_token': 'refresh-secret'} Cookie: SESSDATA=cookie-secret"
    )

    redacted = redact_log_message(message)

    assert "secret" not in redacted
    assert "mobile-token" not in redacted
    assert "upload-signature" not in redacted
    assert "multipart-id" not in redacted
    assert "refresh-secret" not in redacted
    assert "cookie-secret" not in redacted
    assert redacted.count("<redacted>") == 6


def test_log_redaction_sanitizes_exception_tracebacks() -> None:
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "upload failed", (), None)
    secret_message = "signature=upload-signature Cookie=session-secret"
    try:
        raise RuntimeError(secret_message)
    except RuntimeError:
        record.exc_info = sys.exc_info()

    RedactingFilter().filter(record)
    formatted = logging.Formatter("%(message)s").format(record)

    assert "upload-signature" not in formatted
    assert "session-secret" not in formatted
    assert formatted.count("<redacted>") == 2


def test_frontend_api_contract(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        initial_config = client.get("/v1/configuration").json()
        assert initial_config["downloader"] == "ffmpeg"
        assert initial_config["delay"] == 60
        assert initial_config["log_file_max_size_mb"] == 10
        assert initial_config["history_max_records"] == 10_000
        assert initial_config["min_free_disk_gb"] == 5

        initial_config["log_file_max_size_mb"] = 25
        updated_config = client.put("/v1/configuration", json=initial_config)
        assert updated_config.status_code == 200
        assert updated_config.json()["log_file_max_size_mb"] == 25
        assert client.app.state.context.log_handler.maxBytes == 25 * 1024 * 1024

        created = client.post(
            "/v1/streamers",
            json={"url": "https://example.invalid/live", "remark": "demo", "format": "mp4"},
        )
        assert created.status_code == 200
        streamer = created.json()
        assert streamer["remark"] == "demo"
        assert streamer["status"] == "Pending"
        assert streamer["paused"] is False
        assert "filename_prefix" in streamer
        assert "upload_streamers_id" in streamer
        assert "upload_status" in streamer
        assert "filename" not in streamer
        assert "upload_id" not in streamer

        streamer["remark"] = "renamed"
        updated = client.put("/v1/streamers", json=streamer)
        assert updated.status_code == 200
        assert updated.json()["remark"] == "renamed"

        streamer_id = streamer["id"]
        client.app.state.context.scheduler.workers[streamer_id] = WorkerState(streamer_id=streamer_id)
        paused = client.put(f"/v1/streamers/{streamer_id}/pause")
        assert paused.json() == {"id": streamer_id, "paused": True, "status": "Paused"}
        resumed = client.put(f"/v1/streamers/{streamer_id}/pause")
        assert resumed.json() == {"id": streamer_id, "paused": False, "status": "Checking"}
        assert client.put("/v1/streamers/999999/pause").status_code == 404

        template = client.post(
            "/v1/upload/streamers",
            json={
                "template_name": "default",
                "user_cookie": "data/cookies.json",
                "title": "{title}",
                "tags": ["biliup"],
                "uploader": "bili_web",
            },
        )
        assert template.status_code == 200
        assert client.get(f"/v1/upload/streamers/{template.json()['id']}").status_code == 200
        user = client.post("/v1/users", json={"key": "bilibili-cookies", "value": "data/cookies.json"})
        assert user.status_code == 200
        assert client.get("/v1/users").json()[0]["platform"] == "bilibili-cookies"

        video = tmp_path / "downloads" / "sample.mp4"
        video.write_bytes(b"video")
        assert client.get("/v1/videos").json()[0]["name"] == "sample.mp4"
        assert client.get("/static/sample.mp4").content == b"video"

        accepted = client.post(
            "/v1/uploads",
            json={
                "files": ["sample.mp4"],
                "params": {"template_name": "manual", "uploader": "Noop"},
            },
        )
        assert accepted.status_code == 200
        task_id = accepted.json()["task"]
        for _ in range(50):
            job = client.get(f"/v1/uploads/{task_id}").json()
            if job["status"] != "Running":
                break
            time.sleep(0.01)
        assert job == {"id": task_id, "kind": "upload", "status": "Completed", "error": None}

        assert client.delete(f"/v1/streamers/{streamer['id']}").status_code == 204

    with make_client(tmp_path) as restarted_client:
        assert restarted_client.get(f"/v1/uploads/{task_id}").json() == job


def test_manual_upload_deduplicates_active_jobs_and_rejects_over_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    started = Event()
    release = Event()
    calls = 0

    async def blocking_upload(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr("biliup.api.routers.uploads.upload_files", blocking_upload)
    with make_client(tmp_path) as client:
        client.app.state.context.jobs.active_limits["upload"] = 1
        for name in ("first.mp4", "second.mp4"):
            (tmp_path / "downloads" / name).write_bytes(name.encode("ascii"))
        payload = {
            "files": ["first.mp4"],
            "params": {"template_name": "manual", "uploader": "Noop"},
        }
        first = client.post("/v1/uploads", json=payload)
        assert started.wait(1)
        duplicate = client.post("/v1/uploads", json=payload)
        over_capacity = client.post(
            "/v1/uploads",
            json={**payload, "files": ["second.mp4"]},
        )

        assert first.status_code == 200
        assert duplicate.status_code == 200
        assert duplicate.json()["task"] == first.json()["task"]
        assert over_capacity.status_code == 429
        assert calls == 1

        release.set()
        task_id = first.json()["task"]
        for _ in range(100):
            if client.get(f"/v1/uploads/{task_id}").json()["status"] == "Completed":
                break
            time.sleep(0.01)
        resubmitted = client.post("/v1/uploads", json=payload)
        assert resubmitted.status_code == 200
        assert resubmitted.json()["task"] != task_id


def test_manual_upload_request_limits_are_enforced(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        empty = client.post(
            "/v1/uploads",
            json={"files": [], "params": {"template_name": "manual"}},
        )
        too_many = client.post(
            "/v1/uploads",
            json={
                "files": [f"video-{index}.mp4" for index in range(101)],
                "params": {"template_name": "manual"},
            },
        )
        config = client.get("/v1/configuration").json()
        config["threads"] = 9
        invalid_threads = client.put("/v1/configuration", json=config)

    assert empty.status_code == 422
    assert too_many.status_code == 422
    assert invalid_threads.status_code == 422


def test_frontend_serves_exported_rsc_paths_and_head(tmp_path: Path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    rsc_file = frontend / "dashboard" / "__next.!token" / "dashboard" / "__PAGE__.txt"
    rsc_file.parent.mkdir(parents=True)
    rsc_file.write_text("rsc payload", encoding="utf-8")
    chunk = frontend / "_next" / "static" / "chunks" / "app.js"
    chunk.parent.mkdir(parents=True)
    chunk.write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setenv("BILIUP_FRONTEND_DIR", str(frontend))

    with make_client(tmp_path) as client:
        rsc_url = "/dashboard/__next.%21token.dashboard.__PAGE__.txt"
        response = client.get(rsc_url)
        head_response = client.head(rsc_url)
        chunk_response = client.get("/_next/static/chunks/app.js")
        missing_response = client.get("/_next/static/chunks/missing.js", follow_redirects=False)

    assert response.status_code == 200
    assert response.text == "rsc payload"
    assert head_response.status_code == 200
    assert head_response.content == b""
    assert chunk_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing_response.status_code == 404
    assert "location" not in missing_response.headers


def test_legacy_null_global_options_do_not_block_startup(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "data.sqlite3")
    database.migrate()
    with database.session_factory() as session:
        session.add(
            Configuration(
                key="config",
                value=json.dumps(
                    {
                        "filename_prefix": None,
                        "segment_processor_parallel": None,
                        "bili_liveapi": None,
                        "segment_time": None,
                    }
                ),
            )
        )
        session.commit()
    database.dispose()

    with make_client(tmp_path) as client:
        config = client.get("/v1/configuration").json()

    assert config["filename_prefix"] == "{streamer}%Y-%m-%d %H_%M_%S{title}"
    assert config["segment_processor_parallel"] is False
    assert config["segment_time"] is None
    assert "bili_liveapi" not in config


def test_bilibili_proxy_rejects_non_bilibili_hosts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/bili/proxy", params={"url": "http://127.0.0.1/private"})

    assert response.status_code == 400
    assert response.json()["detail"] == "only Bilibili image URLs are supported"


def test_qrcode_login_is_stored_under_app_data(tmp_path: Path, monkeypatch) -> None:
    async def fake_login(_payload):
        return {
            "code": 0,
            "data": {
                "token_info": {"mid": 123, "access_token": "test-token"},
                "cookie_info": {"cookies": []},
            },
        }

    monkeypatch.setattr("biliup.api.routers.bilibili.bili_service.login_by_qrcode", fake_login)

    with make_client(tmp_path) as client:
        response = client.post("/v1/login_by_qrcode", json={"data": {"auth_code": "test"}})

    assert response.status_code == 200
    target = Path(response.json()["filename"])
    assert target == (tmp_path / "data" / "123.json").resolve()
    assert json.loads(target.read_text(encoding="utf-8"))["token_info"]["access_token"] == "test-token"


def test_authentication_contract(tmp_path: Path) -> None:
    with make_client(tmp_path, auth=True) as client:
        assert client.get("/v1/users/biliup").status_code == 404
        assert client.get("/v1/configuration").status_code == 401
        assert client.post("/v1/users/register", json={"username": "biliup", "password": "secret"}).status_code == 200
        assert client.get("/v1/configuration").status_code == 200
        assert client.get("/v1/logout").status_code == 204
        assert client.get("/v1/configuration").status_code == 401
        assert client.post("/v1/users/login", json={"username": "biliup", "password": "secret"}).status_code == 200
        assert client.get("/v1/configuration").status_code == 200


def test_streamer_history_is_paginated_and_includes_files(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.context.database
        with database.session_factory.begin() as session:
            for index in range(25):
                info = StreamerInfo(
                    name=f"streamer-{index}",
                    url=f"https://example.invalid/{index}",
                    title=f"title-{index}",
                    date=datetime(2026, 1, 1) + timedelta(minutes=index),
                    live_cover_path="",
                )
                info.files.append(FileItem(file=f"recording-{index}.mp4"))
                session.add(info)

        response = client.get("/v1/streamer-info", params={"page": 2, "page_size": 10})

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "25"
    assert isinstance(response.json(), list)
    assert len(response.json()) == 10
    assert response.json()[0]["name"] == "streamer-14"
    assert response.json()[0]["files"][0]["file"] == "recording-14.mp4"
    assert "T" in response.json()[0]["date"]


def test_clear_streamer_history_keeps_recording_files(tmp_path: Path) -> None:
    recording = tmp_path / "downloads" / "keep.mp4"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"video")

    with make_client(tmp_path) as client:
        database = client.app.state.context.database
        with database.session_factory.begin() as session:
            info = StreamerInfo(
                name="demo",
                url="https://example.invalid/live",
                title="demo title",
                date=datetime(2026, 1, 1),
                live_cover_path="",
            )
            info.files.append(FileItem(file=str(recording)))
            session.add(info)

        response = client.delete("/v1/streamer-info")

        assert response.status_code == 200
        assert response.json() == {
            "cleared": True,
            "deleted_records": 1,
            "deleted_file_entries": 1,
        }
        with database.session_factory() as session:
            assert session.query(StreamerInfo).count() == 0
            assert session.query(FileItem).count() == 0
        assert recording.read_bytes() == b"video"


def test_log_tail_reads_a_bounded_suffix(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "large.log"
    expected = [f"line-{index}" for index in range(60)]
    path.write_bytes(b"x" * (TAIL_MAX_BYTES * 2) + b"\n" + "\n".join(expected).encode())
    real_open = Path.open
    bytes_read = 0

    class TrackingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            nonlocal bytes_read
            data = self.stream.read(size)
            bytes_read += len(data)
            return data

    def tracking_open(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        return TrackingReader(stream) if self == path and args and args[0] == "rb" else stream

    monkeypatch.setattr(Path, "open", tracking_open)

    assert tail_lines(path) == expected[-50:]
    assert bytes_read <= TAIL_MAX_BYTES


def test_logging_handler_is_closed_after_lifespan(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/healthz").status_code == 200

    log_path = tmp_path / "logs" / "biliup.log"
    moved = log_path.with_suffix(".moved")
    log_path.rename(moved)
    assert moved.is_file()


def test_log_category_filter_keeps_related_traceback_lines() -> None:
    lines = [
        "2026-08-02 10:00:00 INFO biliup.recorder Starting FFmpeg recording for demo",
        "2026-08-02 10:01:00 ERROR biliup.jobs Background upload job abc failed",
        '  File "uploader.py", line 10, in upload_files',
        "RuntimeError: upload failed",
        "2026-08-02 10:02:00 INFO biliup.scheduler Streamer check completed",
    ]

    upload_lines, current = filter_log_lines(lines, "upload")

    assert upload_lines == lines[1:4]
    assert current == "other"


def test_clear_logs_truncates_active_file_and_removes_backups(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "biliup.log"
    log_path.write_text("before clear\n", encoding="utf-8")
    (log_dir / "biliup.log.1").write_text("backup one\n", encoding="utf-8")
    (log_dir / "biliup.log.2").write_text("backup two\n", encoding="utf-8")

    with make_client(tmp_path) as client:
        response = client.delete("/v1/logs")

        assert response.status_code == 200
        assert response.json() == {"cleared": True, "removed_backups": 2}
        assert log_path.read_text(encoding="utf-8") == ""
        assert not (log_dir / "biliup.log.1").exists()
        assert not (log_dir / "biliup.log.2").exists()

        record = logging.LogRecord("biliup.test", logging.INFO, __file__, 1, "after clear", (), None)
        client.app.state.context.log_handler.emit(record)
        assert "after clear" in log_path.read_text(encoding="utf-8")
