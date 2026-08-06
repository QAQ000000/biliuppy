import json
from io import BytesIO
from pathlib import Path
from unittest.mock import ANY

import aiohttp
import pytest
import requests

from biliup.engine.decorators import Plugin
from biliup.integrations.upload_errors import is_transient_upload_error
from biliup.integrations.uploaders.bili_web import (
    BiliBili,
    Data,
    UploadProgress,
    build_web_payload,
    resolve_copyright_source,
)


def test_resolve_copyright_source_ignores_blank_values() -> None:
    live_url = "https://live.bilibili.com/123"

    assert resolve_copyright_source(None, live_url) == live_url
    assert resolve_copyright_source("   ", live_url) == live_url
    assert resolve_copyright_source(" https://example.com/source ", live_url) == "https://example.com/source"


def test_upload_plugin_wrapper_does_not_print_arguments(capsys) -> None:
    platform = "test-no-sensitive-output"

    @Plugin.upload(platform=platform)
    class ExampleUploader:
        def __init__(self, credential):
            self.credential = credential

    try:
        uploader = ExampleUploader(credential="sensitive-value")
        assert uploader.credential == "sensitive-value"
        assert capsys.readouterr().out == ""
    finally:
        Plugin.upload_plugins.pop(platform, None)


def test_upload_progress_is_structured_and_throttled(caplog, monkeypatch) -> None:
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.perf_counter", lambda: 10.0)
    progress = UploadProgress("upos", total_bytes=100, started_at=0.0)

    with caplog.at_level("INFO", logger="biliup"):
        progress.add(4)
        progress.add(1)
        progress.add(4)
        progress.add(1)

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "protocol=upos progress=5.0%" in messages[0]
    assert "protocol=upos progress=10.0%" in messages[1]


def test_submit_web_uses_v3_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"aid": 123}}

    class FakeSession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    bili = BiliBili(Data(title="title", tag="tag"))
    bili._BiliBili__session = FakeSession()
    bili._BiliBili__bili_jct = "csrf-token"
    monkeypatch.setattr(
        bili,
        "_build_web_params",
        lambda: {
            "web_location": "333.1024",
            "t": "1234567000",
            "csrf": "csrf-token",
            "w_rid": "signature",
            "wts": "1234567",
        },
    )

    result = bili.submit_web()

    assert result["code"] == 0
    assert calls == [
        (
            "https://member.bilibili.com/x/vu/web/add/v3",
            {
                "params": {
                    "web_location": "333.1024",
                    "t": "1234567000",
                    "csrf": "csrf-token",
                    "w_rid": "signature",
                    "wts": "1234567",
                },
                "timeout": 60,
                "json": ANY,
            },
        )
    ]
    assert calls[0][1]["json"]["title"] == "title"
    assert "extra_fields" not in calls[0][1]["json"]


def test_build_web_payload_uses_current_schema() -> None:
    video = Data(
        title="title",
        tid=230,
        tag=["tag-a", "tag-b"],
        desc="description",
        videos=[{"filename": "storage-name", "title": "part", "desc": ""}],
        hires=1,
        up_selection_reply=1,
        is_only_self=1,
    )

    payload = build_web_payload(video, timestamp=1000)

    assert payload["cover43"] == payload["cover"]
    assert payload["creation_statement"] == {"id": -1}
    assert payload["lossless_music"] == 1
    assert payload["up_selection_reply"] is True
    assert payload["up_close_reply"] is False
    assert payload["is_only_self"] == 1
    assert payload["member_first"]["exp_time"] == 260200
    assert payload["tag"] == "tag-a,tag-b"
    assert "hires" not in payload
    assert "desc_format_id" not in payload
    assert "desc_v2" not in payload
    assert "dtime" not in payload
    assert "source" not in payload


def test_build_web_payload_preserves_supported_optional_fields() -> None:
    video = Data(
        source="https://example.com/source",
        desc_v2=[{"raw_text": "credit", "biz_id": "1", "type": 2}],
        dtime=2000000000,
        extra_fields='{"human_type2": 1012, "watermark": {"state": 0}}',
    )

    payload = build_web_payload(video, timestamp=1000)

    assert payload["source"] == "https://example.com/source"
    assert payload["desc_v2"][0]["type"] == 2
    assert payload["dtime"] == 2000000000
    assert payload["human_type2"] == 1012
    assert payload["watermark"] == {"state": 0}


