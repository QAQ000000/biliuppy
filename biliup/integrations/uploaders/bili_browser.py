from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from biliup.engine import Plugin
from biliup.engine.upload import UploadBase
from biliup.integrations.upload_errors import (
    TransientUploadError,
    UploadCancelledError,
    UploadOutcomeUnknownError,
    UploadRejectedError,
)
from biliup.integrations.upload_state import UploadStateStore, account_key_for, account_lock_for

logger = logging.getLogger("biliup")
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame?page_from=creative_home_top_upload"
SUBMIT_PATH = "/x/vu/web/add/v3"
VIDEO_INPUT = '.bcc-upload-wrapper > input[type="file"][accept*=".mp4"]'
HUMAN_TYPE_BY_TID = {
    21: "生活经验",
    95: "科技数码",
    138: "娱乐",
    219: "动物",
    231: "科技数码",
    250: "旅游出行",
}


def load_browser_cookies(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Bilibili cookie file must contain a JSON object")
    values = payload.get("cookie_info", {}).get("cookies")
    if not isinstance(values, list):
        values = payload.get("cookies")
    if not isinstance(values, list):
        values = [
            {"name": name, "value": value}
            for name, value in payload.items()
            if isinstance(value, str) and name not in {"access_token", "refresh_token"}
        ]

    cookies: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict) or not value.get("name") or value.get("value") is None:
            continue
        cookie: dict[str, Any] = {
            "name": str(value["name"]),
            "value": str(value["value"]),
            "domain": str(value.get("domain") or ".bilibili.com"),
            "path": str(value.get("path") or "/"),
        }
        for key in ("httpOnly", "secure"):
            if key in value:
                cookie[key] = bool(value[key])
        expires = value.get("expires", value.get("expiry"))
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = int(expires)
        same_site = str(value.get("sameSite") or "").capitalize()
        if same_site in {"Lax", "Strict", "None"}:
            cookie["sameSite"] = same_site
        cookies.append(cookie)
    if not any(cookie["name"] == "SESSDATA" for cookie in cookies):
        raise ValueError("Bilibili cookie file does not contain SESSDATA")
    if not any(cookie["name"] == "DedeUserID" and cookie["value"] for cookie in cookies):
        raise ValueError("Browser upload requires DedeUserID in the Bilibili cookie file")
    return payload, cookies


def save_browser_cookies(path: str | Path, payload: dict[str, Any], cookies: list[dict[str, Any]]) -> None:
    target = Path(path)
    stored = []
    for cookie in cookies:
        value = {key: cookie[key] for key in ("name", "value", "domain", "path") if key in cookie}
        for key in ("expires", "httpOnly", "secure", "sameSite"):
            if key in cookie:
                value[key] = cookie[key]
        stored.append(value)
    cookie_info = payload.setdefault("cookie_info", {})
    if not isinstance(cookie_info, dict):
        cookie_info = payload["cookie_info"] = {}
    cookie_info["cookies"] = stored
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(target)


def category_names(typelist: list[dict[str, Any]], tid: int) -> tuple[str, str]:
    for parent in typelist:
        for child in parent.get("children") or []:
            if int(child.get("id") or 0) == int(tid):
                return str(parent.get("name") or ""), str(child.get("name") or "")
    raise ValueError(f"Bilibili category id is unavailable: {tid}")


def upload_finished(body: str) -> bool:
    return "上传完成" in body or bool(re.search(r"(?<!\d)100(?:\.0+)?%", body))


def ensure_headed_environment() -> None:
    if sys.platform.startswith("linux") and not os.getenv("DISPLAY"):
        raise RuntimeError(
            "Browser upload requires a display. Start biliup with "
            "xvfb-run -a -s '-screen 0 1920x1080x24' or configure DISPLAY."
        )


