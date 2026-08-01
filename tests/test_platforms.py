import asyncio

import biliup.common.util
from biliup.config import config
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