def test_build_web_params_adds_wbi_signature(monkeypatch) -> None:
    class FakeResponse:
        def json(self):
            return {
                "code": 0,
                "data": {
                    "wbi_img": {
                        "img_url": "https://i0.hdslb.com/bfs/wbi/" + "a" * 32 + ".png",
                        "sub_url": "https://i0.hdslb.com/bfs/wbi/" + "b" * 32 + ".png",
                    }
                },
            }

    class FakeSession:
        def get(self, url, **kwargs):
            assert url == "https://api.bilibili.com/x/web-interface/nav"
            assert kwargs == {"timeout": 10}
            return FakeResponse()

    bili = BiliBili(Data())
    bili._BiliBili__session = FakeSession()
    bili._BiliBili__bili_jct = "csrf-token"
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.time", lambda: 1234.567)

    params = bili._build_web_params()

    assert params["web_location"] == "333.1024"
    assert params["t"] == "1234567"
    assert params["csrf"] == "csrf-token"
    assert params["wts"] == "1234"
    assert len(params["w_rid"]) == 32


def test_read_only_login_does_not_rewrite_cookie(tmp_path: Path, monkeypatch) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(
        json.dumps(
            {
                "cookie_info": {"cookies": [{"name": "DedeUserID", "value": "123"}]},
                "token_info": {"access_token": "access", "refresh_token": "refresh"},
            }
        ),
        encoding="utf-8",
    )
    original = cookie_path.read_bytes()
    bili = BiliBili(Data())
    monkeypatch.setattr(bili, "login_by_cookies", lambda _cookies: None)
    monkeypatch.setattr(bili, "store", lambda: pytest.fail("read-only login attempted to store cookies"))

    bili.login(str(cookie_path), str(cookie_path), persist=False)

    assert cookie_path.read_bytes() == original


def test_cookie_store_uses_atomic_replacement(tmp_path: Path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text("old", encoding="utf-8")
    bili = BiliBili(Data())
    bili.persistence_path = str(cookie_path)
    bili.cookies = {"cookie_info": {"cookies": [{"name": "DedeUserID", "value": "123"}]}}
    bili.access_token = "access"
    bili.refresh_token = "refresh"

    bili.store()

    payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    assert payload["access_token"] == "access"
    assert payload["refresh_token"] == "refresh"
    assert list(tmp_path.glob(".cookies.json.*.tmp")) == []


def test_auto_line_probe_retries_line_query_and_honors_exclusions(monkeypatch) -> None:
    line_response = {
        "probe": {"get": True},
        "lines": [
            {
                "os": "upos",
                "query": "upcdn=bda2&probe_version=20221109",
                "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK",
            },
            {
                "os": "upos",
                "query": "upcdn=bldsa&probe_version=20221109",
                "probe_url": "//upos-cs-upcdnbldsa.bilivideo.com/OK",
            },
        ],
    }
    get_attempts = 0
    probed_urls: list[str] = []
    sleeps: list[float] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return line_response

    class FakeSession:
        def get(self, _url, **_kwargs):
            nonlocal get_attempts
            get_attempts += 1
            if get_attempts < 3:
                raise requests.ConnectionError("temporary failure")
            return FakeResponse()

        def request(self, _method, url, **_kwargs):
            probed_urls.append(url)
            return FakeResponse()

    ticks = iter([10.0, 10.2])
    excluded = ["upos:bda2"]
    bili = BiliBili(Data(), excluded_upload_lines=excluded)
    bili._BiliBili__session = FakeSession()
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.random.random", lambda: 0.0)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.sleep", sleeps.append)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.perf_counter", lambda: next(ticks))

    selected = bili.probe()

    assert get_attempts == 3
    assert sleeps == [1.0, 2.0]
    assert probed_urls == ["https://upos-cs-upcdnbldsa.bilivideo.com/OK"]
    assert selected["query"].startswith("upcdn=bldsa")


def test_auto_line_probe_skips_unhealthy_candidate(monkeypatch) -> None:
    lines = [
        {
            "os": "upos",
            "query": "upcdn=bda2&probe_version=20221109",
            "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK",
        },
        {
            "os": "upos",
            "query": "upcdn=tx&probe_version=20221109",
            "probe_url": "//upos-cs-upcdntx.bilivideo.com/OK",
        },
    ]

    class FakeResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, _url, **_kwargs):
            return FakeResponse(payload={"probe": {"get": True}, "lines": lines})

        def request(self, _method, url, **_kwargs):
            return FakeResponse(status_code=500 if "bda2" in url else 200)

    ticks = iter([10.0, 10.1, 20.0, 20.3])
    bili = BiliBili(Data())
    bili._BiliBili__session = FakeSession()
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.perf_counter", lambda: next(ticks))

    selected = bili.probe()

    assert selected["query"].startswith("upcdn=tx")


