import asyncio
import threading
import time
from contextlib import suppress

import pytest

import biliup.common.util
from biliup.config import config
from biliup.engine import StreamStatus
from biliup.platforms import bilibili as bilibili_platform
from biliup.platforms.bilibili import Bililive
from biliup.platforms.douyin import DouyinUtils, select_quality
from biliup.platforms.kuaishou import Kuaishou
from biliup.platforms.nico import Nico
from biliup.platforms.twitch import Twitch


class FakeResponse:
    text = ""
    status_code = 200

    def __init__(self, *, text: str = "", payload=None, cookies=None):
        self.text = text
        self._payload = payload
        self.cookies = cookies or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def reset_bilibili_batch_state(monkeypatch):
    bilibili_platform.BILIBILI_CONFIGURED_ROOM_IDS.clear()
    bilibili_platform.BILIBILI_ROOM_STATUS_CACHE.clear()
    bilibili_platform.BILIBILI_ROOM_STATUS_EXPIRES_AT = 0.0
    bilibili_platform.BILIBILI_BATCH_FAILURE_REASON = None
    bilibili_platform.BILIBILI_BATCH_FAILURE_COUNT = 0
    bilibili_platform.BILIBILI_BATCH_RETRY_AT = 0.0
    monkeypatch.setattr(bilibili_platform, "BILIBILI_BATCH_WAIT_SECONDS", 0.0)


async def test_bilibili_probe_distinguishes_api_failure_from_offline(monkeypatch) -> None:
    responses = [
        FakeResponse(payload={"code": -352, "message": "-352", "ttl": 1}),
        FakeResponse(
            payload={"code": 0, "data": {"by_room_ids": {"123": {"live_status": 0}}}}
        ),
    ]
    calls = 0

    async def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return responses.pop(0)

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    monkeypatch.setattr(bilibili_platform.wbi, "key", "a" * 32)
    monkeypatch.setattr(bilibili_platform.wbi, "last_update", int(time.time()))
    with config.overlay({"user": {}}):
        checker = Bililive("demo", "https://live.bilibili.com/123")
        failed = await checker.aprobe_stream(is_check=True)
        cached_failure = await checker.aprobe_stream(is_check=True)
        bilibili_platform.BILIBILI_BATCH_RETRY_AT = 0.0
        offline = await checker.aprobe_stream(is_check=True)

    assert failed.status is StreamStatus.UNKNOWN
    assert "-352" in (failed.reason or "")
    assert cached_failure.status is StreamStatus.UNKNOWN
    assert offline.status is StreamStatus.OFFLINE
    assert calls == 2


async def test_bilibili_batch_failure_is_shared_by_concurrent_probes(monkeypatch) -> None:
    calls = 0

    async def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return FakeResponse(payload={"code": -352, "message": "risk control"})

    urls = [f"https://live.bilibili.com/{room_id}" for room_id in range(100, 120)]
    bilibili_platform.configure_bilibili_rooms(urls)
    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)

    with config.overlay({"user": {}}):
        results = await asyncio.gather(
            *(Bililive(str(index), url).aprobe_stream(is_check=True) for index, url in enumerate(urls))
        )

    assert all(result.status is StreamStatus.UNKNOWN for result in results)
    assert calls == 1


async def test_bilibili_batch_omission_is_cached(monkeypatch) -> None:
    calls = 0

    async def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse(payload={"code": 0, "data": {"by_room_ids": {}}})

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    with config.overlay({"user": {}}):
        checker = Bililive("demo", "https://live.bilibili.com/123")
        first = await checker.aprobe_stream(is_check=True)
        second = await checker.aprobe_stream(is_check=True)

    assert first.status is StreamStatus.UNKNOWN
    assert second.status is StreamStatus.UNKNOWN
    assert "omitted room 123" in (first.reason or "")
    assert calls == 1


