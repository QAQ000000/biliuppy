import json
from pathlib import Path

import pytest
import requests

from biliup.engine.upload import UploadBase
from biliup.integrations.uploaders import bili_browser


class FakeLocator:
    def __init__(
        self,
        *,
        visible: bool = True,
        children: dict[str, "FakeLocator"] | None = None,
        ancestor: "FakeLocator | None" = None,
    ) -> None:
        self.visible = visible
        self.clicks = 0
        self.filled = ""
        self.children = children or {}
        self.ancestor = ancestor

    def count(self) -> int:
        return int(self.visible)

    def nth(self, _index: int) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self.visible

    def filter(self, **_kwargs) -> "FakeLocator":
        return self

    def locator(self, _selector: str) -> "FakeLocator":
        return self.ancestor or FakeLocator(visible=False)

    def get_by_text(self, value: str, *, exact: bool) -> "FakeLocator":
        return self.children.get(value, FakeLocator(visible=False))

    def click(self) -> None:
        self.clicks += 1

    def fill(self, value: str) -> None:
        self.filled = value


class FakeCopyrightPage:
    def __init__(
        self,
        text: dict[str, FakeLocator],
        statement: FakeLocator,
        text_after_open: dict[str, FakeLocator] | None = None,
    ) -> None:
        self.text = text
        self.statement = statement
        self.text_after_open = text_after_open or {}

    def get_by_text(self, value: str, *, exact: bool) -> FakeLocator:
        if self.statement.clicks:
            return self.text_after_open.get(value, self.text.get(value, FakeLocator(visible=False)))
        return self.text.get(value, FakeLocator(visible=False))

    def get_by_placeholder(self, _value) -> FakeLocator:
        return self.statement

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class MissingOptionPage:
    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(visible=False)


class FakeOverlayPage:
    def __init__(self, text: dict[str, FakeLocator]) -> None:
        self.text = text
        self.waits: list[int] = []

    def get_by_text(self, value: str, *, exact: bool) -> FakeLocator:
        return self.text.get(value, FakeLocator(visible=False))

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


class FakeRequest:
    method = "POST"

    def __init__(self, name: str) -> None:
        self.post_data_json = {"name": name}


class FakeResponse:
    url = "https://member.bilibili.com/upload/multipart/new"

    def __init__(self, name: str, *, filename: str = "remote-name", biz_id: int = 123) -> None:
        self.request = FakeRequest(name)
        self.filename = filename
        self.biz_id = biz_id

    def json(self) -> dict:
        return {
            "code": 0,
            "data": {"filename": self.filename, "biz_id": self.biz_id},
        }


