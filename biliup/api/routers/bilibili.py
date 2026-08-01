from __future__ import annotations

import asyncio
import json
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import Configuration
from biliup.integrations import bilibili as bili_service

from ..context import AppContext
from ..dependencies import get_context, get_session

router = APIRouter()
ALLOWED_IMAGE_DOMAINS = ("hdslb.com", "biliimg.com")


def _cookie_path(context: AppContext, value: str):
    return context.paths.resolve_user_path(value)


def _allowed_image_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in ALLOWED_IMAGE_DOMAINS
    )


@router.get("/v1/get_qrcode")
async def get_qrcode() -> dict:
    try:
        return await bili_service.get_qrcode()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/v1/login_by_qrcode")
async def login_by_qrcode(payload: dict, context: AppContext = Depends(get_context)) -> dict:
    try:
        result = await bili_service.login_by_qrcode(payload)
        data = result.get("data", result)
        mid = data.get("token_info", {}).get("mid") or data.get("mid")
        if not mid:
            raise RuntimeError("Bilibili login response did not contain a user id")
        target = context.paths.data / f"{mid}.json"
        await asyncio.to_thread(
            target.write_text,
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"filename": str(target)}
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/bili/archive/pre")
async def archive_pre(
    session: Session = Depends(get_session), context: AppContext = Depends(get_context)
) -> dict:
    users = session.scalars(select(Configuration).where(Configuration.key == "bilibili-cookies")).all()
    errors = []
    for user in users:
        try:
            return await bili_service.archive_pre(_cookie_path(context, user.value))
        except Exception as exc:
            errors.append(str(exc))
    raise HTTPException(404, "无可用 cookie 文件" + (f": {errors[-1]}" if errors else ""))


@router.get("/bili/space/myinfo")
async def my_info(user: str = Query(...), context: AppContext = Depends(get_context)) -> dict:
    try:
        return await bili_service.my_info(_cookie_path(context, user))
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/bili/proxy")
async def proxy(url: str = Query(...)) -> Response:
    if not _allowed_image_url(url):
        raise HTTPException(400, "only Bilibili image URLs are supported")
    async with httpx.AsyncClient(follow_redirects=False, timeout=20) as client:
        current = url
        for _ in range(5):
            response = await client.get(current)
            if not response.is_redirect:
                break
            current = urljoin(current, response.headers["location"])
            if not _allowed_image_url(current):
                raise HTTPException(400, "Bilibili image redirect left the allowed domains")
        else:
            raise HTTPException(502, "too many Bilibili image redirects")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )
