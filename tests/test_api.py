import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from biliup.api import create_app
from biliup.api.routers.logs import TAIL_MAX_BYTES, tail_lines
from biliup.core import AppSettings
from biliup.database import Database
from biliup.database.models import Configuration, FileItem, StreamerInfo


def make_client(tmp_path: Path, *, auth: bool = False) -> TestClient:
    settings = AppSettings(
        home=tmp_path,
        auth_enabled=auth,
        scheduler_enabled=False,
        session_secret="test-session-secret",
    )
    return TestClient(create_app(settings))


def test_frontend_api_contract(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/v1/configuration").json()["downloader"] == "ffmpeg"

        created = client.post(
            "/v1/streamers",
            json={"url": "https://example.invalid/live", "remark": "demo", "format": "mp4"},
        )
        assert created.status_code == 200
        streamer = created.json()
        assert streamer["remark"] == "demo"
        assert streamer["status"] == "Pending"
        assert "filename_prefix" in streamer
        assert "upload_streamers_id" in streamer
        assert "upload_status" in streamer
        assert "filename" not in streamer
        assert "upload_id" not in streamer

        streamer["remark"] = "renamed"
        updated = client.put("/v1/streamers", json=streamer)
        assert updated.status_code == 200
        assert updated.json()["remark"] == "renamed"

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
        assert client.post(
            "/v1/users/register", json={"username": "biliup", "password": "secret"}
        ).status_code == 200
        assert client.get("/v1/configuration").status_code == 200
        assert client.get("/v1/logout").status_code == 204
        assert client.get("/v1/configuration").status_code == 401
        assert client.post(
            "/v1/users/login", json={"username": "biliup", "password": "secret"}
        ).status_code == 200
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