def write_cookie_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cookie_info": {
                    "cookies": [
                        {"name": "SESSDATA", "value": "session"},
                        {"name": "bili_jct", "value": "csrf"},
                        {"name": "DedeUserID", "value": "123"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_browser_cookie_round_trip_preserves_cookie_container(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    write_cookie_file(path)
    payload, cookies = bili_browser.load_browser_cookies(path)

    assert payload["cookie_info"]["cookies"][0]["name"] == "SESSDATA"
    assert cookies[0]["domain"] == ".bilibili.com"
    cookies.append({"name": "buvid3", "value": "generated", "domain": ".bilibili.com", "path": "/"})
    bili_browser.save_browser_cookies(path, payload, cookies)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [item["name"] for item in saved["cookie_info"]["cookies"]][-1] == "buvid3"


def test_category_names_resolves_nested_tid() -> None:
    typelist = [{"name": "生活", "children": [{"id": 122, "name": "日常"}]}]
    assert bili_browser.category_names(typelist, 122) == ("生活", "日常")
    with pytest.raises(ValueError, match="unavailable"):
        bili_browser.category_names(typelist, 999)


def test_video_input_targets_the_active_upload_wrapper() -> None:
    assert ".bcc-upload-wrapper" in bili_browser.VIDEO_INPUT
    assert 'name="buploader"' not in bili_browser.VIDEO_INPUT


def test_upload_finished_requires_completed_transfer() -> None:
    assert not bili_browser.upload_finished("上传中 0% 创作声明 立即投稿")
    assert not bili_browser.upload_finished("已经上传 99.9%")
    assert not bili_browser.upload_finished("P1 上传完成 P2 上传中 25% P3 等待上传")
    assert not bili_browser.upload_finished("P1 100% P2 等待上传")
    assert bili_browser.upload_finished("已经上传 100%")
    assert bili_browser.upload_finished("视频上传完成")


def test_batch_operations_overlay_is_closed_with_its_own_cancel_button() -> None:
    cancel = FakeLocator()
    dialog = FakeLocator(children={"取消": cancel})
    title = FakeLocator(ancestor=dialog)
    unrelated_cancel = FakeLocator()
    page = FakeOverlayPage({"批量操作": title, "取消": unrelated_cancel})
    uploader = bili_browser.BiliBrowser(principal="demo", data={})

    uploader._dismiss_known_overlays(page)

    assert cancel.clicks == 1
    assert unrelated_cancel.clicks == 0
    assert page.waits == [300]


def test_unrelated_cancel_button_is_not_clicked_without_batch_overlay() -> None:
    unrelated_cancel = FakeLocator()
    page = FakeOverlayPage({"取消": unrelated_cancel})
    uploader = bili_browser.BiliBrowser(principal="demo", data={})

    uploader._dismiss_known_overlays(page)

    assert unrelated_cancel.clicks == 0
    assert page.waits == []


def test_browser_upload_response_builds_api_video_part() -> None:
    result = bili_browser.browser_uploaded_part(
        FakeResponse("demo_001.flv", filename="n260808s123", biz_id=456)
    )

    assert result == (
        "demo_001.flv",
        {
            "filename": "n260808s123",
            "cid": 456,
            "title": "demo_001",
            "desc": "",
        },
    )


def test_browser_parts_follow_selected_file_order_and_use_latest_attempt(tmp_path: Path) -> None:
    first = tmp_path / "first.flv"
    second = tmp_path / "second.flv"
    captured = {
        "first.flv": [
            {"filename": "orphaned", "cid": 1, "title": "first", "desc": ""},
            {"filename": "first-current", "cid": 2, "title": "first", "desc": ""},
        ],
        "second.flv": [
            {"filename": "second-current", "cid": 3, "title": "second", "desc": ""},
        ],
    }

    parts = bili_browser.ordered_browser_parts([second, first], captured)

    assert parts == [
        {"filename": "second-current", "cid": 3, "title": "second", "desc": ""},
        {"filename": "first-current", "cid": 2, "title": "first", "desc": ""},
    ]


def test_browser_parts_require_every_selected_file(tmp_path: Path) -> None:
    files = [tmp_path / "first.flv", tmp_path / "missing.flv"]
    captured = {
        "first.flv": [{"filename": "first", "cid": 1, "title": "first", "desc": ""}],
    }

    assert bili_browser.ordered_browser_parts(files, captured) is None


def test_api_submission_uses_browser_cookies_and_template_fields(tmp_path: Path, monkeypatch) -> None:
    cookie_path = tmp_path / "cookies.json"
    write_cookie_file(cookie_path)
    calls: dict = {}

    class FakeContext:
        def cookies(self):
            return [{"name": "bili_jct", "value": "fresh-csrf"}]

    class FakeBiliBili:
        def __init__(self, video, **_kwargs):
            calls["video"] = video

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login_by_cookies(self, payload):
            calls["cookies"] = payload

        def submit(self, submit_api):
            calls["submit_api"] = submit_api
            return {"code": 0, "data": {"aid": 123, "bvid": "BV123"}}

    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.BiliBili", FakeBiliBili)
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={"format_title": "rendered title", "url": "https://live.bilibili.com/1"},
        user_cookie=str(cookie_path),
        copyright=1,
        tid=95,
        tags=["tag-a", "tag-b"],
        description="description",
        dynamic="dynamic",
        no_reprint=1,
    )
    result = uploader._submit_via_api(
        FakeContext(),
        {"cookie_info": {"cookies": [{"name": "SESSDATA", "value": "old"}]}},
        [{"filename": "remote", "cid": 456, "title": "part", "desc": ""}],
    )

    assert result["data"]["bvid"] == "BV123"
    assert calls["cookies"]["cookie_info"]["cookies"][0]["value"] == "fresh-csrf"
    assert calls["submit_api"] == "web"
    video = calls["video"]
    assert video.title == "rendered title"
    assert video.videos[0]["cid"] == 456
    assert video.tid == 95
    assert video.tag == "tag-a,tag-b"
    assert video.no_reprint == 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(requests.Timeout("unknown"), id="timeout"),
        pytest.param(requests.ConnectionError("unknown"), id="connection"),
    ],
)
def test_api_submission_does_not_hide_unknown_network_outcome(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
) -> None:
    cookie_path = tmp_path / "cookies.json"
    write_cookie_file(cookie_path)

    class FakeContext:
        def cookies(self):
            return [{"name": "bili_jct", "value": "fresh-csrf"}]

    class FakeBiliBili:
        def __init__(self, _video, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login_by_cookies(self, _payload):
            pass

        def submit(self, _submit_api):
            raise error

    monkeypatch.setattr("biliup.integrations.uploaders.bili_web.BiliBili", FakeBiliBili)
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={"format_title": "title"},
        user_cookie=str(cookie_path),
        copyright=1,
    )

    with pytest.raises(type(error), match="unknown"):
        uploader._submit_via_api(
            FakeContext(),
            {"cookie_info": {"cookies": []}},
            [{"filename": "remote", "cid": 456, "title": "part", "desc": ""}],
        )


