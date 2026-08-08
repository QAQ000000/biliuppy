import json
from pathlib import Path

import pytest

from biliup.engine.upload import UploadBase
from biliup.integrations.uploaders import bili_browser


class FakeLocator:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.clicks = 0
        self.filled = ""

    def count(self) -> int:
        return int(self.visible)

    def nth(self, _index: int) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self.visible

    def click(self) -> None:
        self.clicks += 1

    def fill(self, value: str) -> None:
        self.filled = value


class FakeCopyrightPage:
    def __init__(self, text: dict[str, FakeLocator], statement: FakeLocator) -> None:
        self.text = text
        self.statement = statement

    def get_by_text(self, value: str, *, exact: bool) -> FakeLocator:
        return self.text.get(value, FakeLocator(visible=False))

    def get_by_placeholder(self, _value) -> FakeLocator:
        return self.statement

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


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
    assert bili_browser.upload_finished("已经上传 100%")
    assert bili_browser.upload_finished("视频上传完成")


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

    assert option.clicks == 1
    assert source.filled == "https://live.bilibili.com/1"


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