def _visible(locator: Any) -> Any | None:
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _field_near_label(page: Any, labels: tuple[str, ...], selector: str) -> Any | None:
    for label in labels:
        matches = page.get_by_text(label, exact=True)
        for index in range(matches.count()):
            field = matches.nth(index).locator(f"xpath=ancestor::*[.//{selector}][1]//{selector}")
            candidate = _visible(field)
            if candidate is not None:
                return candidate
    return None


@Plugin.upload(platform="bili_browser")
class BiliBrowser(UploadBase):
    def __init__(
        self,
        principal: str,
        data: dict[str, Any],
        user: dict[str, Any] | None = None,
        user_cookie: str = "cookies.json",
        copyright: int = 2,
        copyright_source: str | None = None,
        tid: int = 122,
        tags: list[str] | None = None,
        cover_path: str | None = None,
        description: str = "",
        credits: list[dict[str, Any]] | None = None,
        dynamic: str = "",
        dtime: int | None = None,
        dolby: int = 0,
        hires: int = 0,
        no_reprint: int = 0,
        is_only_self: int = 0,
        charging_pay: int = 0,
        up_selection_reply: int = 0,
        up_close_reply: int = 0,
        up_close_danmu: int = 0,
        extra_fields: str = "",
        upload_state: UploadStateStore | None = None,
        submit_interval: int = 0,
        cancel_event: threading.Event | None = None,
        profile_dir: str | Path | None = None,
        capture_dir: str | Path | None = None,
        **_ignored: Any,
    ) -> None:
        super().__init__(principal, data)
        self.user = user or {}
        self.user_cookie = Path(user_cookie)
        self.copyright = int(copyright)
        self.copyright_source = (copyright_source or data.get("url") or "").strip()
        self.tid = int(tid)
        self.tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        self.cover_path = Path(cover_path) if cover_path else None
        self.description = description
        for credit in credits or []:
            username = str(credit.get("username") or "").strip()
            if username:
                self.description = self.description.replace("@credit", f"@{username}", 1)
        self.dynamic = dynamic
        self.dtime = dtime
        self.flags = {
            "杜比音效": bool(dolby),
            "Hi-Res无损音质": bool(hires),
            "禁止转载": bool(no_reprint),
            "仅自己可见": bool(is_only_self),
            "开启充电面板": bool(charging_pay),
            "精选评论": bool(up_selection_reply),
            "关闭评论": bool(up_close_reply),
            "关闭弹幕": bool(up_close_danmu),
        }
        self.extra_fields = extra_fields.strip()
        self.upload_state = upload_state
        self.submit_interval = max(0, int(submit_interval))
        self.cancel_event = cancel_event
        self.account_key = account_key_for(self.user_cookie, self.user)
        suffix = re.sub(r"[^a-zA-Z0-9_.-]", "_", self.account_key)[-80:]
        self.profile_dir = Path(profile_dir or Path("cache") / "playwright" / suffix)
        self.capture_dir = Path(capture_dir or self.profile_dir.parent / "captures")

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise UploadCancelledError("Browser upload cancelled during application shutdown")

    def upload(self, file_list: list[UploadBase.FileInfo]) -> dict[str, Any]:
        if not file_list:
            raise ValueError("No files selected for browser upload")
        if self.extra_fields:
            raise ValueError("Browser upload does not support extra_fields")
        if self.dtime:
            raise ValueError("Browser upload does not support scheduled publishing yet")
        resolved_files = [Path(item.video).resolve() for item in file_list]

        def callback() -> dict[str, Any]:
            return self._run_browser(resolved_files)

        if self.upload_state is not None:
            return self.upload_state.submit(callback, self.submit_interval)
        with account_lock_for(self.account_key):
            return callback()

    def _run_browser(self, files: list[Path]) -> dict[str, Any]:
        ensure_headed_environment()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser upload requires Playwright. Run 'uv sync' and "
                "'uv run playwright install --with-deps chromium'."
            ) from exc

        payload, cookies = load_browser_cookies(self.user_cookie)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        submitted = False
        context = None
        playwright = None
        try:
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                no_viewport=True,
                locale="zh-CN",
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
            context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            submission: dict[str, Any] = {}

            def capture_submission(response: Any) -> None:
                if urlsplit(response.url).path != SUBMIT_PATH or response.request.method != "POST":
                    return
                try:
                    submission.update(response.json())
                except Exception:
                    logger.exception("无法解析浏览器投稿响应")

            page.on("response", capture_submission)
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=90_000)
            page.locator(VIDEO_INPUT).wait_for(state="attached", timeout=60_000)
            self._check_cancelled()
            logger.info(
                "浏览器高速上传开始 files=%d bytes=%d",
                len(files),
                sum(path.stat().st_size for path in files),
            )
            page.locator(VIDEO_INPUT).set_input_files([str(path) for path in files])
            submit_button = self._wait_for_form(page, files)
            self._fill_form(page)
            self._check_cancelled()
            logger.info("浏览器上传完成，准备单次提交")
            submitted = True
            submit_button.click(timeout=30_000)
            result = self._wait_for_submission(page, submission)
            logger.info(
                "浏览器投稿成功 code=%s aid=%s bvid=%s",
                result.get("code"),
                (result.get("data") or {}).get("aid"),
                (result.get("data") or {}).get("bvid"),
            )
            return result
        except UploadRejectedError:
            raise
        except UploadOutcomeUnknownError:
            self._capture_failure(context, "outcome-unknown")
            raise
        except UploadCancelledError:
            raise
        except (ValueError, RuntimeError):
            self._capture_failure(context, "before-submit")
            raise
        except PlaywrightTimeoutError as exc:
            self._capture_failure(context, "timeout")
            if submitted:
                raise UploadOutcomeUnknownError("Browser submission timed out after the submit click") from exc
            raise TransientUploadError(f"Browser upload timed out before submission: {exc}") from exc
        except Exception as exc:
            self._capture_failure(context, "error")
            if submitted:
                raise UploadOutcomeUnknownError("Browser closed or failed after the submit click") from exc
            raise RuntimeError(f"Browser upload failed: {exc}") from exc
        finally:
            if context is not None:
                try:
                    save_browser_cookies(self.user_cookie, payload, context.cookies())
                except Exception:
                    logger.exception("保存浏览器 Cookie 失败")
                try:
                    context.close()
                except Exception:
                    logger.exception("关闭浏览器上传上下文失败")
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    logger.exception("停止浏览器上传驱动失败")

    def _wait_for_form(self, page: Any, files: list[Path]) -> Any:
        deadline = time.monotonic() + 12 * 60 * 60
        pending_snapshot_at = time.monotonic() + 15
        pending_snapshot_saved = False
        last_progress = ""
        while time.monotonic() < deadline:
            self._check_cancelled()
            self._dismiss_known_overlays(page)
            body = page.locator("body").inner_text(timeout=10_000)
            progress = " ".join(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*[KM]B/s", body)))
            if progress and progress != last_progress:
                logger.info("浏览器上传进度 %s", progress)
                last_progress = progress
            if not last_progress and not pending_snapshot_saved and time.monotonic() >= pending_snapshot_at:
                self._capture_page(page, "pending")
                pending_snapshot_saved = True
            candidates = page.get_by_text(re.compile(r"^(立即投稿|提交稿件)$"))
            button = _visible(candidates)
            if button is not None:
                disabled = button.get_attribute("disabled") is not None
                aria_disabled = button.get_attribute("aria-disabled") == "true"
                if not disabled and not aria_disabled and upload_finished(body):
                    return button
            page.wait_for_timeout(1_000)
        raise RuntimeError(f"Browser upload did not finish for {len(files)} files")

    def _dismiss_known_overlays(self, page: Any) -> None:
        acknowledgement = _visible(page.get_by_text("知道了", exact=True))
        if acknowledgement is not None:
            acknowledgement.click()
            page.wait_for_timeout(300)

    def _fill_form(self, page: Any) -> None:
        title = _field_near_label(page, ("稿件标题",), "input")
        if title is None:
            title = _visible(page.get_by_placeholder(re.compile("标题")))
        if title is None:
            raise RuntimeError("Bilibili title input was not found")
        title.fill(str(self.data.get("format_title") or self.principal)[:80])

        self._select_copyright(page)
        self._fill_remaining_form(page)

    def _select_copyright(self, page: Any) -> None:
        copyright_text = "自制" if self.copyright == 1 else "转载"
        copyright_option = _visible(page.get_by_text(copyright_text, exact=True))
        if copyright_option is not None:
            copyright_option.click()
        else:
            statement = _visible(page.get_by_placeholder("请选择符合您视频内容的创作声明"))
            if statement is None:
                raise RuntimeError(f"Bilibili copyright option was not found: {copyright_text}")
            statement.click()
            page.wait_for_timeout(300)
            statement_text = "内容无需标注" if self.copyright == 1 else "内容为转载"
            statement_option = _visible(page.get_by_text(statement_text, exact=True))
            if statement_option is None:
                raise RuntimeError(f"Bilibili creation statement was not found: {statement_text}")
            statement_option.click()
        if self.copyright == 2:
            source = _field_near_label(page, ("转载来源", "来源"), "input")
            if source is None:
                source = _visible(page.get_by_placeholder(re.compile("转载|来源")))
            if source is None or not self.copyright_source:
                raise RuntimeError("Browser upload requires a copyright source for reposted videos")
            source.fill(self.copyright_source)

    def _fill_remaining_form(self, page: Any) -> None:
        self._select_category(page)
        self._fill_tags(page)

        description = _field_near_label(page, ("简介",), "textarea")
        if description is None:
            description = _visible(
                page.locator('.ql-editor[data-placeholder^="填写更全面的相关信息"]')
            )
        if description is not None:
            description.fill(self.description[:2000])
        dynamic = _field_near_label(page, ("粉丝动态", "动态"), "textarea") or _field_near_label(
            page, ("粉丝动态", "动态"), "input"
        )
        if dynamic is None:
            dynamic = _visible(page.locator('.ql-editor[data-placeholder^="有趣的动态描述"]'))
        if dynamic is not None and self.dynamic:
            dynamic.fill(self.dynamic)
        if self.cover_path:
            cover = page.locator('input[type="file"][accept*=".jpg"], input[type="file"][accept*="image"]')
            if not cover.count():
                raise RuntimeError("Bilibili cover input was not found")
            cover.last.set_input_files(str(self.cover_path.resolve()))
        if any(self.flags.values()):
            self._ensure_more_settings(page)
        for label, enabled in self.flags.items():
            if not enabled:
                continue
            option = _visible(page.locator("label.bcc-checkbox").filter(has_text=label))
            if option is None:
                raise RuntimeError(f"Bilibili submission option was not found: {label}")
            checkbox = option.locator('input[type="checkbox"]')
            if not checkbox.count() or not checkbox.is_checked():
                option.click()

    def _fill_tags(self, page: Any) -> None:
        tag_container = _visible(page.locator("#tag-container"))
        if tag_container is not None:
            tag_input = _visible(tag_container.get_by_placeholder("按回车键Enter创建标签"))
            if tag_input is None:
                raise RuntimeError("Bilibili tag input was not found")
            if self.tags:
                remove_buttons = tag_container.locator(".label-item-v2-container .close")
                while remove_buttons.count():
                    remove_buttons.first.click()
                    page.wait_for_timeout(100)
            for tag in self.tags:
                tag_input.fill(tag)
                page.wait_for_timeout(100)
                tag_input.press("Enter")
                chip = tag_container.locator(".label-item-v2-content").filter(
                    has_text=re.compile(rf"^{re.escape(tag)}$")
                )
                try:
                    chip.first.wait_for(state="visible", timeout=3_000)
                except Exception:
                    logger.warning("浏览器跳过无效标签 tag=%s", tag)
                    continue
                validating = page.get_by_text(re.compile("标签正在请求校验中"))
                if validating.count() and validating.first.is_visible():
                    validating.first.wait_for(state="hidden", timeout=10_000)
            if self.tags and not tag_container.locator(".label-item-v2-content").count():
                raise RuntimeError("Bilibili rejected every configured tag")
            return

        tag_input = _field_near_label(page, ("标签",), "input")
        if tag_input is None:
            tag_input = _visible(page.get_by_placeholder(re.compile("标签|Tag")))
        if tag_input is None:
            raise RuntimeError("Bilibili tag input was not found")
        for tag in self.tags:
            tag_input.fill(tag)
            tag_input.press("Enter")

    def _ensure_more_settings(self, page: Any) -> None:
        enabled_labels = [label for label, enabled in self.flags.items() if enabled]
        if any(
            _visible(page.locator("label.bcc-checkbox").filter(has_text=label)) is not None
            for label in enabled_labels
        ):
            return
        opener = _visible(
            page.locator("div.title > span.label").filter(has_text=re.compile(r"^更多设置"))
        )
        if opener is None:
            raise RuntimeError("Bilibili more settings control was not found")
        opener.click()
        page.wait_for_timeout(300)

    def _select_category(self, page: Any) -> None:
        human_type = _visible(page.locator(".video-human-type .select-controller"))
        if human_type is not None:
            category_name = HUMAN_TYPE_BY_TID.get(self.tid)
            if category_name is None:
                raise RuntimeError(f"Bilibili human category mapping is unavailable for tid: {self.tid}")
            human_type.click()
            page.wait_for_timeout(300)
            option = _visible(
                page.locator(f'.video-human-type .drop-list-v2-item[title="{category_name}"]')
            )
            if option is None:
                raise RuntimeError(f"Bilibili human category option was not found: {category_name}")
            option.click()
            return

        try:
            from biliup.integrations.uploaders.bili_web import BiliBili, Data

            with BiliBili(Data()) as bili:
                archive = bili.tid_archive(load_browser_cookies(self.user_cookie)[0])
            parent, child = category_names((archive.get("data") or {}).get("typelist") or [], self.tid)
        except Exception as exc:
            raise RuntimeError(f"Unable to resolve Bilibili category {self.tid}") from exc
        category = _field_near_label(page, ("分区",), "input")
        if category is None:
            category_label = _visible(page.get_by_text("分区", exact=True))
            if category_label is None:
                raise RuntimeError("Bilibili category field was not found")
            category_label.click()
        else:
            category.click()
        page.wait_for_timeout(300)
        parent_option = _visible(page.get_by_text(parent, exact=True))
        if parent_option is not None:
            parent_option.hover()
            page.wait_for_timeout(300)
        child_option = _visible(page.get_by_text(child, exact=True))
        if child_option is None:
            raise RuntimeError(f"Bilibili category option was not found: {parent}/{child}")
        child_option.click()

    def _wait_for_submission(self, page: Any, submission: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if submission:
                if submission.get("code") != 0:
                    raise UploadRejectedError(submission)
                data = submission.get("data") or {}
                if data.get("aid") or data.get("bvid"):
                    return dict(submission)
            page.wait_for_timeout(500)
        raise UploadOutcomeUnknownError("Bilibili did not return a confirmed submission result")

    def _capture_failure(self, context: Any, suffix: str) -> None:
        if context is None or not context.pages:
            return
        self._capture_page(context.pages[-1], suffix)

    def _capture_page(self, page: Any, suffix: str) -> None:
        target = self.capture_dir / f"browser-upload-{int(time.time())}-{suffix}.png"
        try:
            page.screenshot(path=str(target), full_page=False)
            logger.warning("浏览器上传页面截图 state=%s path=%s", suffix, target)
        except Exception:
            logger.exception("保存浏览器上传失败截图失败")
