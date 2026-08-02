from io import BytesIO
from unittest.mock import ANY

import aiohttp
import pytest

from biliup.engine.decorators import Plugin
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