def test_human_category_mapping_covers_configured_templates() -> None:
    assert bili_browser.HUMAN_TYPE_BY_TID == {
        21: "生活经验",
        95: "科技数码",
        138: "娱乐",
        219: "动物",
        231: "科技数码",
        250: "旅游出行",
    }


def test_creation_statement_maps_self_made_content() -> None:
    statement = FakeLocator()
    option = FakeLocator()
    page = FakeCopyrightPage({"内容无需标注": option}, statement)
    uploader = bili_browser.BiliBrowser(principal="demo", data={}, copyright=1)

    uploader._select_copyright(page)

    assert statement.clicks == 0
    assert option.clicks == 0


def test_creation_statement_selects_value_when_not_already_selected() -> None:
    statement = FakeLocator()
    option = FakeLocator()
    page = FakeCopyrightPage({}, statement, {"内容无需标注": option})
    uploader = bili_browser.BiliBrowser(principal="demo", data={}, copyright=1)

    uploader._select_copyright(page)

    assert statement.clicks == 1
    assert option.clicks == 1


def test_creation_statement_maps_reposted_content(monkeypatch) -> None:
    statement = FakeLocator()
    option = FakeLocator()
    source = FakeLocator()
    page = FakeCopyrightPage({"内容为转载": option}, statement)
    monkeypatch.setattr(bili_browser, "_field_near_label", lambda *_args: source)
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={},
        copyright=2,
        copyright_source="https://live.bilibili.com/1",
    )

    uploader._select_copyright(page)

    assert option.clicks == 0
    assert source.filled == "https://live.bilibili.com/1"


def test_missing_optional_audio_flags_do_not_abort_submission(monkeypatch) -> None:
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={},
        dolby=1,
        hires=1,
    )
    monkeypatch.setattr(uploader, "_ensure_more_settings", lambda _page: None)

    uploader._apply_submission_flags(MissingOptionPage())


def test_missing_visibility_flag_remains_an_error(monkeypatch) -> None:
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={},
        is_only_self=1,
    )
    monkeypatch.setattr(uploader, "_ensure_more_settings", lambda _page: None)

    with pytest.raises(RuntimeError, match="仅自己可见"):
        uploader._apply_submission_flags(MissingOptionPage())


def test_browser_requires_xvfb_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(bili_browser.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(RuntimeError, match="xvfb-run"):
        bili_browser.ensure_headed_environment()


def test_browser_rejects_unsupported_submission_options(tmp_path: Path) -> None:
    cookie_path = tmp_path / "cookies.json"
    write_cookie_file(cookie_path)
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    uploader = bili_browser.BiliBrowser(
        principal="demo",
        data={"format_title": "demo"},
        user_cookie=str(cookie_path),
        extra_fields='{"unexpected": true}',
    )
    with pytest.raises(ValueError, match="extra_fields"):
        uploader.upload([UploadBase.FileInfo(str(video), None)])
