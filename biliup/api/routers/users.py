from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import Configuration
from biliup.integrations import bilibili as bili_service

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import UserInput, orm_dict

router = APIRouter()
COOKIE_KEY = "bilibili-cookies"


def _login_data(result: dict) -> tuple[dict, str]:
    if not isinstance(result, dict):
        raise HTTPException(422, "Bilibili 登录结果无效，请重新扫码")
    data = result.get("data", result)
    if not isinstance(data, dict):
        raise HTTPException(422, "Bilibili 登录结果无效，请重新扫码")
    token_info = data.get("token_info")
    mid = (token_info.get("mid") if isinstance(token_info, dict) else None) or data.get("mid")
    if not mid:
        raise HTTPException(422, "登录结果未包含账号 ID，请重新扫码")
    cookie_info = data.get("cookie_info")
    cookies = cookie_info.get("cookies", []) if isinstance(cookie_info, dict) else []
    if not isinstance(cookies, list):
        raise HTTPException(422, "登录结果 Cookie 格式无效，请重新扫码")
    cookie_values = {
        str(cookie.get("name")): str(cookie.get("value") or "")
        for cookie in cookies
        if isinstance(cookie, dict)
    }
    if not cookie_values.get("SESSDATA") or not cookie_values.get("DedeUserID"):
        raise HTTPException(422, "登录结果缺少必要的 Bilibili Cookie，请重新扫码")
    if cookie_values["DedeUserID"] != str(mid):
        raise HTTPException(422, "登录结果账号信息不一致，请重新扫码")
    return data, str(mid)


def _existing_mid(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if isinstance(value, dict):
        cookie_info = value.get("cookie_info")
        cookies = cookie_info.get("cookies", []) if isinstance(cookie_info, dict) else value.get("cookies", [])
        if isinstance(cookies, list):
            for cookie in cookies:
                if isinstance(cookie, dict) and cookie.get("name") == "DedeUserID" and cookie.get("value"):
                    return str(cookie["value"])
        direct_mid = value.get("DedeUserID")
        if direct_mid:
            return str(direct_mid)
        token_info = value.get("token_info")
        direct_mid = value.get("mid") or (token_info.get("mid") if isinstance(token_info, dict) else None)
        if direct_mid:
            return str(direct_mid)
    return path.stem if path.stem.isdecimal() else None


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.get("/v1/users")
def list_users(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Configuration).where(Configuration.key == COOKIE_KEY)).all()
    return [{"id": row.id, "name": row.value, "value": row.value, "platform": row.key} for row in rows]


@router.post("/v1/users")
def add_user(payload: UserInput, session: Session = Depends(get_session)) -> dict:
    if payload.key != COOKIE_KEY:
        raise HTTPException(422, "unsupported user platform")
    row = Configuration(key=payload.key, value=payload.value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return orm_dict(row)


@router.delete("/v1/users/{user_id}", status_code=204)
def delete_user(user_id: int, session: Session = Depends(get_session)) -> Response:
    row = session.get(Configuration, user_id)
    if not row or row.key != COOKIE_KEY:
        raise HTTPException(404, "user not found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post("/v1/users/{user_id}/refresh-cookie")
async def refresh_user_cookie(
    user_id: int,
    payload: dict,
    session: Session = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> dict:
    row = session.get(Configuration, user_id)
    if not row or row.key != COOKIE_KEY:
        raise HTTPException(404, "user not found")
    cookie_path_value = row.value
    session.close()

    try:
        result = await bili_service.login_by_qrcode(payload)
    except Exception as exc:
        raise HTTPException(502, "Bilibili 扫码登录失败，请稍后重试") from exc
    data, mid = _login_data(result)
    target = context.paths.resolve_user_path(cookie_path_value)
    old_mid = _existing_mid(target)
    if old_mid and old_mid != mid:
        raise HTTPException(409, "扫码账号与当前账号不一致，原 Cookie 未修改")

    try:
        profile = await bili_service.my_info_from_payload(data)
    except Exception as exc:
        raise HTTPException(502, "无法验证新 Cookie，原 Cookie 未修改") from exc
    profile_data = profile.get("data") if isinstance(profile, dict) else None
    profile_mid = profile_data.get("mid") if isinstance(profile_data, dict) else None
    if not isinstance(profile, dict) or profile.get("code") != 0 or str(profile_mid) != mid:
        raise HTTPException(422, "新 Cookie 验证失败或账号不匹配，原 Cookie 未修改")

    try:
        await asyncio.to_thread(_atomic_write_json, target, data)
    except OSError as exc:
        raise HTTPException(500, "新 Cookie 保存失败，原 Cookie 未修改") from exc
    return {"filename": str(target), "mid": mid}