@pytest.mark.parametrize("live_status", [None, "1", 3, True])
async def test_bilibili_batch_rejects_invalid_live_status(monkeypatch, live_status) -> None:
    async def fake_get(_url, **_kwargs):
        return FakeResponse(
            payload={
                "code": 0,
                "data": {"by_room_ids": {"123": {"live_status": live_status}}},
            }
        )

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    with config.overlay({"user": {}}):
        result = await Bililive("demo", "https://live.bilibili.com/123").aprobe_stream(
            is_check=True
        )

    assert result.status is StreamStatus.UNKNOWN
    assert "invalid live_status" in (result.reason or "")


async def test_bilibili_probe_treats_malformed_success_as_unknown(monkeypatch) -> None:
    async def fake_get(url, **_kwargs):
        if url.endswith("/room/v1/Room/room_init"):
            return FakeResponse(
                payload={"code": 0, "data": {"live_status": 1, "room_id": 123}}
            )
        return FakeResponse(payload={"code": 0, "data": {"room_info": {"live_status": 1}}})

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    monkeypatch.setattr(bilibili_platform.wbi, "key", "a" * 32)
    monkeypatch.setattr(bilibili_platform.wbi, "last_update", int(time.time()))
    with config.overlay(
        {
            "user": {},
            "bili_liveapi": "https://primary.example",
            "bili_fallback_api": "https://primary.example",
        }
    ):
        result = await Bililive("demo", "https://live.bilibili.com/123").aprobe_stream(is_check=True)

    assert result.status is StreamStatus.UNKNOWN
    assert "missing required room fields" in (result.reason or "")


