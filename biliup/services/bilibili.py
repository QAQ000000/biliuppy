from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

from biliup.common.util import client
from biliup.plugins.bili_webup import BiliBili, Data

TV_APP_KEY = "4409e2ce8ffd12b8"
TV_APP_SECRET = "59b43e04ad6965f34319062b478f83dd"


def _signed_params(**values: Any) -> dict[str, Any]:
    params = {"appkey": TV_APP_KEY, "local_id": "0", "ts": int(time.time()), **values}
    query = urllib.parse.urlencode(params)
    params["sign"] = hashlib.md5(f"{query}{TV_APP_SECRET}".encode()).hexdigest()
    return params


def load_cookie(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


async def get_qrcode() -> dict[str, Any]:
    response = await client.post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        data=_signed_params(),
        timeout=10,
    )
    response.raise_for_status()
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
        response = await client.post(
            "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
            data=params,
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("code") == 0:
            return result
    raise TimeoutError("Bilibili QR code login timed out")


async def archive_pre(cookie_file: str | Path) -> dict[str, Any]:
    cookies = load_cookie(cookie_file)
    return await asyncio.to_thread(BiliBili(Data()).tid_archive, cookies)


async def my_info(cookie_file: str | Path) -> dict[str, Any]:
    cookies = load_cookie(cookie_file)
    return await asyncio.to_thread(BiliBili(Data()).myinfo, cookies)
