import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from biliup.api import create_app
from biliup.core import AppSettings


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