@pytest.mark.parametrize("status_code", [429, 500])
def test_auto_line_probe_preserves_transient_http_failure(status_code, monkeypatch) -> None:
    line = {
        "os": "upos",
        "query": "upcdn=bda2&probe_version=20221109",
        "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK",
    }

    class FakeResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, _url, **_kwargs):
            return FakeResponse(payload={"probe": {"get": True}, "lines": [line]})

        def request(self, _method, _url, **_kwargs):
            return FakeResponse(status_code=status_code)

    bili = BiliBili(Data())
    bili._BiliBili__session = FakeSession()

    with pytest.raises(RuntimeError, match="No healthy Bilibili upload line") as exc_info:
        bili.probe()

    assert is_transient_upload_error(exc_info.value) is True


def test_auto_upload_failure_excludes_selected_line(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "sample.flv"
    video.write_bytes(b"video")
    selected_line = {
        "os": "upos",
        "query": "upcdn=bda2&probe_version=20221109",
        "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK",
        "cost": 0.1,
    }
    excluded: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "endpoint": "//upos-cs-upcdnbda2.bilivideo.com",
                "upos_uri": "upos://bucket/video.flv",
                "biz_id": 1,
                "chunk_size": 10,
                "auth": "token",
            }

    class FakeSession:
        def get(self, _url, **_kwargs):
            return FakeResponse()

    async def failing_upload(_file, _total_size, _ret, tasks):
        assert tasks == 3
        raise aiohttp.ClientConnectionError("line unavailable")

    bili = BiliBili(Data(), excluded_upload_lines=excluded)
    bili._BiliBili__session = FakeSession()
    monkeypatch.setattr(bili, "probe", lambda: selected_line.copy())
    monkeypatch.setattr(bili, "upos", failing_upload)

    with pytest.raises(aiohttp.ClientConnectionError, match="line unavailable"):
        bili.upload_file(str(video), lines="AUTO", tasks=3)

    assert excluded == ["upos:bda2"]


def test_auto_preupload_failure_excludes_selected_line(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "sample.flv"
    video.write_bytes(b"video")
    selected_line = {
        "os": "upos",
        "query": "upcdn=bda2&probe_version=20221109",
        "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK",
        "cost": 0.1,
    }
    excluded: list[str] = []

    class FakeSession:
        def get(self, _url, **_kwargs):
            raise requests.ConnectionError("preupload unavailable")

    bili = BiliBili(Data(), excluded_upload_lines=excluded)
    bili._BiliBili__session = FakeSession()
    monkeypatch.setattr(bili, "probe", lambda: selected_line.copy())
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.time.sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        bili.upload_file(str(video), lines="AUTO", tasks=3)

    assert excluded == ["upos:bda2"]


def test_manual_upload_line_is_not_excluded_after_failure() -> None:
    excluded: list[str] = []
    bili = BiliBili(Data(), excluded_upload_lines=excluded)
    bili._auto_os = {
        "os": "upos",
        "query": "upcdn=bda2&probe_version=20221109",
    }

    bili._exclude_failed_auto_line(aiohttp.ClientConnectionError("unavailable"), auto_lines=False)

    assert excluded == []


async def test_upload_aborts_after_final_chunk_failure(monkeypatch) -> None:
    attempts = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    def fake_client_session():
        return FakeSession()

    async def failing_upload(_session, _chunk, _params):
        nonlocal attempts
        attempts += 1
        raise aiohttp.ClientError("unavailable")

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.aiohttp.ClientSession", fake_client_session)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.asyncio.sleep", immediate_sleep)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.random.random", lambda: 0.0)

    with pytest.raises(RuntimeError, match="Chunk 0 upload failed after 4 attempts"):
        await BiliBili._upload({}, BytesIO(b"video"), 5, failing_upload, tasks=1)

    assert attempts == 4


async def test_upload_accepts_success_on_final_retry(monkeypatch) -> None:
    attempts = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def eventually_succeeds(_session, _chunk, _params):
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise aiohttp.ClientError("unavailable")

    async def immediate_sleep(_delay):
        return None

    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.aiohttp.ClientSession", FakeSession)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.asyncio.sleep", immediate_sleep)
    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.random.random", lambda: 0.0)

    await BiliBili._upload({}, BytesIO(b"video"), 5, eventually_succeeds, tasks=1)

    assert attempts == 4
