from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from biliup.common.util import client, logger
from biliup.integrations.uploaders.bili_web import BiliBili, Data

TV_APP_KEY = "4409e2ce8ffd12b8"
TV_APP_SECRET = "59b43e04ad6965f34319062b478f83dd"
TV_QR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/63.0.3239.108",
    "Referer": "https://www.bilibili.com/",
}


def _signed_params(**values: Any) -> dict[str, Any]:
    # Bilibili signs the form fields in insertion order. For polling, auth_code
    # must precede local_id and ts (the auth_code request has no extra fields).
    params = {"appkey": TV_APP_KEY, **values, "local_id": "0", "ts": int(time.time())}
    query = urllib.parse.urlencode(params)
    params["sign"] = hashlib.md5(f"{query}{TV_APP_SECRET}".encode()).hexdigest()
    return params


def load_cookie(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


async def _post_qrcode(url: str, data: dict[str, Any], stage: str) -> httpx.Response:
    try:
        response = await client.post(
            url,
            data=data,
            timeout=10,
            headers=TV_QR_HEADERS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Bilibili QR %s request failed error_type=%s",
            stage,
            type(exc).__name__,
        )
        raise

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Bilibili QR %s request failed status=%d",
            stage,
            exc.response.status_code,
        )
        raise
    return response


async def get_qrcode() -> dict[str, Any]:
    response = await _post_qrcode(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        _signed_params(),
        "auth_code",
    )
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError("Bilibili did not return a QR code")
    return result


async def login_by_qrcode(value: dict[str, Any]) -> dict[str, Any]:
    try:
        auth_code = value["data"]["auth_code"]
    except (KeyError, TypeError) as exc:
        raise ValueError("QR response does not contain auth_code") from exc
    params = _signed_params(auth_code=auth_code)
    for _ in range(120):
        await asyncio.sleep(1)
        response = await _post_qrcode(
            "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
            params,
            "poll",
        )
        result = response.json()
        code = result.get("code")
        if code == 0:
            return result
        if code in (86039, 86090):
            continue
        if code == 86038:
            raise TimeoutError("Bilibili QR code expired, please scan a new code")
        if isinstance(code, int):
            logger.warning("Bilibili QR poll rejected code=%d", code)
            raise RuntimeError(f"Bilibili QR poll rejected: code={code}")
        logger.warning("Bilibili QR poll returned invalid status")
        raise RuntimeError("Bilibili QR poll returned an invalid status")
    raise TimeoutError("Bilibili QR code login timed out")


async def archive_pre(cookie_file: str | Path) -> dict[str, Any]:
    cookies = load_cookie(cookie_file)
    return await asyncio.to_thread(BiliBili(Data()).tid_archive, cookies)


async def my_info(cookie_file: str | Path) -> dict[str, Any]:
    cookies = load_cookie(cookie_file)
    return await asyncio.to_thread(BiliBili(Data()).myinfo, cookies)


async def my_info_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(BiliBili(Data()).myinfo, payload)
