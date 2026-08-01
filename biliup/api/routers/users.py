from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import Configuration

from ..dependencies import get_session
from ..schemas import UserInput, orm_dict

router = APIRouter()


@router.get("/v1/users")
def list_users(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Configuration).where(Configuration.key == "bilibili-cookies")).all()
    return [{"id": row.id, "name": row.value, "value": row.value, "platform": row.key} for row in rows]


@router.post("/v1/users")
def add_user(payload: UserInput, session: Session = Depends(get_session)) -> dict:
    if payload.key != "bilibili-cookies":
        raise HTTPException(422, "unsupported user platform")
    row = Configuration(key=payload.key, value=payload.value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return orm_dict(row)


@router.delete("/v1/users/{user_id}", status_code=204)
def delete_user(user_id: int, session: Session = Depends(get_session)) -> Response:
    row = session.get(Configuration, user_id)
    if not row or row.key != "bilibili-cookies":
        raise HTTPException(404, "user not found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)
