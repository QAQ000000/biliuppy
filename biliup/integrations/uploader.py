from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from biliup.core import AppPaths
from biliup.database.session import Database
from biliup.engine.upload import UploadBase
from biliup.integrations.upload_errors import is_transient_upload_error
from biliup.integrations.upload_state import (
    SubmitDelayError,
    UploadResult,
    UploadStateStore,
    account_key_for,
)
from biliup.integrations.uploaders.bili_web import BiliWeb

logger = logging.getLogger("biliup.uploader")
_upload_executor: ThreadPoolExecutor | None = None
_upload_executor_guard = threading.Lock()
_IDEMPOTENCY_PARAM_KEYS = (
    "uploader",
    "template_name",
    "title",
    "source_url",
    "submit_api",
    "copyright",
    "copyright_source",
    "dtime",
    "dynamic",
    "lines",
    "threads",
    "tid",
    "tags",
    "cover_path",
    "description",
    "credits",
    "dolby",
    "hires",
    "no_reprint",
    "is_only_self",
    "charging_pay",
    "up_selection_reply",
    "up_close_reply",
    "up_close_danmu",
    "extra_fields",
)


def _get_upload_executor() -> ThreadPoolExecutor:
    global _upload_executor
    with _upload_executor_guard:
        if _upload_executor is None:
            workers = min(32, (os.cpu_count() or 1) + 4)
            _upload_executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="biliup-upload")
        return _upload_executor


async def shutdown_upload_executor() -> None:
    global _upload_executor
    with _upload_executor_guard:
        executor = _upload_executor
        _upload_executor = None
    if executor is not None:
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)


def _resolve_files(files: list[str], paths: AppPaths) -> list[Path]:
    resolved: list[Path] = []
    for value in files:
        candidate = Path(value).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else (paths.downloads / candidate).resolve()
        if paths.downloads not in candidate.parents or not candidate.is_file():
            raise ValueError(f"Upload file is outside the downloads directory: {value}")
        resolved.append(candidate)
    return resolved


def upload_idempotency_key(files: list[str], params: dict[str, Any], paths: AppPaths) -> str:
    resolved = _resolve_files(files, paths)
    if not resolved:
        raise ValueError("No files selected")
    cookie_path = paths.resolve_user_path(params.get("user_cookie") or "cookies.json")
    account_key = account_key_for(cookie_path, params.get("user") or {})
    payload = {
        "account": account_key,
        "files": [
            {
                "path": str(path).casefold(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            for path in resolved
            for stat in (path.stat(),)
        ],
        "params": {key: params.get(key) for key in _IDEMPOTENCY_PARAM_KEYS},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _upload_sync(
    files: list[Path],
    params: dict[str, Any],
    paths: AppPaths,
    database: Database | None = None,
) -> UploadResult | None:
    database = database or params.get("_database")
    uploader_name = params.get("uploader") or "bili_web"
    if uploader_name == "Noop":
        return UploadResult(aid=None, bvid=None, account_key="noop", cookie_path="")
    if uploader_name not in {"bili_web", "bili_web_sync", "bilibili"}:
        raise ValueError(f"Unknown uploader: {uploader_name}")
    cookie_value = params.get("user_cookie") or "cookies.json"
    cookie_path = paths.resolve_user_path(cookie_value)
    user = params.get("user") or {}
    account_key = account_key_for(cookie_path, user)
    upload_state = UploadStateStore(database, account_key) if database is not None else None
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
        user=user,
        user_cookie=str(cookie_path),
        submit_api=params.get("submit_api") or "web",
        copyright=params.get("copyright") or 2,
        dtime=params.get("dtime"),
        dynamic=params.get("dynamic") or "",
        lines=params.get("lines") or "AUTO",
        threads=min(8, max(1, int(params.get("threads") or 3))),
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
        upload_state=upload_state,
        submit_interval=max(0, int(params.get("submit_interval") or 0)),
        excluded_upload_lines=params.setdefault("_excluded_upload_lines", []),
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
    response = uploader.upload(file_list)
    if not isinstance(response, dict):
        return UploadResult(
            aid=None,
            bvid=None,
            account_key=account_key,
            cookie_path=str(cookie_path),
        )
    result_data = response.get("data") or {}
    aid = result_data.get("aid") or response.get("aid")
    bvid = result_data.get("bvid") or response.get("bvid")
    return UploadResult(
        aid=int(aid) if aid else None,
        bvid=str(bvid) if bvid else None,
        account_key=account_key,
        cookie_path=str(cookie_path),
    )


async def upload_files(
    files: list[str],
    params: dict[str, Any],
    paths: AppPaths | None = None,
    database: Database | None = None,
    max_attempts: int = 1,
) -> UploadResult | None:
    app_paths = paths or AppPaths.discover().ensure()
    resolved = _resolve_files(files, app_paths)
    if not resolved:
        raise ValueError("No files selected")
    transient_failures = 0
    max_attempts = max(1, int(max_attempts))
    while True:
        executor_future = _get_upload_executor().submit(
            _upload_sync,
            resolved,
            params,
            app_paths,
            database,
        )
        upload_future = asyncio.wrap_future(executor_future)
        try:
            return await asyncio.shield(upload_future)
        except SubmitDelayError as exc:
            delay = max(0.05, exc.retry_after)
            logger.info("等待账号投稿间隔 %.1f 秒，已上传分P将在稍后复用", delay)
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if executor_future.cancel():
                logger.info("已取消尚未开始的排队上传任务")
                raise
            logger.warning("上传任务已收到取消请求，等待当前同步上传步骤安全结束")
            while True:
                try:
                    return await asyncio.shield(upload_future)
                except asyncio.CancelledError:
                    continue
        except Exception as exc:
            transient_failures += 1
            if transient_failures >= max_attempts or not is_transient_upload_error(exc):
                raise
            delay = min(2**transient_failures, 30)
            logger.warning(
                "上传线路发生暂时故障，%s 秒后切换线路重试 %s/%s：%s",
                delay,
                transient_failures + 1,
                max_attempts,
                exc,
            )
            await asyncio.sleep(delay)
