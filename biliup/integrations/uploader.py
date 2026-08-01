from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from biliup.core import AppPaths
from biliup.engine.upload import UploadBase
from biliup.integrations.uploaders.bili_web import BiliWeb

logger = logging.getLogger("biliup.uploader")


def _resolve_files(files: list[str], paths: AppPaths) -> list[Path]:
    resolved: list[Path] = []
    for value in files:
        candidate = Path(value).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else (paths.downloads / candidate).resolve()
        if paths.downloads not in candidate.parents or not candidate.is_file():
            raise ValueError(f"Upload file is outside the downloads directory: {value}")
        resolved.append(candidate)
    return resolved


def _upload_sync(files: list[Path], params: dict[str, Any], paths: AppPaths) -> None:
    uploader_name = params.get("uploader") or "bili_web"
    if uploader_name == "Noop":
        return
    if uploader_name not in {"bili_web", "bili_web_sync", "bilibili"}:
        raise ValueError(f"Unknown uploader: {uploader_name}")
    cookie_value = params.get("user_cookie") or "cookies.json"
    cookie_path = paths.resolve_user_path(cookie_value)
    cover_path = params.get("cover_path")
    if cover_path:
        cover_path = str(paths.resolve_user_path(cover_path))
    data = {
        "name": params.get("template_name") or "manual-upload",
        "format_title": params.get("title") or files[0].stem,
        "url": params.get("source_url") or params.get("copyright_source") or "",
    }
    common_options = dict(
        principal=data["name"],
        data=data,
        user=params.get("user") or {},
        user_cookie=str(cookie_path),
        submit_api=params.get("submit_api") or "web",
        copyright=params.get("copyright") or 2,
        dtime=params.get("dtime"),
        dynamic=params.get("dynamic") or "",
        lines=params.get("lines") or "AUTO",
        threads=max(1, int(params.get("threads") or 3)),
        tid=params.get("tid") or 122,
        tags=params.get("tags") or [],
        cover_path=cover_path,
        description=params.get("description") or "",
        credits=params.get("credits") or [],
        dolby=params.get("dolby") or 0,
        hires=params.get("hires") or 0,
        no_reprint=params.get("no_reprint") or 0,
        is_only_self=params.get("is_only_self") or 0,
        charging_pay=params.get("charging_pay") or 0,
        up_selection_reply=params.get("up_selection_reply") or 0,
        up_close_reply=params.get("up_close_reply") or 0,
        up_close_danmu=params.get("up_close_danmu") or 0,
        copyright_source=params.get("copyright_source") or None,
        extra_fields=params.get("extra_fields") or "",
    )
    file_list = [UploadBase.FileInfo(str(path), None) for path in files]
    if uploader_name == "bilibili":
        from biliup.integrations.uploaders.bili_chrome import BiliChrome

        uploader = BiliChrome(principal=data["name"], data=data)
    else:
        if uploader_name == "bili_web_sync":
            logger.warning(
                "Uploader bili_web_sync uses the file-based bili_web adapter; live streaming upload is not available"
            )
        uploader = BiliWeb(**common_options)
    uploader.upload(file_list)


async def upload_files(files: list[str], params: dict[str, Any], paths: AppPaths | None = None) -> None:
    app_paths = paths or AppPaths.discover().ensure()
    resolved = _resolve_files(files, app_paths)
    if not resolved:
        raise ValueError("No files selected")
    await asyncio.to_thread(_upload_sync, resolved, params, app_paths)