async def test_bilibili_probe_uses_fallback_api_for_invalid_primary_response(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get(url, **_kwargs):
        calls.append(url)
        if url.startswith("https://primary.example"):
            return FakeResponse(payload={"code": 0, "data": {}})
        return FakeResponse(payload={"code": 0, "data": {"live_status": 0, "room_id": 123}})

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    monkeypatch.setattr(bilibili_platform.wbi, "key", "a" * 32)
    monkeypatch.setattr(bilibili_platform.wbi, "last_update", int(time.time()))
    with config.overlay(
        {
            "user": {},
            "bili_liveapi": "https://primary.example",
            "bili_fallback_api": "https://fallback.example",
        }
    ):
        result = await Bililive("demo", "https://live.bilibili.com/123").aprobe_stream(is_check=True)

    assert result.status is StreamStatus.OFFLINE
    assert calls == [
        "https://primary.example/room/v1/Room/room_init",
        "https://fallback.example/room/v1/Room/room_init",
    ]


async def test_bilibili_probe_uses_fallback_for_non_object_detail_response(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get(url, **_kwargs):
        calls.append(url)
        if url.endswith("/room/v1/Room/room_init"):
            return FakeResponse(payload={"code": 0, "data": {"live_status": 1, "room_id": 123}})
        if url.startswith("https://primary.example"):
            return FakeResponse(payload=[])
        return FakeResponse(
            payload={"code": 0, "data": {"room_info": {"live_status": 0}}}
        )

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    monkeypatch.setattr(bilibili_platform.wbi, "key", "a" * 32)
    monkeypatch.setattr(bilibili_platform.wbi, "last_update", int(time.time()))
    with config.overlay(
        {
            "user": {},
            "bili_liveapi": "https://primary.example",
            "bili_fallback_api": "https://fallback.example",
        }
    ):
        result = await Bililive("demo", "https://live.bilibili.com/123").aprobe_stream(
            is_check=True
        )

    assert result.status is StreamStatus.OFFLINE
    assert calls == [
        "https://primary.example/room/v1/Room/room_init",
        "https://primary.example/xlive/web-room/v1/index/getInfoByRoom",
        "https://fallback.example/xlive/web-room/v1/index/getInfoByRoom",
    ]


async def test_bilibili_offline_batch_probe_skips_wbi_api(monkeypatch) -> None:
    calls: list[str] = []
    request_headers: list[dict] = []

    async def fake_get(url, **kwargs):
        calls.append(url)
        request_headers.append(kwargs.get("headers", {}))
        return FakeResponse(
            payload={"code": 0, "data": {"by_room_ids": {"123": {"live_status": 0}}}}
        )

    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)
    monkeypatch.setattr(bilibili_platform.wbi, "last_update", 0)
    with config.overlay({"user": {"bili_cookie": "SESSDATA=secret"}}):
        result = await Bililive("demo", "https://live.bilibili.com/123").aprobe_stream(is_check=True)

    assert result.status is StreamStatus.OFFLINE
    assert calls == [
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomBaseInfo"
    ]
    assert all(key.lower() != "cookie" for key in request_headers[0])
    assert request_headers[0]["user-agent"] == bilibili_platform.BILIBILI_USER_AGENT


async def test_bilibili_batch_probe_reuses_all_configured_room_statuses(monkeypatch) -> None:
    calls: list[list[tuple[str, str]]] = []

    async def fake_get(_url, **kwargs):
        calls.append(kwargs["params"])
        return FakeResponse(
            payload={
                "code": 0,
                "data": {
                    "by_room_ids": {
                        "123": {"live_status": 0},
                        "456": {"live_status": 2},
                    }
                },
            }
        )

    bilibili_platform.configure_bilibili_rooms(
        ["https://live.bilibili.com/123", "https://live.bilibili.com/456"]
    )
    monkeypatch.setattr(bilibili_platform, "_bilibili_get", fake_get)

    with config.overlay({"user": {}}):
        first = await Bililive("first", "https://live.bilibili.com/123").aprobe_stream(is_check=True)
        second = await Bililive("second", "https://live.bilibili.com/456").aprobe_stream(is_check=True)

    assert first.status is StreamStatus.OFFLINE
    assert second.status is StreamStatus.OFFLINE
    assert len(calls) == 1
    assert ("room_ids", "123") in calls[0]
    assert ("room_ids", "456") in calls[0]


async def test_bilibili_get_uses_requests_transport(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(payload={"code": 0})

    monkeypatch.setattr(bilibili_platform.BILIBILI_REQUEST_SESSION, "get", fake_get)
    bilibili_platform.BILIBILI_REQUEST_SESSION.cookies.set("stale", "secret")

    response = await bilibili_platform._bilibili_get("https://api.live.bilibili.com/test")

    assert response.json() == {"code": 0}
    assert calls == [("https://api.live.bilibili.com/test", {"timeout": 15})]
    assert not bilibili_platform.BILIBILI_REQUEST_SESSION.cookies


async def test_bilibili_get_keeps_session_serialized_after_cancellation(monkeypatch) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    calls = 0
    active = 0
    peak_active = 0

    def fake_get(_url, **_kwargs):
        nonlocal calls, active, peak_active
        with state_lock:
            calls += 1
            call_number = calls
            active += 1
            peak_active = max(peak_active, active)
        if call_number == 1:
            first_started.set()
            release_first.wait(timeout=2)
        with state_lock:
            active -= 1
        return FakeResponse(payload={"code": 0})

    monkeypatch.setattr(bilibili_platform.BILIBILI_REQUEST_SESSION, "get", fake_get)

    first = asyncio.create_task(bilibili_platform._bilibili_get("https://example.test/first"))
    assert await asyncio.to_thread(first_started.wait, 1)
    first.cancel()
    with suppress(asyncio.CancelledError):
        await first

    second = asyncio.create_task(bilibili_platform._bilibili_get("https://example.test/second"))
    await asyncio.sleep(0.05)
    assert calls == 1
    release_first.set()
    response = await second

    assert response.json() == {"code": 0}
    assert calls == 2
    assert peak_active == 1


async def test_kuaishou_cookie_is_request_scoped(monkeypatch) -> None:
    original_headers = dict(biliup.common.util.client.headers)
    calls = []

    async def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "livedetail" in url:
            return FakeResponse(
                payload={
                    "data": {
                        "result": 1,
                        "liveStream": {
                            "caption": "live",
                            "playUrls": [{"adaptationSet": {"representation": [{"url": "stream"}]}}],
                        },
                    }
                }
            )
        return FakeResponse()

    real_sleep = asyncio.sleep

    async def yielding_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(biliup.common.util.client, "get", fake_get)
    monkeypatch.setattr("biliup.platforms.kuaishou.asyncio.sleep", yielding_sleep)
    with config.overlay({"kuaishou_cookie": "session=secret"}):
        checker = Kuaishou("demo", "https://live.kuaishou.com/u/account")
        assert await checker.acheck_stream()

    assert dict(biliup.common.util.client.headers) == original_headers
    assert calls
    assert all(call[1]["headers"]["Cookie"] == "session=secret" for call in calls)


def test_douyin_quality_fallback_handles_either_side() -> None:
    quality_order = ["origin", "uhd", "hd", "sd", "ld", "md"]
    assert select_quality("origin", quality_order, {"hd": {}}) == "hd"
    assert select_quality("md", quality_order, {"sd": {}}) == "sd"
    assert select_quality("hd", quality_order, {"uhd": {}, "sd": {}}) == "sd"


async def test_douyin_ttwid_fetch_yields_to_event_loop(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_get(_url, **_kwargs):
        started.set()
        await release.wait()
        return FakeResponse(cookies={"ttwid": "token"})

    DouyinUtils._douyin_ttwid = None
    monkeypatch.setattr("biliup.platforms.douyin.client.get", fake_get)
    task = asyncio.create_task(DouyinUtils.get_ttwid())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()
    release.set()
    assert await task == "token"
    DouyinUtils._douyin_ttwid = None


async def test_nico_uses_async_subprocess_and_sleep(monkeypatch) -> None:
    class FakeProcess:
        returncode = None

        def terminate(self):
            return None

    process = FakeProcess()
    commands = []
    sleeps = 0

    async def fake_create(*command):
        commands.append(command)
        return process

    real_sleep = asyncio.sleep

    async def yielding_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        await real_sleep(0)

    async def fake_get(_url, **_kwargs):
        return FakeResponse(text='"name":"title","description":"description"')

    monkeypatch.setattr("biliup.platforms.nico.asyncio.create_subprocess_exec", fake_create)
    monkeypatch.setattr("biliup.platforms.nico.asyncio.sleep", yielding_sleep)
    monkeypatch.setattr(biliup.common.util.client, "get", fake_get)
    with config.overlay({"user": {}}):
        checker = Nico("demo", "https://live.nicovideo.jp/watch/lv1")
        assert await checker.acheck_stream()

    assert commands[0][0] == "streamlink"
    assert sleeps == 5


async def test_twitch_uses_async_subprocess_and_sleep(monkeypatch) -> None:
    class FakeProcess:
        returncode = None

        def terminate(self):
            return None

    commands = []
    sleeps = 0
    real_sleep = asyncio.sleep

    async def fake_post_gql(_operations):
        return {
            "data": {
                "user": {
                    "stream": {
                        "type": "live",
                        "title": "title",
                        "previewImageURL": "cover",
                        "playbackAccessToken": {"signature": "signature", "value": "token"},
                    }
                }
            }
        }

    async def fake_create(*command):
        commands.append(command)
        return FakeProcess()

    async def yielding_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        await real_sleep(0)

    monkeypatch.setattr("biliup.platforms.twitch.TwitchUtils.post_gql", fake_post_gql)
    monkeypatch.setattr("biliup.platforms.twitch.asyncio.create_subprocess_exec", fake_create)
    monkeypatch.setattr("biliup.platforms.twitch.asyncio.sleep", yielding_sleep)
    with config.overlay({"downloader": "ffmpeg", "user": {}}):
        checker = Twitch("demo", "https://twitch.tv/channel")
        assert await checker.acheck_stream()

    assert commands[0][0] == "streamlink"
    assert sleeps == 5
